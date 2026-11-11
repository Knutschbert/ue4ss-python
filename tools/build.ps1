<#
.SYNOPSIS
  Configures and builds the mod from a normal PowerShell (no developer prompt needed).

.PARAMETER Config
  <Target>__<Config>__Win64, must match the installed UE4SS. Default: Game__Shipping__Win64.

.PARAMETER Target
  CMake target to build. Default: PythonScripting. Use UE4SS to build UE4SS itself.

.PARAMETER Ue4ssRoot
  RE-UE4SS checkout. Default: a RE-UE4SS folder next to this repository.

.PARAMETER Jobs
  Maximum parallel compile jobs (0 = Ninja's default). Lower it if cl.exe fails with C1060 (out of heap).

.PARAMETER Reconfigure
  Delete the build cache and configure again.
#>
[CmdletBinding()]
param(
    [string]$Config = 'Game__Shipping__Win64',
    [string]$Target = 'PythonScripting',
    [string]$Ue4ssRoot = '',
    [int]$Jobs = 0,
    [switch]$Reconfigure
)

$ErrorActionPreference = 'Stop'

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$repoRoot  = Split-Path -Parent $scriptDir
if ($repoRoot.StartsWith('\\')) { throw 'Run from a Windows drive checkout, not a UNC path.' }
if (-not $Ue4ssRoot) { $Ue4ssRoot = Join-Path (Split-Path -Parent $repoRoot) 'RE-UE4SS' }
if (-not (Test-Path (Join-Path $Ue4ssRoot 'CMakeLists.txt'))) { throw "RE-UE4SS not found at $Ue4ssRoot (run tools\setup_windows.ps1)." }

# Load the x64 MSVC environment into this process.
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) { throw 'vswhere.exe not found.' }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'No Visual Studio with C++ build tools found.' }
$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
cmd /c "`"$vcvars`" >nul && set" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2] }
}

$buildDir = Join-Path $repoRoot 'build'
if ($Reconfigure -and (Test-Path $buildDir)) { Remove-Item $buildDir -Recurse -Force }
if (-not (Test-Path (Join-Path $buildDir 'CMakeCache.txt'))) {
    cmake -S $repoRoot -B $buildDir -G Ninja "-DCMAKE_BUILD_TYPE=$Config" "-DUE4SS_ROOT=$Ue4ssRoot"
    if ($LASTEXITCODE) { throw "cmake configure failed ($LASTEXITCODE)" }
}
$buildArgs = @('--build', $buildDir, '--target', $Target)
if ($Jobs -gt 0) { $buildArgs += @('--parallel', $Jobs) }
cmake @buildArgs
if ($LASTEXITCODE) { throw "build failed ($LASTEXITCODE)" }
Write-Host "Build OK: $Target ($Config)"
