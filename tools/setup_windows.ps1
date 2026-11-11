<#
.SYNOPSIS
  Prepares a Windows machine to build the Python scripting mod for UE4SS.

.DESCRIPTION
  Checks the toolchain (git, CMake >= 3.22, MSVC with the C++ workload, Rust >= 1.73),
  downloads the pinned CPython (embeddable runtime + NuGet package for headers and import
  libraries) with SHA-256 verification into third_party/python, and clones RE-UE4SS with
  submodules next to this repository. Does not build anything.

  Not run from WSL. Run from a normal PowerShell prompt.

.PARAMETER Ue4ssDir
  Where RE-UE4SS is (or will be) cloned. Default: a RE-UE4SS folder next to this repository

.PARAMETER UEPseudoDir
  Local checkout of the private Re-UE4SS/UEPseudo repository. If deps\first\Unreal is empty
  after the clone, it is populated from here and checked out at the commit RE-UE4SS pins.

.PARAMETER SkipUe4ss
  Skip the RE-UE4SS clone/update step.
#>
[CmdletBinding()]
param(
    [string]$Ue4ssDir = '',
    [switch]$SkipUe4ss,
    [string]$UEPseudoDir = ''
)

$ErrorActionPreference = 'Stop'

# Pinned versions. Change these together with docs/BUILDING.md.
$PythonVersion  = '3.12.10'
$EmbedUrl       = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$EmbedSha256    = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
$NugetUrl       = "https://www.nuget.org/api/v2/package/python/$PythonVersion"
$NugetSha256    = '0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8'
$Ue4ssRepo      = 'https://github.com/UE4SS-RE/RE-UE4SS'
$MinCMake       = [version]'3.22'
$MinRust        = [version]'1.73.0'

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$repoRoot  = Split-Path -Parent $scriptDir
if ($repoRoot.StartsWith('\\') -or $repoRoot.StartsWith('Microsoft.PowerShell.Core\FileSystem::')) {
    throw "Run this from a checkout on a Windows drive (e.g. C:\dev\ue4ss-python), not from a UNC path like \\wsl.localhost\...; see docs/BUILDING.md."
}
if (-not $Ue4ssDir) { $Ue4ssDir = Join-Path (Split-Path -Parent $repoRoot) 'RE-UE4SS' }
$pyRoot   = Join-Path $repoRoot 'third_party\python'

function Get-ToolVersion([string]$Command, [string]$Pattern) {
    $cmd = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $cmd) { return $null }
    $out = & $cmd.Source --version 2>&1 | Out-String
    if ($out -match $Pattern) { return [version]$Matches[1] }
    return $null
}

function Assert-Tool([string]$Name, $Found, [version]$Min) {
    if (-not $Found) { throw "$Name not found on PATH." }
    if ($Min -and $Found -lt $Min) { throw "$Name $Found is older than required $Min." }
    Write-Host "ok  $Name $Found"
}

function Get-VerifiedDownload([string]$Url, [string]$Sha256, [string]$Dest) {
    if (-not (Test-Path $Dest)) {
        Write-Host "download $Url"
        Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
    }
    $actual = (Get-FileHash -Algorithm SHA256 -Path $Dest).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256) {
        Remove-Item $Dest -Force
        throw "SHA-256 mismatch for $Url`n expected $Sha256`n actual   $actual"
    }
}

# --- toolchain checks ---
Assert-Tool 'git'   (Get-ToolVersion 'git'   'git version (\d+\.\d+\.\d+)') $null
Assert-Tool 'cmake' (Get-ToolVersion 'cmake' 'cmake version (\d+\.\d+(\.\d+)?)') $MinCMake
Assert-Tool 'rustc' (Get-ToolVersion 'rustc' 'rustc (\d+\.\d+\.\d+)') $MinRust

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) { throw 'vswhere.exe not found. Install Visual Studio 2022 or Build Tools.' }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'No Visual Studio with the "C++ x64/x86 build tools" component found.' }
Write-Host "ok  MSVC at $vs"

# --- pinned CPython ---
New-Item -ItemType Directory -Force -Path $pyRoot | Out-Null
$embedZip = Join-Path $pyRoot "python-$PythonVersion-embed-amd64.zip"
$nugetZip = Join-Path $pyRoot "python-$PythonVersion.nupkg.zip"
Get-VerifiedDownload $EmbedUrl $EmbedSha256 $embedZip
Get-VerifiedDownload $NugetUrl $NugetSha256 $nugetZip

$embedDir = Join-Path $pyRoot 'runtime'   # python312.dll, python312.zip, vcruntime*.dll
$sdkDir   = Join-Path $pyRoot 'sdk'       # include/ and libs/
Expand-Archive -Path $embedZip -DestinationPath $embedDir -Force
$nugetTmp = Join-Path $pyRoot 'nuget-tmp'
Expand-Archive -Path $nugetZip -DestinationPath $nugetTmp -Force
New-Item -ItemType Directory -Force -Path $sdkDir | Out-Null
Copy-Item (Join-Path $nugetTmp 'tools\include') $sdkDir -Recurse -Force
Copy-Item (Join-Path $nugetTmp 'tools\libs')    $sdkDir -Recurse -Force
Remove-Item $nugetTmp -Recurse -Force
Write-Host "ok  CPython $PythonVersion in $pyRoot"

# --- RE-UE4SS ---
if (-not $SkipUe4ss) {
    if (-not (Test-Path (Join-Path $Ue4ssDir '.git'))) {
        git clone --recurse-submodules $Ue4ssRepo $Ue4ssDir
    } else {
        git -C $Ue4ssDir submodule update --init --recursive
    }
    $unrealDir = Join-Path $Ue4ssDir 'deps\first\Unreal'
    if ($UEPseudoDir -and -not (Test-Path (Join-Path $unrealDir 'include'))) {
        $pin = (git -C $Ue4ssDir ls-tree HEAD deps/first/Unreal).Split()[2]
        if (Test-Path $unrealDir) { Remove-Item $unrealDir -Recurse -Force }
        git clone $UEPseudoDir $unrealDir
        git -C $unrealDir checkout $pin
    }
    if (-not (Test-Path (Join-Path $unrealDir 'include'))) {
        Write-Warning 'deps/first/Unreal (Re-UE4SS/UEPseudo) is missing. It is a private repository: request access or pass -UEPseudoDir <local checkout>, then rerun.'
    }
}

Write-Host "`nDone. Next: see docs/BUILDING.md (build UE4SS and the mod with the same <Target>__<Config>__Win64)."
