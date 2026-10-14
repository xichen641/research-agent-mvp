param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$Dataset = (Join-Path (Split-Path -Parent $PSScriptRoot) "evaluation/datasets/reid_research_tasks.jsonl"),
    [string]$Manifest = (Join-Path (Split-Path -Parent $PSScriptRoot) "evaluation/corpora/reid-v1.json"),
    [switch]$StructureOnly,
    [switch]$Docker,
    [string]$Image = "research-agent-mvp-api",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$previousPythonPath = $env:PYTHONPATH
try {
    if ($Docker) {
        $datasetPath = (Resolve-Path -LiteralPath $Dataset).Path
        $manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
        $dockerArguments = @(
            "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--mount", "type=bind,source=$datasetPath,target=/workspace/tasks.jsonl,readonly",
            "--mount", "type=bind,source=$manifestPath,target=/workspace/corpus.json,readonly"
        )
        if (-not $StructureOnly) {
            $papersPath = (Resolve-Path -LiteralPath (Join-Path $Root "datasets")).Path
            $dockerArguments += @(
                "--mount", "type=bind,source=$papersPath,target=/workspace/datasets,readonly"
            )
        }
        $dockerArguments += @(
            "--entrypoint", "/app/.venv/bin/python", $Image,
            "-X", "utf8", "-m", "research_agent.evaluation.research_tasks",
            "--dataset", "/workspace/tasks.jsonl", "validate",
            "--manifest", "/workspace/corpus.json", "--root", "/workspace"
        )
        if ($StructureOnly) { $dockerArguments += "--structure-only" }
        & docker @dockerArguments
        exit $LASTEXITCODE
    }
    $sourcePath = Join-Path (Split-Path -Parent $PSScriptRoot) "backend/src"
    $env:PYTHONPATH = if ($previousPythonPath) {
        "$sourcePath$([IO.Path]::PathSeparator)$previousPythonPath"
    } else {
        $sourcePath
    }
    $arguments = @(
        "-X", "utf8", "-m", "research_agent.evaluation.research_tasks",
        "--dataset", $Dataset, "validate", "--manifest", $Manifest, "--root", $Root
    )
    if ($StructureOnly) { $arguments += "--structure-only" }
    & $Python @arguments
    $result = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
exit $result
