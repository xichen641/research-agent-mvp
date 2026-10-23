param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$ImportReport = (Join-Path (Split-Path -Parent $PSScriptRoot) ".tmp/reid-import-report.json"),
    [string]$OutputDirectory,
    [string[]]$TaskId = @(),
    [ValidateSet("auto", "on", "off")][string]$RetrievalMode = "auto",
    [double]$TurnTimeoutSeconds = 600,
    [double]$ConversationTimeoutSeconds = 1800,
    [switch]$Docker,
    [string]$ProjectName = "research-agent-mvp",
    [string]$Image = "research-agent-mvp-api",
    [string]$ApiUrl = "http://127.0.0.1:8000",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$importPath = (Resolve-Path -LiteralPath $ImportReport).Path
$runSuffix = (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $resolvedRoot ".tmp/reid-baselines/$runSuffix" }
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $outputPath) { throw "Existing baseline results are preserved; use a new output directory." }
$outputParent = Split-Path -Parent $outputPath
[void](New-Item -ItemType Directory -Path $outputParent -Force)
$outputName = Split-Path -Leaf $outputPath
$identityPath = Join-Path $outputParent "runtime-$runSuffix.json"
$revision = & git -C $resolvedRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw "Cannot identify the repository revision." }
$identity = @{ git_revision = $revision.Trim(); environment = "local_docker" }
$previousPythonPath = $env:PYTHONPATH
try {
    if ($Docker) {
        $sourceFingerprintCode = @'
import hashlib
from pathlib import Path
root = Path('/app/src/research_agent')
state = hashlib.sha256()
for path in sorted(root.rglob('*.py')):
    state.update(path.relative_to(root).as_posix().encode())
    state.update(hashlib.sha256(path.read_bytes()).digest())
print(state.hexdigest())
'@
        foreach ($service in @("api", "worker")) {
            $containerName = "$ProjectName-$service-1"
            $containerImage = & docker inspect --format '{{.Image}}' $containerName
            if ($LASTEXITCODE -ne 0) { throw "$service container is unavailable." }
            $sourceFingerprint = & docker exec $containerName /app/.venv/bin/python -c $sourceFingerprintCode
            if ($LASTEXITCODE -ne 0) { throw "Cannot identify $service source." }
            $identity["${service}_image"] = $containerImage.Trim()
            $identity["${service}_source_sha256"] = $sourceFingerprint.Trim()
        }
        if ($identity["api_source_sha256"] -ne $identity["worker_source_sha256"]) {
            throw "API and Worker source differ; update both images before baseline execution."
        }
        $runnerImage = & docker image inspect --format '{{.Id}}' $Image
        if ($LASTEXITCODE -ne 0) { throw "Baseline runner image is unavailable." }
        $identity["runner_image"] = $runnerImage.Trim()
        $identity | ConvertTo-Json | Set-Content -LiteralPath $identityPath -Encoding utf8
        $evaluationPath = Join-Path $resolvedRoot "evaluation"
        $papersPath = Join-Path $resolvedRoot "datasets"
        $identityName = Split-Path -Leaf $identityPath
        $arguments = @(
            "run", "--rm", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--network", "${ProjectName}_default",
            "--mount", "type=bind,source=$evaluationPath,target=/workspace/evaluation,readonly",
            "--mount", "type=bind,source=$papersPath,target=/workspace/datasets,readonly",
            "--mount", "type=bind,source=$importPath,target=/workspace/import.json,readonly",
            "--mount", "type=bind,source=$outputParent,target=/workspace/runs",
            "--entrypoint", "/app/.venv/bin/python", $Image,
            "-X", "utf8", "-m", "research_agent.evaluation.baseline",
            "--dataset", "/workspace/evaluation/datasets/reid_research_tasks.jsonl",
            "--manifest", "/workspace/evaluation/corpora/reid-v1.json",
            "--root", "/workspace", "--import-report", "/workspace/import.json",
            "--output", "/workspace/runs/$outputName", "--api-url", "http://api:8000",
            "--runtime-identity", "/workspace/runs/$identityName"
        )
        $command = "docker"
    } else {
        $identity["environment"] = "local_http_client"
        $identity | ConvertTo-Json | Set-Content -LiteralPath $identityPath -Encoding utf8
        $sourcePath = Join-Path $resolvedRoot "backend/src"
        $env:PYTHONPATH = if ($previousPythonPath) { "$sourcePath$([IO.Path]::PathSeparator)$previousPythonPath" } else { $sourcePath }
        $arguments = @(
            "-X", "utf8", "-m", "research_agent.evaluation.baseline",
            "--dataset", (Join-Path $resolvedRoot "evaluation/datasets/reid_research_tasks.jsonl"),
            "--manifest", (Join-Path $resolvedRoot "evaluation/corpora/reid-v1.json"),
            "--root", $resolvedRoot, "--import-report", $importPath,
            "--output", $outputPath, "--api-url", $ApiUrl, "--runtime-identity", $identityPath
        )
        $command = $Python
    }
    $arguments += @("--retrieval-mode", $RetrievalMode, "--turn-timeout", "$TurnTimeoutSeconds", "--conversation-timeout", "$ConversationTimeoutSeconds")
    foreach ($task in $TaskId) { $arguments += @("--task-id", $task) }
    & $command @arguments
    $result = $LASTEXITCODE
    Write-Host "Baseline report: $outputPath/report.md"
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
exit $result
