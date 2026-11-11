<#
Builds, cooks and stages the PyTestGame test game (testgame/PyTestGame) as a Shipping Win64 build, using the UE 4.25
installed engine. Run it from a drive-letter checkout (not a \\wsl.localhost path). About 1-2 minutes after the first run. Builds the project's editor module first (the cook needs it).

  powershell -ExecutionPolicy Bypass -File tools\package_testgame.ps1 -Engine C:\UE_4.25     (or set UE4_ENGINE)

Output: testgame\PyTestGame\Saved\StagedBuilds\WindowsNoEditor\PyTestGame\Binaries\Win64\PyTestGame-Win64-Shipping.exe
Refuses while the game is running (its files would be replaced).
#>
param(
    [string]$Engine = $env:UE4_ENGINE,
    [string]$Config = 'Shipping'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if ($root -like '\\*') { throw 'Run this from a drive-letter checkout (for example C:\dev\ue4ss-python), not a UNC path.' }
if (-not $Engine) { throw 'Pass -Engine <path to the installed UE 4.25> or set UE4_ENGINE.' }
$project = Join-Path $root 'testgame\PyTestGame\PyTestGame.uproject'
if (Get-Process -Name "PyTestGame-Win64-$Config" -ErrorAction SilentlyContinue) { throw "PyTestGame-Win64-$Config is running; close it before packaging." }

# The staged game has Mods\PythonScripting as a directory junction to dist\PythonScripting (see docs/TESTGAME.md). UAT cleans
# the staging folder recursively and that follows the junction and empties dist\, so remove the link itself first. It is made
# again by hand afterwards (mklink /J).
$junction = Join-Path $root 'testgame\PyTestGame\Saved\StagedBuilds\WindowsNoEditor\PyTestGame\Binaries\Win64\Mods\PythonScripting'
if (Test-Path -LiteralPath $junction) {
    $item = Get-Item -LiteralPath $junction -Force
    if ($item.LinkType -eq 'Junction' -or $item.LinkType -eq 'SymbolicLink') { [System.IO.Directory]::Delete($junction) }
    else { throw "$junction is a real folder, not a link; refusing to continue (UAT would delete it)" }
}

# The cook runs the editor, which needs this project's editor module (UE4Editor-PyTestGame.dll). UAT is told not to compile the
# editor (an installed engine cannot), so the module is built here; later runs are incremental and take seconds.
$here = Get-Location
Set-Location C:\   # cmd.exe cannot start in a UNC directory
try {
    $start = Get-Date
    & (Join-Path $Engine 'Engine\Build\BatchFiles\Build.bat') PyTestGameEditor Win64 Development "-Project=$project" -2019 -WaitMutex
    if ($LASTEXITCODE -ne 0) { throw "building the editor module (PyTestGameEditor) failed with exit code $LASTEXITCODE" }
    & (Join-Path $Engine 'Engine\Build\BatchFiles\RunUAT.bat') BuildCookRun "-project=$project" -noP4 -platform=Win64 "-clientconfig=$Config" `
        -build -cook -stage -nocompileeditor -unattended -utf8output -map=/Engine/Maps/Entry -prereqs
    if ($LASTEXITCODE -ne 0) { throw "UAT failed with exit code $LASTEXITCODE" }
    Write-Output ("packaged in {0:N1} min" -f ((Get-Date) - $start).TotalMinutes)
}
finally {
    Set-Location $here   # leave the caller's shell where it was
}
