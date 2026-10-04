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
    [string]$Cache = "$PSScriptRoot\..\build\installer-cache",
    # A side-by-side installer with its own AppId, registry key, Start menu name and protocol
    # (none of the published Jig's). Never set this for a release. scripts\publish.ps1 does not,
    # and it refuses an installer that carries the test AppId. -OutputDir is required so the test
    # file is not written into dist\, where a release build looks.
    [switch]$TestInstall,
    [string]$OutputDir = ""
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

$OutDir = if ($OutputDir) { $OutputDir } else { Join-Path $Root "dist" }
if ($TestInstall) {
    if (-not $OutputDir) { throw "-TestInstall needs -OutputDir. A test installer is not written to dist\." }
    $releaseDir = [IO.Path]::GetFullPath((Join-Path $Root "dist"))
    $asked = [IO.Path]::GetFullPath($OutputDir)
    if ($asked.TrimEnd('\') -eq $releaseDir.TrimEnd('\')) {
        throw "-TestInstall must not write into dist\. That folder is for the release installer."
    }
}
$Iscc = Join-Path $InnoSetup "ISCC.exe"
$isccArgs = @("/DAppVersion=$Version", "/DStage=$Stage", "/DIcon=$Root\jig\web\favicon.ico", "/O$OutDir")
$idFile = (Join-Path $OutDir "installer-identity.txt") -replace '\\', '/'
$isccArgs += "/DIdentityFile=$idFile"
if ($TestInstall) { $isccArgs += "/DTestInstall=yes" }
& $Iscc @isccArgs "$PSScriptRoot\jig.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup couldn't build the installer" }
$Built = Join-Path $OutDir "JigSetup-$Version.exe"
# The AppId is stored as text in the installer. A release build must carry the published id and
# not the test id, and the other way round. This is the check that -TestInstall cannot sneak
# into a normal build, and that a test build cannot carry the real identity.
& $Python -c @"
import pathlib, sys
exe = pathlib.Path(sys.argv[1]).read_bytes()
script = pathlib.Path(sys.argv[2]).read_text(encoding='utf-8', errors='replace')
want_test = sys.argv[3] == 'yes'
def has(text):
    return text.encode('utf-16le') in exe or text.encode('ascii') in exe
prod = '95703079-D843-48C3-A6C6-5F82AC829549'
test = 'C4E8B2A1-7D5F-4A93-9E16-2B8F0D4C6A71'
if want_test:
    if 'AppId={{' + test + '}' not in script:
        sys.exit('the preprocessed script does not use the test AppId')
    if prod in script or 'Software\\Jig\\Install' in script or 'Software\\Classes\\jig' in script:
        sys.exit('the preprocessed test script still contains the published identity')
    if 'Software\\JigUpdateTest\\Install' not in script:
        sys.exit('the preprocessed test script is missing its registry key')
    if not has('Jig Update Test'):
        sys.exit('the test installer does not identify itself as Jig Update Test')
else:
    if 'AppId={{' + prod + '}' not in script:
        sys.exit('the preprocessed script does not use the published AppId')
    if test in script or 'JigUpdateTest' in script:
        sys.exit('the preprocessed release script contains the test identity')
    if 'Software\\Classes\\jig' not in script or 'Software\\Jig\\Install' not in script:
        sys.exit('the preprocessed release script is missing the protocol or registry key')
    if has('Jig Update Test') or has('JigUpdateTest'):
        sys.exit('the release installer contains the test identity')
print('installer identity ok')
"@ $Built $idFile $(if ($TestInstall) { 'yes' } else { 'no' })
if ($LASTEXITCODE -ne 0) { throw "The installer identity check failed" }
Write-Host "Built $Built"
