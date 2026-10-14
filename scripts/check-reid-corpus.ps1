param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$Manifest = (Join-Path (Split-Path -Parent $PSScriptRoot) "evaluation/corpora/reid-v1.json"),
    [string]$OutputPath,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$previousPythonPath = $env:PYTHONPATH
try {
    $sourcePath = Join-Path $repositoryRoot "backend/src"
    $env:PYTHONPATH = if ($previousPythonPath) {
        "$sourcePath$([IO.Path]::PathSeparator)$previousPythonPath"
    } else {
        $sourcePath
    }
    $arguments = @("-X", "utf8", "-m", "research_agent.evaluation.corpus", "--root", $Root, "--manifest", $Manifest)
    if ($OutputPath) {
        $arguments += @("--output", $OutputPath)
    }
    & $Python @arguments
    $result = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
exit $result
