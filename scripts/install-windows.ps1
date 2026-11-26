param(
    [string]$PythonCommand = ""
)

$ErrorActionPreference = "Stop"
$RootDir = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$Installer = Join-Path $RootDir "scripts\install_native.py"

function Get-PythonVersion($Command, $Arguments) {
    # An unavailable launcher version writes to stderr. PowerShell 5 can turn
    # redirected native stderr into an error when ErrorActionPreference is Stop.
    $ErrorActionPreference = "Continue"
    $PSNativeCommandUseErrorActionPreference = $false
    try {
        $output = & $Command @Arguments -c "import sys; print('%s.%s' % sys.version_info[:2])" 2>&1
        if ($LASTEXITCODE -eq 0) {
            return ($output | Select-Object -Last 1).ToString().Trim()
        }
    } catch {
        return $null
    }
    return $null
}

$candidates = @()
if ($PythonCommand) {
    $candidates += @{ Command = $PythonCommand; Arguments = @() }
} else {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += @{ Command = "py"; Arguments = @("-3.14") }
        $candidates += @{ Command = "py"; Arguments = @("-3.13") }
    }
    foreach ($command in @("python", "python3")) {
        if (Get-Command $command -ErrorAction SilentlyContinue) {
            $candidates += @{ Command = $command; Arguments = @() }
        }
    }
}

$found = @()
$selected = $null
foreach ($candidate in $candidates) {
    $version = Get-PythonVersion $candidate.Command $candidate.Arguments
    $label = (@($candidate.Command) + $candidate.Arguments) -join " "
    if ($version) {
        $found += "${label}: Python $version"
        if ($version -in @("3.13", "3.14")) {
            $selected = $candidate
            break
        }
    } else {
        $found += "${label}: unavailable"
    }
}

if (-not $selected) {
    $details = if ($found.Count) { $found -join "; " } else { "no Python commands found" }
    throw "Python 3.13 or 3.14 is required. Checked: $details. If Python is installed elsewhere, rerun with -PythonCommand followed by the path to python.exe."
}

Write-Host "Using $label (Python $version)."
$Python = $selected.Command
$PythonArgs = $selected.Arguments
& $Python @PythonArgs $Installer --requirements (Join-Path $RootDir "requirements.windows.lock")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
