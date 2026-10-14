param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$Manifest = (Join-Path (Split-Path -Parent $PSScriptRoot) "evaluation/corpora/reid-v1.json"),
    [string]$ApiUrl = "http://127.0.0.1:8000",
    [string]$OutputPath = (Join-Path (Split-Path -Parent $PSScriptRoot) ".tmp/reid-import-report.json"),
    [double]$WaitSeconds = 0,
    [double]$PollInterval = 2,
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
    $arguments = @(
        "-X", "utf8", "-m", "research_agent.evaluation.import_corpus",
        "--root", $Root, "--manifest", $Manifest,
        "--api-url", $ApiUrl, "--output", $OutputPath,
        "--wait-seconds", $WaitSeconds.ToString([Globalization.CultureInfo]::InvariantCulture),
        "--poll-interval", $PollInterval.ToString([Globalization.CultureInfo]::InvariantCulture)
    )
    & $Python @arguments
    $result = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
exit $result
