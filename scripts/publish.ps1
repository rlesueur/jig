<#
.SYNOPSIS
    Publishes Jig as a public GitHub repository under the personal account rlesueur, with GitHub Pages.

.DESCRIPTION
    Run this once, from the repository root, when all work is committed. It:
      a. checks that the GitHub CLI is signed in as rlesueur (never an organisation);
      b. checks that the working tree is clean, the branch is main and nothing has been published yet;
      c. sets the author and committer of every commit whose author or committer is 'Jig <jig@localhost>'
         or has the name 'rlesueur' to 'Robyn Le Sueur <2302916+rlesueur@users.noreply.github.com>' with
         git filter-repo, replaces local absolute paths (C:\Users\<name>\...) in the history with portable
         ones, and in the same pass strips the self-authored attack content (listed in $AttackPaths) from
         every commit, then stops unless that is the only author and committer identity left in the history;
         a bundle of the original history is saved first;
      d. verifies that no removed attack path and no distinctive marker from those files survives anywhere
         in the rewritten history, then scans it (gitleaks plus explicit checks) and stops on any finding;
      e. creates github.com/rlesueur/<repo> as a public repository and pushes main;
      f. sets the topics;
      g. enables GitHub Pages with GitHub Actions as the source, and private vulnerability reporting;
      h. waits for the Pages workflow and prints the live URL;
      i. verifies that the repository's owner is the user account rlesueur.

    With -DryRun it only runs the non-destructive checks: (a), (b), and (c) plus the attack-content
    verification and (d) on a throwaway clone in the temporary folder. The real repository, its history
    and GitHub are not touched.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\publish.ps1 -DryRun
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\publish.ps1
#>
[CmdletBinding()]
param(
    [string]$Repo = 'jig',
    [switch]$DryRun
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
# any blob; these are plain source identifiers, not attack text.
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

with open(attack_paths_file, encoding='utf-8') as fh:
    path_args = [arg for line in fh if line.strip() for arg in ('--path', line.strip())]

args = fr.FilteringOptions.parse_args(['--force', '--invert-paths', '--replace-text', replacements] + path_args)
fr.RepoFilter(args, commit_callback=fix_identity).run()
'@)
    [IO.File]::WriteAllText($attackPaths, (($script:AttackPaths) -join "`n") + "`n")
    # Order matters: the repository path first, then any other profile path. The profile path is read at
    # run time so that this script never contains it (filter-repo would otherwise rewrite its own rules).
    [IO.File]::WriteAllText($replacements, (@(
        "$(Join-Path $env:USERPROFILE 'Jig')==>jig"
        'regex:[A-Za-z]:\\Users\\[^\\/\s"''<>]+==>C:\Users\you'
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
    $count = (Run git -C $RepoPath rev-list --all --count).Trim()
    Ok "rewrote history: $count commits, all authored and committed as $NewIdent"
}

# --- Attack-content verification -----------------------------------------------------------
# Fails publishing if any removed attack path, or any distinctive marker from those files, survives the
# rewrite anywhere in the history. Run this straight after Invoke-Rewrite, before anything is pushed.
function Assert-NoAttackContent([string]$RepoPath) {
    Step "Verifying that no authored attack content remains in $RepoPath"
    foreach ($p in $script:AttackPaths) {
        $hits = @(Run git -C $RepoPath log --all --oneline -- $p)
        if ($hits) { Fail "attack path '$p' still appears in history ($($hits.Count) commit(s), e.g. $($hits[0]))." }
    }
    Ok "none of the $($script:AttackPaths.Count) removed attack paths appear in any commit"

    $revs = @(Run git -C $RepoPath rev-list --all)
    foreach ($m in $script:AttackMarkers) {
        $grep = Try-Run git -C $RepoPath grep -I -l -F $m @revs
        if ($grep.Code -gt 1) { Fail "git grep for marker '$m' failed (exit code $($grep.Code)): $($grep.Output)" }
        if ($grep.Code -eq 0 -and $grep.Output.Trim()) { Fail "attack marker '$m' still appears in history:`n      $($grep.Output -replace "`n", "`n      ")" }
    }
    Ok "none of the attack markers ($($script:AttackMarkers -join ', ')) appear in any blob"
}

# --- (d) Secret and privacy scan ------------------------------------------------------------
function Invoke-Scan([string]$RepoPath) {
    Step "(d) Scanning the history of $RepoPath"
    $report = Join-Path ([IO.Path]::GetTempPath()) "jig-gitleaks-$([guid]::NewGuid().ToString('N').Substring(0, 8)).json"
    $scan = Try-Run $script:Gitleaks git $RepoPath --log-opts=--all --redact --no-banner --report-format json --report-path $report --exit-code 3
    if ($scan.Code -eq 3) {
        Write-Host $scan.Output
        Fail "gitleaks found possible secrets. Redacted report: $report"
    }
    if ($scan.Code -ne 0) { Fail "gitleaks failed (exit code $($scan.Code)): $($scan.Output)" }
    Remove-Item $report -ErrorAction SilentlyContinue
    Ok 'gitleaks: no leaks in any commit'

    $problems = @()

    $forbidden = '(^|/)(\.env(\..*)?|api-token|[^/]*\.token|[^/]*\.(db|db-wal|db-shm|sqlite3?|pem|key|gguf|safetensors|onnx|pt|pth|ckpt))$|(^|/)(data|sandbox|demo-output|promo|\.venv|venv|models|workspace)/'
    $paths = Run git -C $RepoPath log --all --name-only --format= | Where-Object { $_ } | Sort-Object -Unique
    $bad = $paths | Where-Object { $_ -match $forbidden }
    if ($bad) { $problems += "sensitive paths in history: $($bad -join ', ')" } else { Ok "no data, token, database, env, vault, sandbox, promo or model files in $($paths.Count) paths ever committed" }

    $revs = Run git -C $RepoPath rev-list --all
    $hits = Try-Run git -C $RepoPath grep -I -n -E '[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z]' @revs
    if ($hits.Code -gt 1) { Fail "git grep failed (exit code $($hits.Code)): $($hits.Output)" }
    $userPaths = ($hits.Output -split "`n") | Where-Object { $_ -and $_ -notmatch 'C:[\\/]+Users[\\/]+you\b' }
    if ($userPaths) { $problems += "local profile paths in history:`n      $($userPaths -join "`n      ")" } else { Ok 'no local profile paths in any commit' }

    $objects = Run git -C $RepoPath rev-list --all --objects
    $sizes = $objects | git -C $RepoPath cat-file '--batch-check=%(objecttype) %(objectsize) %(rest)'
    if ($LASTEXITCODE -ne 0) { Fail "git cat-file exited with code $LASTEXITCODE" }
    $big = $sizes | Where-Object { $_ -match '^blob (\d+) ' -and [int64]$Matches[1] -gt $MaxBlobBytes }
    if ($big) { $problems += "blobs over $($MaxBlobBytes / 1MB) MB: $($big -join ', ')" } else { Ok "no blobs over $($MaxBlobBytes / 1MB) MB" }

    return $problems
}

# --- Main -----------------------------------------------------------------------------------
$repoRoot = (Run git rev-parse --show-toplevel).Trim()
Set-Location $repoRoot
Write-Host "Repository: $repoRoot"
Write-Host "Target:     github.com/$Owner/$Repo (public), Pages at $PagesUrl"
if ($DryRun) { Write-Host 'Mode:       DRY RUN (no changes to this repository or GitHub)' -ForegroundColor Yellow }

$script:Gh = Find-Tool 'gh' 'GitHub.cli' 'Install it with: winget install GitHub.cli'
$script:Gitleaks = Find-Tool 'gitleaks' 'Gitleaks.Gitleaks' 'Install it with: winget install Gitleaks.Gitleaks'

Assert-Account
Assert-Clean

Step '(c) Rewriting commit authors and local paths'
$python = Get-FilterRepo
if ($DryRun) {
    $clone = Join-Path ([IO.Path]::GetTempPath()) "jig-publish-dryrun-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    Run git clone --quiet --no-local $repoRoot $clone | Out-Null
    Write-Host "    Rehearsing on a throwaway clone: $clone"
    Invoke-Rewrite $python $clone
    Assert-NoAttackContent $clone
    $problems = @(Invoke-Scan $clone)
    Remove-Item -Recurse -Force $clone
    if ($problems) { $problems | ForEach-Object { Write-Host "    FOUND  $_" -ForegroundColor Red }; Fail 'the rehearsal scan found problems (listed above).' }
    Write-Host "`nDry run passed. Steps (e) to (i) were not run. Run without -DryRun to publish." -ForegroundColor Green
    return
}

$bundle = Join-Path ([IO.Path]::GetTempPath()) "jig-before-publish-$(Get-Date -Format yyyyMMdd-HHmmss).bundle"
Run git bundle create --quiet $bundle --all | Out-Null
Ok "original history saved to $bundle"
Invoke-Rewrite $python $repoRoot
Assert-NoAttackContent $repoRoot
$problems = @(Invoke-Scan $repoRoot)
if ($problems) { $problems | ForEach-Object { Write-Host "    FOUND  $_" -ForegroundColor Red }; Fail "the scan found problems (listed above). Nothing was pushed. The original history is in $bundle." }

Step "(e) Creating github.com/$Owner/$Repo (public) and pushing main"
Run $script:Gh repo create "$Owner/$Repo" --public --source . --remote origin --push --description $Description --homepage $PagesUrl
$ownerNow = (Run $script:Gh repo view "$Owner/$Repo" --json owner --jq '.owner.login').Trim()
if ($ownerNow -ne $Owner) { Fail "the new repository is owned by '$ownerNow', not $Owner. Check it on GitHub now." }
Ok "created https://github.com/$Owner/$Repo"

Step '(f) Setting topics'
Run $script:Gh repo edit "$Owner/$Repo" --add-topic ($Topics -join ',') | Out-Null
Ok ($Topics -join ', ')

Step '(g) Enabling GitHub Pages (GitHub Actions) and private vulnerability reporting'
Run $script:Gh api -X POST "repos/$Owner/$Repo/pages" -f build_type=workflow | Out-Null
$buildType = (Run $script:Gh api "repos/$Owner/$Repo/pages" --jq '.build_type').Trim()
if ($buildType -ne 'workflow') { Fail "Pages build type is '$buildType', not 'workflow'." }
Ok 'Pages source: GitHub Actions'
Run $script:Gh api -X PUT "repos/$Owner/$Repo/private-vulnerability-reporting" | Out-Null
Ok 'private vulnerability reporting enabled (used by SECURITY.md)'

Step '(h) Waiting for the Pages workflow'
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

Step '(i) Verifying the owner'
$info = Run $script:Gh repo view "$Owner/$Repo" --json owner,visibility,url,homepageUrl | ConvertFrom-Json
$ownerType = (Run $script:Gh api "repos/$Owner/$Repo" --jq '.owner.type').Trim()
if ($info.owner.login -ne $Owner -or $ownerType -ne 'User') { Fail "owner is '$($info.owner.login)' ($ownerType), not the personal account $Owner." }
if ($info.visibility -ne 'PUBLIC') { Fail "visibility is $($info.visibility), not PUBLIC." }
Ok "$($info.url) is public and owned by the personal account $Owner"

Write-Host "`nPublished."
Write-Host "  Repository: $($info.url)"
Write-Host "  Pages:      $live"
Write-Host "  Backup of the pre-rewrite history: $bundle"
