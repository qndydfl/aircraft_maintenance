$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot "venv\Scripts\python.exe"
$managePath = Join-Path $projectRoot "manage.py"
$logDirectory = Join-Path $projectRoot "logs"
$logPath = Join-Path $logDirectory "reindex-worker.log"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Project Python was not found: $pythonPath"
}

if (-not (Test-Path -LiteralPath $managePath)) {
    throw "Django manage.py was not found: $managePath"
}

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Location -LiteralPath $projectRoot

$startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -LiteralPath $logPath -Value "[$startedAt] Re-index worker starting."

try {
    $ErrorActionPreference = "Continue"
    & $pythonPath $managePath process_reindex_jobs --sleep 5 *>> $logPath
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
}
catch {
    $failedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $logPath -Value "[$failedAt] Worker failed: $($_.Exception.Message)"
    throw
}

if ($exitCode -ne 0) {
    throw "Re-index worker exited with code $exitCode."
}
