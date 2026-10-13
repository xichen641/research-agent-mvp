param(
    [Parameter(Mandatory = $true)]
    [string]$Source,
    [Parameter(Mandatory = $true)]
    [switch]$ConfirmDataLoss,
    [string]$ProjectName = "research-agent",
    [string]$ComposeFile = "infra/compose.yaml",
    [string]$EnvFile = ".env",
    [string]$MinioClientImage = "minio/mc@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-DockerCommand {
    param([Parameter(Mandatory = $true)][string[]]$CommandArguments)
    & docker @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code $LASTEXITCODE"
    }
}

function Get-DockerOutput {
    param([Parameter(Mandatory = $true)][string[]]$CommandArguments)
    $output = & docker @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code $LASTEXITCODE"
    }
    return (($output | Out-String).Trim())
}

if (-not $ConfirmDataLoss) {
    throw "Restore requires -ConfirmDataLoss"
}
if ($ProjectName -notmatch '^[a-z0-9][a-z0-9_-]+$') {
    throw "ProjectName contains unsupported characters"
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$composePath = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $ComposeFile))
$envPath = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $EnvFile))
$resolved = [System.IO.Path]::GetFullPath($Source)
$dumpPath = Join-Path $resolved "research.dump"
$objectsPath = Join-Path $resolved "minio"
$manifestPath = Join-Path $resolved "manifest.json"
$completePath = Join-Path $resolved "backup.complete"

foreach ($requiredFile in @($dumpPath, $manifestPath, $completePath)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Backup is incomplete: $([System.IO.Path]::GetFileName($requiredFile)) is missing"
    }
}
if (-not (Test-Path -LiteralPath $objectsPath -PathType Container)) {
    throw "Backup is incomplete: minio directory is missing"
}
if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
    throw "Compose file does not exist"
}
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "Environment file does not exist"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.format_version -ne 1) {
    throw "Unsupported backup format"
}
$actualHash = (Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $manifest.database_dump_sha256) {
    throw "Database dump checksum does not match the manifest"
}

$compose = @("compose", "--env-file", $envPath, "-p", $ProjectName, "-f", $composePath)
Invoke-DockerCommand -CommandArguments ($compose + @("up", "-d", "postgres", "redis", "minio"))
Invoke-DockerCommand -CommandArguments ($compose + @("stop", "api", "worker"))
$restored = $false

try {
    Invoke-DockerCommand -CommandArguments (
        $compose + @("cp", $dumpPath, "postgres:/tmp/research-agent-restore.dump")
    )
    Invoke-DockerCommand -CommandArguments (
        $compose + @("exec", "-T", "postgres", "dropdb", "-U", "research", "--if-exists", "--force", "research")
    )
    Invoke-DockerCommand -CommandArguments (
        $compose + @("exec", "-T", "postgres", "createdb", "-U", "research", "research")
    )
    Invoke-DockerCommand -CommandArguments (
        $compose + @("exec", "-T", "postgres", "pg_restore", "-U", "research", "-d", "research", "/tmp/research-agent-restore.dump")
    )

    $network = "${ProjectName}_default"
    $mirrorScript = @'
mc alias set local http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null
mc mb --ignore-existing "local/$MINIO_BUCKET" >/dev/null
mc mirror --overwrite --remove /backup/minio "local/$MINIO_BUCKET"
'@
    Invoke-DockerCommand -CommandArguments @(
        "run", "--rm", "--network", $network, "--env-file", $envPath,
        "--mount", "type=bind,source=$resolved,destination=/backup,readonly",
        "--entrypoint", "/bin/sh", $MinioClientImage, "-c", $mirrorScript
    )
    Invoke-DockerCommand -CommandArguments (
        $compose + @("run", "--rm", "--no-deps", "migrate")
    )

    $countQuery = @'
SELECT json_build_object(
  'documents', (SELECT count(*) FROM document),
  'versions', (SELECT count(*) FROM document_version),
  'chunks', (SELECT count(*) FROM document_chunk),
  'chat_sessions', (SELECT count(*) FROM chat_session),
  'chat_messages', (SELECT count(*) FROM chat_message),
  'chat_runs', (SELECT count(*) FROM chat_run),
  'chat_memories', (SELECT count(*) FROM chat_memory),
  'notes', (SELECT count(*) FROM paper_note),
  'note_revisions', (SELECT count(*) FROM note_revision),
  'external_sources', (SELECT count(*) FROM external_paper_source)
);
'@
    $actualCountsJson = Get-DockerOutput -CommandArguments (
        $compose + @(
            "exec", "-T", "postgres", "psql", "-U", "research", "-d",
            "research", "-tA", "-c", $countQuery
        )
    )
    $actualCounts = $actualCountsJson | ConvertFrom-Json
    $expectedCompact = $manifest.counts | ConvertTo-Json -Compress
    $actualCompact = $actualCounts | ConvertTo-Json -Compress
    if ($actualCompact -ne $expectedCompact) {
        throw "Restored durable counts do not match the backup manifest"
    }
    $actualObjectCount = Get-DockerOutput -CommandArguments @(
        "run", "--rm", "--network", $network, "--env-file", $envPath,
        "--entrypoint", "/bin/sh", $MinioClientImage, "-c",
        'mc alias set local http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null; mc find "local/$MINIO_BUCKET" | wc -l'
    )
    if ([int]$actualObjectCount -ne [int]$manifest.object_count) {
        throw "Restored object count does not match the backup manifest"
    }
    $restored = $true
} finally {
    if ($restored) {
        Invoke-DockerCommand -CommandArguments ($compose + @("start", "api", "worker"))
    } else {
        Write-Warning "Restore failed; API and worker remain stopped to protect the partial state."
    }
}

Write-Output "Restore completed and durable counts verified: $resolved"
