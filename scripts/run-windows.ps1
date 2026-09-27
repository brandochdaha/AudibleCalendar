param(
    [string]$BindAddress = "",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$Python = Join-Path $RootDir ".venv\Scripts\python.exe"
$Runner = Join-Path $RootDir "scripts\run_native.py"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "No native installation found. Run scripts\install-windows.ps1 first."
}

$RunnerArguments = @()
if ($BindAddress) { $RunnerArguments += @("--host", $BindAddress) }
if ($Port) { $RunnerArguments += @("--port", "$Port") }
& $Python $Runner @RunnerArguments
exit $LASTEXITCODE
