# Builds the Jig installer for Windows: dist\JigSetup-<version>.exe
#
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 -InnoSetup C:\path\to\innosetup
#
# Needs: Python 3.11+ with pip (to fetch Jig's dependencies as wheels), and Inno Setup 6
# (https://jrsoftware.org/isinfo.php; ISCC.exe in -InnoSetup). The installer bundles the official
# Windows embeddable Python from python.org (checked against its published SHA-256), so the people
# who install Jig need no Python, Git or terminal.

param(
    [Parameter(Mandatory = $true)][string]$InnoSetup,
    [string]$Python = "python",
    [string]$Cache = "$PSScriptRoot\..\build\installer-cache"
)
$ErrorActionPreference = "Stop"

$PyVersion = "3.13.16"
$PyZip = "python-$PyVersion-embed-amd64.zip"
$PySha256 = "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"  # from python.org's release page

$Root = (Resolve-Path "$PSScriptRoot\..").Path
$Stage = Join-Path $Root "build\installer-stage"
$Version = (& $Python -c "import tomllib; print(tomllib.load(open(r'$Root\pyproject.toml','rb'))['project']['version'])").Trim()
if ($LASTEXITCODE -ne 0) { throw "Couldn't read the version from pyproject.toml" }

New-Item -ItemType Directory -Force $Cache | Out-Null
$ZipPath = Join-Path $Cache $PyZip
if (-not (Test-Path $ZipPath)) {
    Write-Host "Downloading $PyZip from python.org"
    Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/$PyZip" -OutFile $ZipPath
}
$Actual = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLower()
if ($Actual -ne $PySha256) { throw "$PyZip has SHA-256 $Actual, not python.org's $PySha256. Delete it and try again." }

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force "$Stage\python", "$Stage\app" | Out-Null
Expand-Archive $ZipPath -DestinationPath "$Stage\python"

# The embeddable Python ignores PYTHONPATH and site-packages unless its ._pth file lists them.
$Pth = Get-ChildItem "$Stage\python" -Filter "python*._pth" | Select-Object -First 1
$ZipName = (Get-ChildItem "$Stage\python" -Filter "python3*.zip" | Select-Object -First 1).Name
Set-Content -Path $Pth.FullName -Encoding ascii -Value @($ZipName, ".", "..\app", "Lib\site-packages", "import site")

# Jig and its dependencies, as Windows wheels for this exact Python. Jig itself goes in app\ (next to
# installed.json, which tells it it was installed this way); its dependencies in Lib\site-packages.
$Short = ($PyVersion -split "\.")[0..1] -join ""
# proxy_tools (which pywebview needs) is published only as source. It's one pure-Python module, so its wheel
# is built here and works on any Windows.
$Wheels = Join-Path $Cache "wheels"
& $Python -m pip wheel --disable-pip-version-check --no-deps --wheel-dir $Wheels "proxy_tools==0.1.0"
if ($LASTEXITCODE -ne 0) { throw "pip couldn't build the proxy_tools wheel" }
& $Python -m pip install --disable-pip-version-check --no-warn-script-location --only-binary=:all: `
    --platform win_amd64 --python-version $Short --implementation cp --find-links $Wheels `
    --target "$Stage\python\Lib\site-packages" $Root
if ($LASTEXITCODE -ne 0) { throw "pip couldn't install Jig and its dependencies" }
Move-Item "$Stage\python\Lib\site-packages\jig" "$Stage\app\jig"
Get-ChildItem "$Stage\python\Lib\site-packages" -Directory -Filter "jig-*.dist-info" | Remove-Item -Recurse -Force
Remove-Item -Recurse -Force "$Stage\python\Lib\site-packages\bin" -ErrorAction SilentlyContinue

# The web UI serves the avatar from the source tree's avatar\ folder, next to jig\.
New-Item -ItemType Directory -Force "$Stage\app\avatar" | Out-Null
Copy-Item "$Root\avatar\jig-avatar.js" "$Stage\app\avatar\jig-avatar.js"
Copy-Item "$Root\LICENSE" "$Stage\LICENSE.txt"
Copy-Item "$PSScriptRoot\jig.toml" "$Stage\jig.toml.template"
Set-Content -Path "$Stage\app\installed.json" -Encoding utf8 -Value "{`"installer`": `"$Version`"}"

# A quick check that the bundled Python can load Jig before packaging it.
& "$Stage\python\python.exe" -c "import jig.cli, jig.tray, fastapi, uvicorn, cryptography, pypdf, PIL, pypdfium2; from jig.api.app import AVATAR_JS, WEB_DIR; assert AVATAR_JS.is_file() and (WEB_DIR / 'index.html').is_file(), 'web UI files missing'; print('Jig loads')"
if ($LASTEXITCODE -ne 0) { throw "The bundled Python couldn't load Jig" }
# Jig's window: pywebview, and through pythonnet the .NET Framework that Windows includes.
& "$Stage\python\python.exe" -c "import jig.desktop, tzlocal, webview; import webview.platforms.winforms as w; from System.Windows.Forms import Form; print('The window loads (engine on this computer: ' + w.renderer + ')')"
if ($LASTEXITCODE -ne 0) { throw "The bundled Python couldn't load Jig's window (pywebview and pythonnet)" }
Get-ChildItem $Stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

$Iscc = Join-Path $InnoSetup "ISCC.exe"
& $Iscc "/DAppVersion=$Version" "/DStage=$Stage" "/DIcon=$Root\jig\web\favicon.ico" "/O$Root\dist" "$PSScriptRoot\jig.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup couldn't build the installer" }
Write-Host "Built $Root\dist\JigSetup-$Version.exe"
