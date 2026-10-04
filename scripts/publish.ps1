<#
.SYNOPSIS
    Publishes Jig as a public GitHub repository under the personal account rlesueur, with GitHub Pages
    and a first release carrying the Windows installer.

.DESCRIPTION
    Run this once, from the repository root, when all work is committed. It:
      a. checks that the GitHub CLI is signed in as rlesueur (never an organisation);
      b. checks that the working tree is clean, the branch is main and nothing has been published yet, that
         Inno Setup is available, that pyproject.toml, jig/__init__.py and compose.yaml agree on the version,
         and that its tag (v<version>) does not exist yet;
      c. sets the author and committer of every commit whose author or committer is 'Jig <jig@localhost>'
         or has the name 'rlesueur' to 'Robyn Le Sueur <2302916+rlesueur@users.noreply.github.com>' with
         git filter-repo, replaces local absolute paths (C:\Users\<name>\...) in the history with portable
         ones, and in the same pass strips the self-authored attack content (listed in $AttackPaths) from
         every commit, then stops unless that is the only author and committer identity left in the history;
         a bundle of the original history is saved first;
      d. verifies that no removed attack path and no distinctive marker from those files survives anywhere
         in the rewritten history, then scans it (gitleaks plus explicit checks) and stops on any finding;
      e. builds the Windows installer, JigSetup-<version>.exe, with installer\build.ps1 from a clean export
         (git archive) of the rewritten HEAD, so it holds exactly what is published, and writes its SHA-256
         to JigSetup-<version>.exe.sha256; this happens before anything is pushed;
      f. creates github.com/rlesueur/<repo> as a public repository and pushes main;
      g. sets the topics;
      h. enables GitHub Pages with GitHub Actions as the source, and private vulnerability reporting;
      i. tags HEAD as v<version> (annotated), pushes the tag (which starts the container workflow), creates
         the GitHub release with the installer and its .sha256 file, and checks the uploaded installer by
         downloading it again and comparing its SHA-256;
      j. waits for the Pages workflow and prints the live URL;
      k. verifies that the repository's owner is the user account rlesueur.

    With -DryRun it only runs the non-destructive steps: (a), (b), and (c), the attack-content
    verification, (d) and (e) on a throwaway clone in the temporary folder, then prints the tag and release
    it would publish. The installer and .sha256 file it built are left in the temporary folder for testing.
    The real repository, its history and GitHub are not touched (the build reuses the git-ignored
    build\installer-cache, which only holds python.org's embeddable Python, checked by its SHA-256).

.PARAMETER InnoSetup
    The Inno Setup 6 folder (the one with ISCC.exe). Without it the script looks on PATH, in the usual
    install folders and in %USERPROFILE%\tools\innosetup-*.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\publish.ps1 -DryRun
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\publish.ps1
#>
[CmdletBinding()]
param(
    [string]$Repo = 'jig',
    [string]$InnoSetup,
    [switch]$DryRun,
    [switch]$Subsequent
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Owner = 'rlesueur'
$OwnerId = 2302916
$NewName = 'Robyn Le Sueur'
$NewEmail = '2302916+rlesueur@users.noreply.github.com'
$NewIdent = "$NewName <$NewEmail>"
$PagesUrl = "https://$Owner.github.io/$Repo/"
$Description = 'An open-source, always-on personal AI agent for your own local model. Memory, rules, audit trail and secrets stay on your machine.'
$Topics = @('local-ai', 'ai-agents', 'local-llm', 'privacy', 'self-hosted', 'llama-cpp', 'ollama', 'open-source')
$MaxBlobBytes = 50MB

# Self-authored attack content from the early research commits. It was removed from the working tree in
# commit e6a3913, but still lives in the history; it is stripped from every commit during the rewrite so
# it can never reach the public repository. Paths are repository-relative; a trailing slash means a whole
# directory. Keep this list and $AttackMarkers in step with anything removed as authored attack content.
$AttackPaths = @(
    'research/benchmark/'
    'research/harness/scenarios/'
    'research/harness/jigbench/experiments/d1_sentinel.py'
    'research/harness/jigbench/experiments/d2_injection.py'
    'research/harness/jigbench/experiments/cd1_poisoning.py'
    'research/harness/configs/full/d1_full.yaml'
    'research/harness/configs/full/d2_full.yaml'
    'research/harness/configs/full/cd1_full.yaml'
    'research/harness/configs/pilot/d1_pilot.yaml'
    'research/harness/configs/pilot/d2_pilot.yaml'
    'research/harness/configs/pilot/d2_pilot_e4b.yaml'
    'research/harness/configs/pilot/cd1_pilot.yaml'
    'research/results/d1/'
    'research/results/d2/'
)
# Distinctive, benign identifiers (a function name from the D1 experiment and the D2 injection-variant
# label) that only ever appeared inside the removed files. After the rewrite none of them may remain in
# any blob except this file, which has to name them. These are plain source identifiers, not attack text.
$AttackMarkers = @('tool_mimic', 'decision_of', 'items_for')

function Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Ok([string]$Text) { Write-Host "    OK  $Text" -ForegroundColor Green }
function Fail([string]$Text) { throw "ABORTED: $Text" }

# Runs a native command and throws if it exits non-zero. Returns its standard output as text lines.
# Run and Try-Run deliberately have no param block: as simple functions they pass every argument,
# including short flags such as -I or -p, to the native command instead of binding them to common parameters.
function Run {
    $Exe = $args[0]
    $Rest = @($args | Select-Object -Skip 1)
    $out = & $Exe @Rest
    if ($LASTEXITCODE -ne 0) { Fail "'$Exe $($Rest -join ' ')' exited with code $LASTEXITCODE" }
    return $out
}

# Runs a native command whose failure is an expected answer (for example 'repository not found').
function Try-Run {
    $Exe = $args[0]
    $Rest = @($args | Select-Object -Skip 1)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = & $Exe @Rest 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $previous }
    return [pscustomobject]@{ Code = $LASTEXITCODE; Output = ($out -join "`n") }
}

function Find-Tool([string]$Name, [string]$WingetPackage, [string]$Hint) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $root = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    $hit = Get-ChildItem $root -Directory -Filter "$WingetPackage*" -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem $_.FullName -Recurse -Filter "$Name.exe" -ErrorAction SilentlyContinue } |
        Select-Object -First 1
    if ($hit) { return $hit.FullName }
    Fail "$Name was not found on PATH or in the WinGet packages folder. $Hint"
}

# --- (a) GitHub account ---------------------------------------------------------------------
function Assert-Account {
    Step '(a) Checking the GitHub CLI account'
    $login = (Run $script:Gh api user --jq '.login').Trim()
    $id = [int](Run $script:Gh api user --jq '.id')
    $type = (Run $script:Gh api user --jq '.type').Trim()
    if ($login -ne $Owner -or $id -ne $OwnerId -or $type -ne 'User') {
        Fail "the GitHub CLI is signed in as '$login' (id $id, type $type), not the personal account $Owner ($OwnerId). Run 'gh auth switch' or 'gh auth login'."
    }
    Ok "signed in as $login (id $id, personal account)"
}

# --- (b) Clean working tree and nothing published yet ---------------------------------------
function Assert-Clean {
    Step '(b) Checking the working tree'
    $branch = (Run git rev-parse --abbrev-ref HEAD).Trim()
    if ($branch -ne 'main') { Fail "the current branch is '$branch', not main." }
    $status = Run git status --porcelain --untracked-files=all
    if ($status) {
        $status | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
        Fail 'the working tree has uncommitted or untracked changes (listed above). Commit or remove them first.'
    }
    Ok 'working tree is clean on main'

    $remotes = Run git remote
    if ($remotes) { Fail "the repository already has remotes ($($remotes -join ', ')). This script expects an unpublished repository." }
    Ok 'no git remotes configured'

    $view = Try-Run $script:Gh repo view "$Owner/$Repo" --json name
    if ($view.Code -eq 0) { Fail "github.com/$Owner/$Repo already exists." }
    if ($view.Output -notmatch 'Could not resolve to a Repository') { Fail "could not confirm that github.com/$Owner/$Repo is free: $($view.Output)" }
    Ok "github.com/$Owner/$Repo is free"
}

# --- (c) Rewrite authors and local paths ----------------------------------------------------
function Get-FilterRepo {
    $python = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $python) { Fail 'python was not found on PATH; it is needed to run git filter-repo.' }
    $probe = Try-Run $python.Source -m git_filter_repo --version
    if ($probe.Code -ne 0) {
        Write-Host '    Installing git-filter-repo into the Python user site'
        Run $python.Source -m pip install --user --quiet git-filter-repo | Out-Null
        $probe = Try-Run $python.Source -m git_filter_repo --version
        if ($probe.Code -ne 0) { Fail "git-filter-repo is installed but will not run: $($probe.Output)" }
    }
    Ok "git filter-repo $($probe.Output.Trim())"
    return $python.Source
}

function Invoke-Rewrite([string]$Python, [string]$RepoPath) {
    $work = Join-Path ([IO.Path]::GetTempPath()) "jig-publish-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    New-Item -ItemType Directory -Path $work | Out-Null
    $driver = Join-Path $work 'rewrite.py'
    $replacements = Join-Path $work 'replacements'
    $attackPaths = Join-Path $work 'attack-paths'
    # A mailmap cannot match on a name alone, so the identities are rewritten in a commit callback. The same
    # pass strips the authored attack paths from every commit (--invert-paths over the listed --path values).
    [IO.File]::WriteAllText($driver, @'
import sys
import git_filter_repo as fr

new_name, new_email, replacements, attack_paths_file = (
    sys.argv[1].encode(), sys.argv[2].encode(), sys.argv[3], sys.argv[4])

def stale(name, email):
    return name == b'rlesueur' or (name, email) == (b'Jig', b'jig@localhost')

def fix_identity(commit, metadata):
    if stale(commit.author_name, commit.author_email) or stale(commit.committer_name, commit.committer_email):
        commit.author_name = commit.committer_name = new_name
        commit.author_email = commit.committer_email = new_email
    lines = commit.message.split(b'\n')
    kept = [l for l in lines if not l.lower().startswith(b'co-authored-by: cursor')]
    if kept != lines:
        commit.message = b'\n'.join(kept).rstrip(b'\n') + b'\n'

with open(attack_paths_file, encoding='utf-8') as fh:
    path_args = [arg for line in fh if line.strip() for arg in ('--path', line.strip())]

args = fr.FilteringOptions.parse_args(['--force', '--invert-paths', '--replace-text', replacements] + path_args)
fr.RepoFilter(args, commit_callback=fix_identity).run()
'@)
    [IO.File]::WriteAllText($attackPaths, (($script:AttackPaths) -join "`n") + "`n")
    # Order matters: the repository path first, then any other profile path. The profile path is read at
    # run time so that this script never contains it (filter-repo would otherwise rewrite its own rules).
    # One backslash (C:\Users\you a forward slash (C:/Users/you and the doubled
    # backslash written inside source strings (C:\Users\you A bare single-backslash
    # rule does not match the other two, so local paths were surviving the rewrite.
    [IO.File]::WriteAllText($replacements, (@(
        "$(Join-Path $env:USERPROFILE 'Jig')==>jig"
        'regex:[A-Za-z]:\\Users\\[^\\/\s"''<>]+==>C:\Users\you'
        'regex:[A-Za-z]:/Users/[^/\s"''<>]+==>C:/Users/you'
        'regex:[A-Za-z]:\\\\Users\\\\[^\\\\/\s"''<>]+==>C:\Users\you'
    ) -join "`n") + "`n")
    Push-Location $RepoPath
    try {
        Run $Python $driver $NewName $NewEmail $replacements $attackPaths | Out-Null
    } finally { Pop-Location }
    Remove-Item -Recurse -Force $work

    $idents = @(Run git -C $RepoPath log --all --format='%an <%ae>%n%cn <%ce>' | Sort-Object -Unique -CaseSensitive)
    Write-Host '    Author and committer identities after the rewrite:'
    $idents | ForEach-Object { Write-Host "      $_" }
    if ($idents.Count -ne 1 -or $idents[0] -cne $NewIdent) {
        Fail "the history must contain exactly one author and committer identity, '$NewIdent', but has the $($idents.Count) listed above."
    }
    $trailers = @(Run git -C $RepoPath log --all -i --grep='^Co-authored-by: Cursor' --format='%h')
    if ($trailers.Count -gt 0) {
        Fail "$($trailers.Count) commit(s) still carry a 'Co-authored-by: Cursor' trailer after the rewrite: $($trailers -join ', ')"
    }
    $count = (Run git -C $RepoPath rev-list --all --count).Trim()
    Ok "rewrote history: $count commits, all authored and committed as $NewIdent"
}

# --- Attack-content verification -----------------------------------------------------------
# Fails publishing if any removed attack path, or any distinctive marker from those files, survives the
# rewrite anywhere in the history. Run this straight after Invoke-Rewrite, before anything is pushed.
function Assert-NoAttackContent([string]$RepoPath) {
    Step "Verifying that no authored attack content remains in $RepoPath"
    foreach ($p in $script:AttackPaths) {
        # A bare -- is dropped by PowerShell before Run sees it, so Git would read the path as a revision.
        $hits = @(Run git -C $RepoPath log --all --oneline '--' $p)
        if ($hits) { Fail "attack path '$p' still appears in history ($($hits.Count) commit(s), e.g. $($hits[0]))." }
    }
    Ok "none of the $($script:AttackPaths.Count) removed attack paths appear in any commit"

    $revs = @(Run git -C $RepoPath rev-list --all)
    foreach ($m in $script:AttackMarkers) {
        # Quoted so PowerShell keeps the separator. This file names the markers, so it is not a hit.
        $grep = Try-Run git -C $RepoPath grep -I -l -F $m @revs '--' . ':(exclude)scripts/publish.ps1'
        if ($grep.Code -gt 1) { Fail "git grep for marker '$m' failed (exit code $($grep.Code)): $($grep.Output)" }
        if ($grep.Code -eq 0 -and $grep.Output.Trim()) { Fail "attack marker '$m' still appears in history:`n      $($grep.Output -replace "`n", "`n      ")" }
    }
    Ok "none of the attack markers ($($script:AttackMarkers -join ', ')) appear in any blob"
}

# --- (d) Secret and privacy scan ------------------------------------------------------------
function Invoke-Scan([string]$RepoPath, [string]$RevRange = '') {
    # A later release passes origin/main..HEAD. The first publish leaves this empty and scans every commit.
    $scope = if ($RevRange) { $RevRange } else { 'every commit' }
    Step "(d) Scanning $scope in $RepoPath"
    $report = Join-Path ([IO.Path]::GetTempPath()) "jig-gitleaks-$([guid]::NewGuid().ToString('N').Substring(0, 8)).json"
    $logOpts = if ($RevRange) { $RevRange } else { '--all' }
    $scan = Try-Run $script:Gitleaks git $RepoPath "--log-opts=$logOpts" --redact --no-banner --report-format json --report-path $report --exit-code 3
    if ($scan.Code -eq 3) {
        Write-Host $scan.Output
        Fail "gitleaks found possible secrets. Redacted report: $report"
    }
    if ($scan.Code -ne 0) { Fail "gitleaks failed (exit code $($scan.Code)): $($scan.Output)" }
    Remove-Item $report -ErrorAction SilentlyContinue
    Ok "gitleaks: no leaks in $scope"

    $problems = @()

    $forbidden = '(^|/)(\.env(\..*)?|api-token|[^/]*\.token|[^/]*\.(db|db-wal|db-shm|sqlite3?|pem|key|gguf|safetensors|onnx|pt|pth|ckpt))$|(^|/)(data|sandbox|demo-output|promo|\.venv|venv|models|workspace)/'
    $logArgs = if ($RevRange) { @('log', $RevRange, '--name-only', '--format=') } else { @('log', '--all', '--name-only', '--format=') }
    $paths = @(Run git -C $RepoPath @logArgs | Where-Object { $_ } | Sort-Object -Unique)
    # research/models is the model catalogue (a script, a toml and a lock file), not a directory of weights.
    $bad = @($paths | Where-Object { $_ -match $forbidden -and $_ -notmatch '^research/models/' })
    if ($bad) { $problems += "sensitive paths: $($bad -join ', ')" } else { Ok "no data, token, database, env, vault, sandbox, promo or model files in $($paths.Count) paths" }

    $revArgs = if ($RevRange) { @('rev-list', $RevRange) } else { @('rev-list', '--all') }
    $revs = @(Run git -C $RepoPath @revArgs)
    if ($RevRange -and -not $revs) { Fail "there are no commits in $RevRange." }
    $hits = Try-Run git -C $RepoPath grep -I -n -E '[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z]' @revs
    if ($hits.Code -gt 1) { Fail "git grep failed (exit code $($hits.Code)): $($hits.Output)" }
    $userPaths = ($hits.Output -split "`n") | Where-Object { $_ -and $_ -notmatch 'C:[\\/]+Users[\\/]+you\b' }
    if ($userPaths) { $problems += "local profile paths:`n      $($userPaths -join "`n      ")" } else { Ok 'no local profile paths' }

    $objectArgs = if ($RevRange) { @('rev-list', $RevRange, '--objects') } else { @('rev-list', '--all', '--objects') }
    $objects = Run git -C $RepoPath @objectArgs
    $sizes = $objects | git -C $RepoPath cat-file '--batch-check=%(objecttype) %(objectsize) %(rest)'
    if ($LASTEXITCODE -ne 0) { Fail "git cat-file exited with code $LASTEXITCODE" }
    $big = $sizes | Where-Object { $_ -match '^blob (\d+) ' -and [int64]$Matches[1] -gt $MaxBlobBytes }
    if ($big) { $problems += "blobs over $($MaxBlobBytes / 1MB) MB: $($big -join ', ')" } else { Ok "no blobs over $($MaxBlobBytes / 1MB) MB" }

    return $problems
}

# --- Installer and release ------------------------------------------------------------------
function Find-InnoSetup([string]$Given) {
    if ($Given) {
        if (-not (Test-Path (Join-Path $Given 'ISCC.exe'))) { Fail "-InnoSetup '$Given' has no ISCC.exe." }
        return (Resolve-Path $Given).Path
    }
    $iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if ($iscc) { return (Split-Path $iscc.Source) }
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6')
        (Join-Path $env:ProgramFiles 'Inno Setup 6')
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6')
    ) + @(Get-ChildItem (Join-Path $env:USERPROFILE 'tools') -Directory -Filter 'innosetup-*' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | ForEach-Object { $_.FullName })
    foreach ($dir in $candidates) {
        if ($dir -and (Test-Path (Join-Path $dir 'ISCC.exe'))) { return $dir }
    }
    Fail 'Inno Setup 6 (ISCC.exe) was not found on PATH or in the usual folders. Install it (winget install JRSoftware.InnoSetup) or pass -InnoSetup <folder>.'
}

# The version in pyproject.toml, after checking that jig/__init__.py and compose.yaml's image tags agree
# (the container workflow refuses a tag otherwise).
function Get-Version([string]$Python, [string]$RepoPath) {
    $pyproject = Join-Path $RepoPath 'pyproject.toml'
    $version = (Run $Python -c "import tomllib, sys; print(tomllib.load(open(sys.argv[1], 'rb'))['project']['version'])" $pyproject).Trim()
    # PEP 440: a final X.Y.Z, or a pre-release X.Y.ZaN / X.Y.ZbN / X.Y.ZrcN (this beta is 0.1.0b1).
    # The same string is the git tag without the leading v, and the GHCR tag. It is not SemVer
    # (0.1.0-beta.1 is not valid Python), so the container workflow also emits it as a raw tag.
    if ($version -notmatch '^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$') {
        Fail "pyproject.toml's version '$version' is not X.Y.Z or a PEP 440 pre-release (X.Y.ZaN, X.Y.ZbN or X.Y.ZrcN)."
    }
    $init = Get-Content -Raw (Join-Path $RepoPath 'jig\__init__.py')
    if ($init -notmatch "(?m)^__version__ = `"$([regex]::Escape($version))`"\s*$") { Fail "jig/__init__.py's __version__ is not $version." }
    $compose = Get-Content -Raw (Join-Path $RepoPath 'compose.yaml')
    foreach ($image in 'jig', 'jig-sandbox') {
        if (-not $compose.Contains("ghcr.io/$($script:Owner)/$($image):$version}")) { Fail "compose.yaml's default $image image tag is not $version." }
    }
    return $version
}

# Builds JigSetup-<version>.exe from a git archive of $RepoPath's HEAD (no untracked or ignored files), in a
# separate PowerShell process so this script's strict mode does not apply to installer\build.ps1.
function Build-Installer([string]$RepoPath, [string]$Version, [string]$Python, [string]$Inno) {
    Step "(e) Building the Windows installer from a clean export of $RepoPath"
    $work = Join-Path ([IO.Path]::GetTempPath()) "jig-installer-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    $src = Join-Path $work 'src'
    $out = Join-Path $work 'release'
    New-Item -ItemType Directory -Path $src, $out | Out-Null
    $commit = (Run git -C $RepoPath rev-parse HEAD).Trim()
    Run git -C $RepoPath archive --format=zip --output (Join-Path $work 'src.zip') $commit | Out-Null
    Expand-Archive (Join-Path $work 'src.zip') -DestinationPath $src
    Ok "exported $commit to $src"

    $shell = (Get-Process -Id $PID).Path
    & $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $src 'installer\build.ps1') `
        -InnoSetup $Inno -Python $Python -Cache (Join-Path $script:RepoRoot 'build\installer-cache') | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "installer\build.ps1 exited with code $LASTEXITCODE." }

    $name = "JigSetup-$Version.exe"
    $built = Join-Path $src "dist\$name"
    if (-not (Test-Path $built)) { Fail "installer\build.ps1 finished but $built does not exist." }
    $exe = Join-Path $out $name
    Move-Item $built $exe
    Remove-Item -Recurse -Force $src, (Join-Path $work 'src.zip')
    $hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    $sumFile = "$exe.sha256"
    [IO.File]::WriteAllText($sumFile, "$hash  $name`n", (New-Object Text.UTF8Encoding $false))
    $size = (Get-Item $exe).Length
    Ok "built $name ($([math]::Round($size / 1MB, 1)) MB), SHA-256 $hash"
    return [pscustomobject]@{ Exe = $exe; SumFile = $sumFile; Name = $name; Hash = $hash; Size = $size; Commit = $commit; Dir = $out }
}

function Test-PreRelease([string]$Version) {
    return $Version -match '(?:a|b|rc)\d+$'
}

function Get-ReleaseNotes([string]$Version, $Installer) {
    $name = $Installer.Name
    $beta = if (Test-PreRelease $Version) { " This is a beta." } else { "" }
    return @"
Jig $Version for Windows (64-bit).$beta

Download **$name** and run it. It needs no administrator rights, and no Python, Git or terminal. At the end, Jig opens in your browser on its set-up page, where you choose a model.

When a newer release is published, open **Settings**, then **About and updates**, and choose **Check for updates**. The same check is on the tray icon. Jig checks only when you ask. On Windows it can then download that release's installer, check the file against the SHA-256 published beside it, turn itself off, and run the installer. Your settings and data stay on this computer. A checkout or a container is not updated this way: use ``git pull`` and ``pip install -e .``, or pull the new image tag.

The installer isn't code-signed yet, so Windows will probably warn you. If your browser says the file isn't commonly downloaded, choose **Keep**. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**. Only do this for the file from this page.

SHA-256 of $($name):

``````
$($Installer.Hash)
``````

The same value is in $name.sha256. To check your download in PowerShell: ``Get-FileHash .\$name -Algorithm SHA256``

There is no installer for macOS or Linux yet: install from the repository, as the README's quick start describes.
"@
}

function Publish-Release([string]$Version, $Installer) {
    $tag = "v$Version"
    Step "(i) Tagging $tag and creating the release with the installer"
    $head = (Run git rev-parse HEAD).Trim()
    if ($head -ne $Installer.Commit) { Fail "HEAD is $head, but the installer was built from $($Installer.Commit)." }
    Run git -c "user.name=$NewName" -c "user.email=$NewEmail" tag -a $tag -m "Jig $Version" $head | Out-Null
    Run git push --quiet origin "refs/tags/$tag" | Out-Null
    Ok "pushed tag $tag ($head); the container workflow builds the images from it"

    $notes = Join-Path $Installer.Dir 'release-notes.md'
    [IO.File]::WriteAllText($notes, (Get-ReleaseNotes $Version $Installer), (New-Object Text.UTF8Encoding $false))
    $releaseArgs = @('release', 'create', $tag, $Installer.Exe, $Installer.SumFile, '--repo', "$Owner/$Repo",
        '--title', "Jig $Version", '--notes-file', $notes, '--verify-tag', '--latest')
    if (Test-PreRelease $Version) { $releaseArgs += '--prerelease' }
    Run $script:Gh @releaseArgs | Out-Null

    $check = Join-Path $Installer.Dir 'downloaded'
    New-Item -ItemType Directory -Path $check | Out-Null
    Run $script:Gh release download $tag --repo "$Owner/$Repo" --pattern $Installer.Name --dir $check | Out-Null
    $got = (Get-FileHash (Join-Path $check $Installer.Name) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -ne $Installer.Hash) { Fail "the installer downloaded from the release has SHA-256 $got, not $($Installer.Hash)." }
    $assets = @(Run $script:Gh release view $tag --repo "$Owner/$Repo" --json assets --jq '.assets[].name')
    foreach ($want in $Installer.Name, "$($Installer.Name).sha256") {
        if ($assets -notcontains $want) { Fail "the release has no asset named $want (it has: $($assets -join ', '))." }
    }
    Ok "https://github.com/$Owner/$Repo/releases/tag/$tag has $($assets -join ' and '); the download matches"
}

# A later release of the repository that already exists. No history rewrite and no force-push.
# Scans only the commits that origin/main does not have yet, then pushes main and the new tag.
function Assert-Subsequent {
    Step 'Checking this is a later release of the existing repository'
    $branch = (Run git rev-parse --abbrev-ref HEAD).Trim()
    if ($branch -ne 'main') { Fail "the current branch is '$branch', not main." }
    $status = Run git status --porcelain --untracked-files=all
    if ($status) {
        $status | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
        Fail 'the working tree has uncommitted or untracked changes (listed above). Commit or remove them first.'
    }
    Ok 'working tree is clean on main'
    $url = (Run git remote get-url origin).Trim()
    if ($url -notmatch 'github\.com[:/]rlesueur/jig(\.git)?$') { Fail "origin is '$url', not github.com/rlesueur/jig." }
    Ok "origin is $url"
    Run git fetch --quiet origin main | Out-Null
    $remote = (Run git rev-parse origin/main).Trim()
    $head = (Run git rev-parse HEAD).Trim()
    Run git merge-base --is-ancestor $remote $head | Out-Null
    if ($remote -eq $head) { Fail 'HEAD is already origin/main, so there is nothing new to publish.' }
    Ok "origin/main ($remote) is an ancestor of HEAD ($head). The push is a fast-forward."
}

function Wait-Workflow([string]$Workflow, [string]$Head, [string]$Label) {
    $runId = $null
    for ($i = 0; $i -lt 36 -and -not $runId; $i++) {
        $listed = (Run $script:Gh run list --repo "$Owner/$Repo" --workflow $Workflow --commit $Head --limit 1 --json databaseId --jq '.[0].databaseId')
        $text = if ($listed -is [array]) { "$($listed[0])" } else { "$listed" }
        $text = $text.Trim()
        if ($text -and $text -ne 'null') { $runId = $text }
        if (-not $runId) { Start-Sleep -Seconds 5 }
    }
    if (-not $runId) { Fail "$Label did not start within three minutes. Check the Actions tab." }
    Run $script:Gh run watch $runId --repo "$Owner/$Repo" --exit-status | Out-Null
    Ok "$Label run $runId succeeded"
    return $runId
}

function Test-AnonymousDownload([string]$Version, $Installer) {
    Step 'Downloading the installer with no GitHub credentials'
    $url = "https://github.com/$Owner/$Repo/releases/download/v$Version/$($Installer.Name)"
    $dest = Join-Path $Installer.Dir "anonymous-$($Installer.Name)"
    $previous = $env:GH_TOKEN
    $hadGh = Test-Path Env:GH_TOKEN
    $hadHub = Test-Path Env:GITHUB_TOKEN
    $previousHub = $env:GITHUB_TOKEN
    Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $dest
    } finally {
        if ($hadGh) { $env:GH_TOKEN = $previous }
        if ($hadHub) { $env:GITHUB_TOKEN = $previousHub }
    }
    $got = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -ne $Installer.Hash) { Fail "the anonymous download has SHA-256 $got, not $($Installer.Hash)." }
    Ok "anonymous download of $($Installer.Name) matches $got"
}

function Test-AnonymousImages([string]$Version) {
    Step 'Pulling the container images with no registry credentials'
    $config = Join-Path ([IO.Path]::GetTempPath()) "jig-docker-anon-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    New-Item -ItemType Directory -Path $config | Out-Null
    $previous = $env:DOCKER_CONFIG
    $had = Test-Path Env:DOCKER_CONFIG
    $env:DOCKER_CONFIG = $config
    try {
        foreach ($image in 'jig', 'jig-sandbox') {
            $ref = "ghcr.io/$Owner/${image}:$Version"
            Run docker pull $ref | Out-Null
            Ok "pulled $ref with an empty Docker config"
        }
    } finally {
        if ($had) { $env:DOCKER_CONFIG = $previous } else { Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue }
        Remove-Item -Recurse -Force $config
    }
}

function Publish-Subsequent([string]$RepoPath) {
    Assert-Subsequent
    $inno = Find-InnoSetup $InnoSetup
    Ok "Inno Setup: $inno"
    $python = Get-FilterRepo
    $version = Get-Version $python $RepoPath
    $tag = "v$version"
    if (Run git tag --list $tag) { Fail "the tag $tag already exists in this repository." }
    $remoteTag = Try-Run git ls-remote --tags origin "refs/tags/$tag"
    if ($remoteTag.Code -ne 0) { Fail "could not check origin for tag ${tag}: $($remoteTag.Output)" }
    if ($remoteTag.Output.Trim()) { Fail "the tag $tag already exists on origin." }
    Ok "version $version agrees in pyproject.toml, jig/__init__.py and compose.yaml; tag $tag is free"

    $range = 'origin/main..HEAD'
    $problems = @(Invoke-Scan $RepoPath $range)
    if ($problems) {
        $problems | ForEach-Object { Write-Host "    FOUND  $_" -ForegroundColor Red }
        Fail 'the scan of the new commits found problems (listed above). Nothing was pushed.'
    }
    $installer = Build-Installer $RepoPath $version $python $inno
    if ($DryRun) {
        Step 'Dry run: the installer is built. Nothing will be pushed.'
        Write-Host "    SHA-256: $($installer.Hash)"
        Write-Host "    Built files, kept for testing: $($installer.Dir)"
        return
    }

    Step '(f) Pushing main. This is a normal push, not a force-push.'
    Run git push origin HEAD:main | Out-Null
    $pushed = (Run git rev-parse origin/main).Trim()
    if ($pushed -ne $installer.Commit) { Fail "origin/main is $pushed after the push, not $($installer.Commit)." }
    Ok "origin/main is $($installer.Commit)"

    Publish-Release $version $installer
    Test-AnonymousDownload $version $installer

    Step 'Waiting for Pages and the container images'
    $pages = Wait-Workflow 'pages.yml' $installer.Commit 'Pages'
    $containers = Wait-Workflow 'container.yml' $installer.Commit 'Container images'
    $live = (Run $script:Gh api "repos/$Owner/$Repo/pages" --jq '.html_url').Trim()
    $linked = $false
    for ($i = 0; $i -lt 24 -and -not $linked; $i++) {
        try {
            $page = Invoke-WebRequest -UseBasicParsing -Uri $live
            $linked = ($page.StatusCode -eq 200 -and $page.Content -match [regex]::Escape("releases/tag/$tag"))
        } catch { $linked = $false }
        if (-not $linked) { Start-Sleep -Seconds 5 }
    }
    if (-not $linked) { Fail "$live did not return HTTP 200 with a link to $tag within two minutes." }
    Ok "$live is HTTP 200 and links to $tag"
    Test-AnonymousImages $version

    Write-Host "`nPublished $tag."
    Write-Host "  Release:    https://github.com/$Owner/$Repo/releases/tag/$tag"
    Write-Host "  Installer:  $($installer.Name)"
    Write-Host "  SHA-256:    $($installer.Hash)"
    Write-Host "  Pages run:  $pages"
    Write-Host "  Images run: $containers"
}

# --- Main -----------------------------------------------------------------------------------
$repoRoot = (Run git rev-parse --show-toplevel).Trim()
$script:RepoRoot = $repoRoot
Set-Location $repoRoot
Write-Host "Repository: $repoRoot"
Write-Host "Target:     github.com/$Owner/$Repo (public), Pages at $PagesUrl"
if ($DryRun) { Write-Host 'Mode:       DRY RUN (no changes to this repository or GitHub)' -ForegroundColor Yellow }

$script:Gh = Find-Tool 'gh' 'GitHub.cli' 'Install it with: winget install GitHub.cli'
$script:Gitleaks = Find-Tool 'gitleaks' 'Gitleaks.Gitleaks' 'Install it with: winget install Gitleaks.Gitleaks'

Assert-Account
if ($Subsequent) {
    Publish-Subsequent $repoRoot
    return
}
Assert-Clean
$inno = Find-InnoSetup $InnoSetup
Ok "Inno Setup: $inno"
$python = Get-FilterRepo
$version = Get-Version $python $repoRoot
$tag = "v$version"
if (Run git tag --list $tag) { Fail "the tag $tag already exists in this repository." }
Ok "version $version agrees in pyproject.toml, jig/__init__.py and compose.yaml; tag $tag is free"

Step '(c) Rewriting commit authors and local paths'
if ($DryRun) {
    $clone = Join-Path ([IO.Path]::GetTempPath()) "jig-publish-dryrun-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    Run git clone --quiet --no-local $repoRoot $clone | Out-Null
    Write-Host "    Rehearsing on a throwaway clone: $clone"
    Invoke-Rewrite $python $clone
    Assert-NoAttackContent $clone
    $problems = @(Invoke-Scan $clone)
    if ($problems) {
        Remove-Item -Recurse -Force $clone
        $problems | ForEach-Object { Write-Host "    FOUND  $_" -ForegroundColor Red }; Fail 'the rehearsal scan found problems (listed above).'
    }
    $installer = Build-Installer $clone $version $python $inno
    Remove-Item -Recurse -Force $clone

    Step "Release that would be published (steps (f) to (k) not run)"
    Write-Host "    Tag:      $tag (annotated, by $NewIdent), on the rewritten HEAD $($installer.Commit)"
    $kind = if (Test-PreRelease $version) { 'marked latest, and as a pre-release' } else { 'marked latest' }
    Write-Host "    Release:  'Jig $version' on https://github.com/$Owner/$Repo/releases/tag/$tag, $kind"
    Write-Host "    Assets:   $($installer.Name) ($($installer.Size) bytes)"
    Write-Host "              $($installer.Name).sha256"
    Write-Host "    SHA-256:  $($installer.Hash)"
    Write-Host "    Built files, kept for testing: $($installer.Dir)"
    Write-Host '    Release notes:'
    (Get-ReleaseNotes $version $installer) -split "`n" | ForEach-Object { Write-Host "      $_" }
    Write-Host "`nDry run passed. Nothing was pushed or published. Run without -DryRun to publish." -ForegroundColor Green
    return
}

$bundle = Join-Path ([IO.Path]::GetTempPath()) "jig-before-publish-$(Get-Date -Format yyyyMMdd-HHmmss).bundle"
Run git bundle create --quiet $bundle --all | Out-Null
Ok "original history saved to $bundle"
Invoke-Rewrite $python $repoRoot
Assert-NoAttackContent $repoRoot
$problems = @(Invoke-Scan $repoRoot)
if ($problems) { $problems | ForEach-Object { Write-Host "    FOUND  $_" -ForegroundColor Red }; Fail "the scan found problems (listed above). Nothing was pushed. The original history is in $bundle." }
$installer = Build-Installer $repoRoot $version $python $inno

Step "(f) Creating github.com/$Owner/$Repo (public) and pushing main"
Run $script:Gh repo create "$Owner/$Repo" --public --source . --remote origin --push --description $Description --homepage $PagesUrl
$ownerNow = (Run $script:Gh repo view "$Owner/$Repo" --json owner --jq '.owner.login').Trim()
if ($ownerNow -ne $Owner) { Fail "the new repository is owned by '$ownerNow', not $Owner. Check it on GitHub now." }
Ok "created https://github.com/$Owner/$Repo"

Step '(g) Setting topics'
Run $script:Gh repo edit "$Owner/$Repo" --add-topic ($Topics -join ',') | Out-Null
Ok ($Topics -join ', ')

Step '(h) Enabling GitHub Pages (GitHub Actions) and private vulnerability reporting'
Run $script:Gh api -X POST "repos/$Owner/$Repo/pages" -f build_type=workflow | Out-Null
$buildType = (Run $script:Gh api "repos/$Owner/$Repo/pages" --jq '.build_type').Trim()
if ($buildType -ne 'workflow') { Fail "Pages build type is '$buildType', not 'workflow'." }
Ok 'Pages source: GitHub Actions'
Run $script:Gh api -X PUT "repos/$Owner/$Repo/private-vulnerability-reporting" | Out-Null
Ok 'private vulnerability reporting enabled (used by SECURITY.md)'

# Before the Pages wait, so the site's "latest GitHub release" link works by the time the site is live.
Publish-Release $version $installer

Step '(j) Waiting for the Pages workflow'
$head = (Run git rev-parse HEAD).Trim()
$runId = $null
for ($i = 0; $i -lt 30 -and -not $runId; $i++) {
    $runId = Run $script:Gh run list --repo "$Owner/$Repo" --workflow pages.yml --commit $head --limit 1 --json databaseId --jq '.[0].databaseId'
    if (-not $runId) { Start-Sleep -Seconds 4 }
}
if (-not $runId) { Fail 'the Pages workflow did not start within two minutes of the push. Check the Actions tab.' }
$watch = Try-Run $script:Gh run watch $runId --repo "$Owner/$Repo" --exit-status
if ($watch.Code -ne 0) {
    # The push can start the workflow before Pages is enabled, in which case it fails at configure-pages. Rerun it once now that Pages exists.
    Write-Host '    The first run failed (it can start before Pages is enabled). Rerunning it once.' -ForegroundColor Yellow
    Run $script:Gh run rerun $runId --repo "$Owner/$Repo" | Out-Null
    Start-Sleep -Seconds 5
    Run $script:Gh run watch $runId --repo "$Owner/$Repo" --exit-status | Out-Null
}
Ok "workflow run $runId succeeded"
$live = (Run $script:Gh api "repos/$Owner/$Repo/pages" --jq '.html_url').Trim()
$status = 0
for ($i = 0; $i -lt 24 -and $status -ne 200; $i++) {
    try { $status = (Invoke-WebRequest -UseBasicParsing -Uri $live -Method Head).StatusCode } catch { Start-Sleep -Seconds 5 }
}
if ($status -ne 200) { Fail "$live did not return HTTP 200 within two minutes." }
Ok "live: $live"

Step '(k) Verifying the owner'
$info = Run $script:Gh repo view "$Owner/$Repo" --json owner,visibility,url,homepageUrl | ConvertFrom-Json
$ownerType = (Run $script:Gh api "repos/$Owner/$Repo" --jq '.owner.type').Trim()
if ($info.owner.login -ne $Owner -or $ownerType -ne 'User') { Fail "owner is '$($info.owner.login)' ($ownerType), not the personal account $Owner." }
if ($info.visibility -ne 'PUBLIC') { Fail "visibility is $($info.visibility), not PUBLIC." }
Ok "$($info.url) is public and owned by the personal account $Owner"

Write-Host "`nPublished."
Write-Host "  Repository: $($info.url)"
Write-Host "  Pages:      $live"
Write-Host "  Release:    https://github.com/$Owner/$Repo/releases/tag/$tag ($($installer.Name), SHA-256 $($installer.Hash))"
Write-Host "  Backup of the pre-rewrite history: $bundle"
