<#
.SYNOPSIS
  Performs a REAL run of a Jig demo, checks it (acceptance test), captures it and renders the video.

.DESCRIPTION
  demos/run.ps1 <name> [-Test] [-CaptureOnly] [-RenderOnly] [-Capture <stamp>] [-Format landscape|portrait|all]
                       [-Theme jig-light|jig-dark|neon|warm] [-NoAudio] [-Commit <sha>] [-PrepareOnly]

  <name> is one of: research, coding, background, memory, safety (the launch demos; `launch` runs all five),
  quickstart, compose, byo-model, always-on, all (or the full scenario file name, such as 07-research).
  Without -Theme, each scenario renders in its own theme (jig-light unless it says otherwise).

  Each run:
    1. snapshots the committed tree (HEAD, or -Commit) into demos/.work/src-<sha> with `git archive`, so
       uncommitted work by others never ends up on camera, and installs it into demos/.work/venv;
    2. starts its own Jig on port 8770 with a fresh temporary data folder (never 8080/8765/8766/8767/8780/8790);
    3. drives the real UI and real console commands against the real local model, asserting on UI and API states;
    4. renders the video(s) into demos/out/<scenario>/.
  Exit code: 0 passed, 1 failed, 3 pending (the feature has not landed in the commit under test).
#>
param(
  [Parameter(Mandatory = $true, Position = 0)][string]$Name,
  [switch]$Test,
  [switch]$CaptureOnly,
  [switch]$RenderOnly,
  [string]$Capture = 'latest',
  [string]$Format = 'all',
  [string]$Theme = '',
  [switch]$NoAudio,
  [string]$Commit = '',
  [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
$Demos = $PSScriptRoot
$Repo = Split-Path $Demos -Parent
# DEMO_WORK (see lib/util.mjs): pip cannot reinstall a venv whose jig.exe a running Jig holds open
$Work = if ($env:DEMO_WORK) { $env:DEMO_WORK } else { Join-Path $Demos '.work' }

$FfmpegBin = 'C:\Users\you\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin'
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  if (-not (Test-Path (Join-Path $FfmpegBin 'ffmpeg.exe'))) { throw "ffmpeg is not on PATH and not at $FfmpegBin (winget install Gyan.FFmpeg)" }
  $env:PATH = "$FfmpegBin;$env:PATH"
}

$Aliases = [ordered]@{
  'research' = '07-research'; 'coding' = '08-coding'; 'background' = '09-background'; 'memory' = '04-memory'
  'safety' = '03-safety'; 'quickstart' = '01-quickstart'; 'compose' = '01-quickstart-compose'
  'byo-model' = '05-byo-model'; 'always-on' = '06-always-on'
}
# The launch demos, in the order they are usually recorded
$Launch = @('07-research', '08-coding', '09-background', '04-memory', '03-safety')
if ($Name -eq 'all') { $Scenarios = @($Aliases.Values) }
elseif ($Name -eq 'launch') { $Scenarios = $Launch }
elseif ($Aliases.Contains($Name)) { $Scenarios = @($Aliases[$Name]) }
elseif (Test-Path (Join-Path $Demos "scenarios\$Name.mjs")) { $Scenarios = @($Name) }
else { throw "Unknown demo '$Name'. Use one of: $($Aliases.Keys -join ', '), all" }

function Invoke-Native([string]$What, [scriptblock]$Block) {
  # Windows PowerShell turns any stderr line from a native tool into an error; judge by the exit code instead
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { & $Block 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -notmatch '^\[notice\]' } | Write-Host }
  finally { $ErrorActionPreference = $saved }
  if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

function Initialize-Snapshot {
  New-Item -ItemType Directory -Force $Work | Out-Null
  $rev = if ($Commit) { "$Commit^{commit}" } else { 'HEAD' }
  $sha = git -C $Repo rev-parse --short --verify $rev
  if ($LASTEXITCODE -ne 0 -or -not $sha) { throw "git rev-parse failed for $rev" }
  $sha = $sha.Trim()
  $src = Join-Path $Work "src-$sha"
  if (-not (Test-Path (Join-Path $src 'pyproject.toml'))) {
    Write-Host "Snapshotting commit $sha into $src"
    $zip = Join-Path $Work "src-$sha.zip"
    Invoke-Native 'git archive' { git -C $Repo archive --format=zip -o $zip $sha }
    New-Item -ItemType Directory -Force $src | Out-Null
    Invoke-Native 'tar' { tar -xf $zip -C $src }
    Remove-Item $zip
  }
  $venv = Join-Path $Work 'venv'
  $py = Join-Path $venv 'Scripts\python.exe'
  if (-not (Test-Path $py)) {
    Write-Host "Creating $venv"
    Invoke-Native 'python -m venv' { python -m venv $venv }
    Invoke-Native 'pip install tools' { & $py -m pip install -q pywinpty numpy scipy }
  }
  $installed = if (Test-Path (Join-Path $Work 'venv-commit.txt')) { (Get-Content (Join-Path $Work 'venv-commit.txt')).Trim() } else { '' }
  if ($installed -ne $sha) {
    $exe = Join-Path $venv 'Scripts\jig.exe'
    $users = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$exe*" })
    if ($users) { throw "A Jig (pid $($users[0].ProcessId)) is running from $venv, so pip can't reinstall it; set DEMO_WORK to a folder of its own" }
    Write-Host "Installing Jig $sha into the demo venv"
    Invoke-Native 'pip install -e' { & $py -m pip install -q -e $src }
    Set-Content (Join-Path $Work 'venv-commit.txt') $sha
  }
  $where = (& $py -c "import jig, pathlib; print(pathlib.Path(jig.__file__).resolve().parents[1].name)" 2>$null)
  if ($where -ne "src-$sha") { throw "The demo venv imports Jig from '$where', expected src-$sha" }
  Set-Content (Join-Path $Work 'snapshot.txt') $sha
  return $sha
}

Push-Location $Demos
try {
  if (-not (Test-Path (Join-Path $Demos 'node_modules\playwright'))) { Invoke-Native 'npm install' { npm install --no-audit --no-fund } }
  $sha = Initialize-Snapshot
  Write-Host "Jig under test: commit $sha"
  if ($PrepareOnly) { exit 0 }
  $exit = 0
  foreach ($s in $Scenarios) {
    Write-Host "`n=== $s ==="
    if (-not $RenderOnly) {
      $captureArgs = @('lib/capture.mjs', $s)
      if ($Test) { $captureArgs += '--test' }
      & node @captureArgs
      $code = $LASTEXITCODE
      if ($code -eq 3) { Write-Host "$s is PENDING (feature not in commit $sha)"; if ($exit -eq 0) { $exit = 3 }; continue }
      if ($code -ne 0) { Write-Host "$s FAILED (exit $code)"; $exit = 1; continue }
    }
    if (-not $CaptureOnly) {
      $renderArgs = @('lib/render.mjs', $s, "--capture=$Capture", "--format=$Format")
      if ($Theme) { $renderArgs += "--theme=$Theme" }
      if ($NoAudio) { $renderArgs += '--no-audio' }
      if ($Test) { $renderArgs += '--test' }
      & node @renderArgs
      if ($LASTEXITCODE -ne 0) { Write-Host "$s render FAILED"; $exit = 1 }
    }
  }
  exit $exit
} finally {
  Pop-Location
}
