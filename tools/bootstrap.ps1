param([switch]$WithZig, [switch]$WithGui)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tempDir = Join-Path $root '.tmp'
$venvDir = Join-Path $root '.venv'
$toolsDir = Join-Path $root '.tools'
New-Item -ItemType Directory -Force $tempDir, $toolsDir | Out-Null
$env:TEMP = $tempDir
$env:TMP = $tempDir

if (-not (Test-Path (Join-Path $venvDir 'Scripts\python.exe'))) {
    python -m venv $venvDir --without-pip
}
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
& $venvPython -m ensurepip --upgrade --default-pip
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment pip bootstrap failed.' }
$wheelProfile = & $venvPython -c 'import platform, sys; print("win-cp313" if sys.platform == "win32" and sys.version_info[:2] == (3, 13) and platform.machine().lower() in ("amd64", "x86_64") else "other")'
if ($wheelProfile -eq 'win-cp313') {
    & $venvPython -m pip install --require-hashes --only-binary=:all: -r (Join-Path $root 'requirements-win-cp313.hashes.lock')
} else {
    & $venvPython -m pip install --only-binary=:all: -r (Join-Path $root 'requirements-dev.lock')
}
if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency installation failed.' }

if ($WithGui) {
    if ($wheelProfile -eq 'win-cp313') {
        & $venvPython -m pip install --require-hashes --only-binary=:all: -r (Join-Path $root 'requirements-gui-win-cp313.hashes.lock')
    } else {
        & $venvPython -m pip install --only-binary=:all: -r (Join-Path $root 'requirements-gui.lock')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Pinned GUI dependency installation failed.' }
}

# The Windows source-tree workflow continues to build its local Zig extension
# with tools/build_native.py. The Linux/release wheel uses the CMake backend.
& $venvPython -m pip install --no-build-isolation --no-deps -e $root '--config-settings=wheel.cmake=false' '--config-settings=wheel.packages=src/nmrnext'
if ($LASTEXITCODE -ne 0) { throw 'Editable project installation failed.' }

if ($WithZig) {
    $archive = Join-Path $toolsDir 'zig-x86_64-windows-0.17.0.zip'
    $zigPath = Join-Path $toolsDir 'zig-x86_64-windows-0.17.0\zig.exe'
    $expected = 'B5663F69581DCF391293FBF16C06CB80D81D806545CE618B4D0BAB7F0EB8C428'
    if (-not (Test-Path $archive)) {
        Invoke-WebRequest 'https://ziglang.org/download/0.17.0/zig-x86_64-windows-0.17.0.zip' -OutFile $archive
    }
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actual -ne $expected) { throw "Zig archive SHA-256 mismatch: $actual" }
    if (-not (Test-Path $zigPath)) {
        Expand-Archive -LiteralPath $archive -DestinationPath $toolsDir
    }
    & $zigPath version
    if ($LASTEXITCODE -ne 0) { throw 'Zig compiler is not usable.' }
}

Write-Host "Ready: $venvPython"
