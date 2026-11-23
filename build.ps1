param(
    [string]$Version = '0.1.0'
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$webviewPlugin = python -c 'import pathlib, PySide6; print(pathlib.Path(PySide6.__file__).parent / "plugins" / "webview" / "qtwebview_webview2.dll")'
if (-not (Test-Path -LiteralPath $webviewPlugin)) { throw 'Qt WebView2 plugin is missing.' }

python -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name XMediaSaver `
    --icon web/assets/icons/app-icon.ico `
    --add-data 'web/assets;web/assets' `
    --add-binary "$webviewPlugin;PySide6/plugins/webview" `
    --collect-all imageio_ffmpeg `
    desktop.py
if ($LASTEXITCODE -ne 0) { throw 'Desktop app build failed.' }

# PyInstaller can pick up unrelated ICU DLLs from the runner PATH. QtWebView
# uses the Windows system ICU API, so those copies break QtCore at startup.
foreach ($name in @('icuuc.dll', 'icudt78.dll')) {
    $foreignIcu = Join-Path $PSScriptRoot "dist\XMediaSaver\_internal\$name"
    if (Test-Path -LiteralPath $foreignIcu) {
        Remove-Item -LiteralPath $foreignIcu
    }
}

python -m PyInstaller --noconfirm --clean --onefile --console `
    --name XMediaSaverCLI `
    --collect-all gallery_dl `
    gallery_dl_cli.py
if ($LASTEXITCODE -ne 0) { throw 'Download helper build failed.' }

$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )
    $iscc = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $iscc) { throw 'Inno Setup 6 (ISCC.exe) is required.' }

& $iscc "/DAppVersion=$Version" installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }

$installer = Join-Path $PSScriptRoot 'dist\installer\XMediaSaverSetup.exe'
if (-not (Test-Path -LiteralPath $installer)) { throw 'Installer was not created.' }
Get-FileHash -Algorithm SHA256 -LiteralPath $installer |
    ForEach-Object { "$($_.Hash.ToLowerInvariant())  XMediaSaverSetup.exe" } |
    Set-Content -Encoding ascii -LiteralPath "$installer.sha256"
Write-Host "Installer: $installer"
