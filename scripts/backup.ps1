param(
    [Parameter(Mandatory = $true)]
    [string]$Destination,
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

if ($ProjectName -notmatch '^[a-z0-9][a-z0-9_-]+$') {
    throw "ProjectName contains unsupported characters"
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$composePath = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $ComposeFile))
$envPath = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $EnvFile))
$resolved = [System.IO.Path]::GetFullPath($Destination)

if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
    throw "Compose file does not exist"
}
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "Environment file does not exist"
}
if ($resolved -eq $repositoryRoot -or $resolved -eq [System.IO.Path]::GetPathRoot($resolved)) {
    throw "Destination must be a dedicated backup directory"
}
if (Test-Path -LiteralPath $resolved) {
    if (Get-ChildItem -LiteralPath $resolved -Force | Select-Object -First 1) {
        throw "Destination must be empty"
    }
} else {
    New-Item -ItemType Directory -Path $resolved | Out-Null
}

$objectsPath = Join-Path $resolved "minio"
New-Item -ItemType Directory -Path $objectsPath | Out-Null
$dumpPath = Join-Path $resolved "research.dump"
$manifestPath = Join-Path $resolved "manifest.json"
$completePath = Join-Path $resolved "backup.complete"
$compose = @("compose", "--env-file", $envPath, "-p", $ProjectName, "-f", $composePath)
$running = Get-DockerOutput -CommandArguments ($compose + @("ps", "--status", "running", "--services"))
$restartApi = ($running -split "`r?`n") -contains "api"
$restartWorker = ($running -split "`r?`n") -contains "worker"
$succeeded = $false

try {
    Invoke-DockerCommand -CommandArguments ($compose + @("stop", "api", "worker"))
    Invoke-DockerCommand -CommandArguments (
        $compose + @(
            "exec", "-T", "postgres", "pg_dump", "-U", "research", "-Fc",
            "-f", "/tmp/research-agent-backup.dump", "research"
        )
    )
    Invoke-DockerCommand -CommandArguments (
        $compose + @(
            "cp", "postgres:/tmp/research-agent-backup.dump", $dumpPath
        )
    )

    $network = "${ProjectName}_default"
    $mirrorScript = @'
mc alias set local http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null
mc mb --ignore-existing "local/$MINIO_BUCKET" >/dev/null
mc mirror --overwrite --remove "local/$MINIO_BUCKET" /backup/minio
'@
    Invoke-DockerCommand -CommandArguments @(
        "run", "--rm", "--network", $network, "--env-file", $envPath,
        "--mount", "type=bind,source=$resolved,destination=/backup",
        "--entrypoint", "/bin/sh", $MinioClientImage, "-c", $mirrorScript
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
    $countsJson = Get-DockerOutput -CommandArguments (
        $compose + @(
            "exec", "-T", "postgres", "psql", "-U", "research", "-d",
            "research", "-tA", "-c", $countQuery
        )
    )
    $counts = $countsJson | ConvertFrom-Json
    $schemaHead = Get-DockerOutput -CommandArguments (
        $compose + @(
            "exec", "-T", "postgres", "psql", "-U", "research", "-d",
            "research", "-tA", "-c", "SELECT version_num FROM alembic_version;"
        )
    )
    $objectCount = @(Get-ChildItem -LiteralPath $objectsPath -File -Recurse).Count
    $dumpHash = (Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifest = [ordered]@{
        format_version = 1
        created_at_utc = [DateTime]::UtcNow.ToString("o")
        project_name = $ProjectName
        schema_head = $schemaHead
        database_dump_sha256 = $dumpHash
        object_count = $objectCount
        counts = $counts
    }
    Set-Content -LiteralPath $manifestPath -Value ($manifest | ConvertTo-Json -Depth 5) -Encoding utf8
    Set-Content -LiteralPath $completePath -Value "complete" -Encoding ascii
    $succeeded = $true
} finally {
    if ($restartApi) {
        Invoke-DockerCommand -CommandArguments ($compose + @("start", "api"))
    }
    if ($restartWorker) {
        Invoke-DockerCommand -CommandArguments ($compose + @("start", "worker"))
    }
}

if (-not $succeeded) {
    throw "Backup did not complete"
}

Write-Output "Backup completed: $resolved"
Write-Output "Database and object counts are recorded in manifest.json; secrets are not included."
