# Launch all 9 bots locally on Windows, each in its own window, using the
# project venv on D:. Run from the project root:  ./scripts/run_all_local.ps1
# Add -DryRun to simulate without sending orders.
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "venvbots\Scripts\python.exe"
$extra = if ($DryRun) { "--dry-run" } else { "" }

$configs = Get-ChildItem (Join-Path $root "configs") -Filter *.yaml
foreach ($cfg in $configs) {
    $cmd = "& '$py' '$($root)\run_bot.py' --config '$($cfg.FullName)' $extra"
    Write-Host "Starting $($cfg.BaseName)..."
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd
}
Write-Host "All bots launched (one window each)."
