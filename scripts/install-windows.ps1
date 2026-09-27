param(
    [string]$PythonCommand = ""
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$Installer = Join-Path $RootDir "scripts\install_native.py"

if ($PythonCommand) {
    $PythonArgs = @()
    $Python = $PythonCommand
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
    & $Python -3.14 -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $PythonArgs = @("-3.14") }
    else { $PythonArgs = @("-3.13") }
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
    $PythonArgs = @()
} else {
    throw "Python 3.13 or 3.14 is required. Install it from python.org and rerun this script."
}

& $Python @PythonArgs $Installer --requirements (Join-Path $RootDir "requirements.windows.lock")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
