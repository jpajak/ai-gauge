#requires -version 7
<#
Build a standalone Windows .exe using PyInstaller.

Usage:
    .\build.ps1            # one-folder build (recommended)
    .\build.ps1 -OneFile   # single-file build (slower startup)
#>
param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "Virtualenv not found at .venv. Run: py -m venv .venv; .venv\Scripts\pip install -e .[dev]"
}

& $venvPython -m pip install --quiet pyinstaller

$versionInfo = Join-Path $PSScriptRoot "build\pyinstaller-version-info.txt"
& $venvPython (Join-Path $PSScriptRoot "tools\write_pyinstaller_version_info.py") $versionInfo

if ($OneFile) {
    $targetExe = Join-Path $PSScriptRoot "dist\ai-gauge.exe"
    if (Test-Path $targetExe) {
        try {
            Remove-Item -LiteralPath $targetExe -Force -ErrorAction Stop
        } catch {
            Write-Error "Cannot replace dist\ai-gauge.exe. Close any running ai-gauge.exe process, then build again."
        }
    }
}

$args = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--noupx",
    "--name", "ai-gauge",
    "--version-file", $versionInfo,
    "--paths", "src",
    "--collect-all", "PyQt6.QtWebEngineWidgets",
    "--collect-all", "PyQt6.QtWebEngineCore",
    "pyinstaller_entry.py"
)
if ($OneFile) { $args += "--onefile" }

& $venvPython @args

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
if ($OneFile) {
    Write-Host "Binary: dist\ai-gauge.exe"
} else {
    Write-Host "Folder: dist\ai-gauge\  (run ai-gauge.exe inside)"
}
