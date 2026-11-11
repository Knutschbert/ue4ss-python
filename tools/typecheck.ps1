# Runs mypy (installed into third_party\typecheck, see docs/TYPED_API.md) against the stubs the game generated.
# STUBS overrides the stub folder; PYTHON overrides the interpreter (default: python on PATH).
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $root 'third_party\typecheck'
if (-not $env:STUBS) { $env:STUBS = Join-Path $root 'testgame\stubs_testgame' }
$env:MYPYPATH = $env:STUBS + ';' + (Join-Path $root 'python')
$python = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
Set-Location $root
& $python -m mypy --python-version 3.12 --no-error-summary "--cache-dir=$(Join-Path $root 'third_party\typecheck\.mypy_cache')" @args
