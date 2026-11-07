"""Human-role action library: one function per daily operation.

dev writes code and opens PRs; reviewer approves; release manager merges, tags
and publishes GitHub Releases; on-call triages signals (issue close + triage
record commit). The local pipeline (unit tests, secret scan) runs the commands
as written. Nothing here edits derived audit files; deviations happen only as
performed (or omitted) operations, disclosed per scenario story.
"""
from __future__ import annotations
import datetime as dt
import hashlib, json, os, re, sys
from pathlib import Path

from loguru import logger

from . import ghutil as G

TRIAGE_NOTE = {'RESOLVED': 'handled; stage healthy',
               'UNRESOLVED': 'acknowledged; still investigating',
               'AUTHORIZED_EXCLUDED': 'monitoring noise; excluded from scope'}


def setup_logger():
    """Console narration format (the persistent actions.log keeps plain lines)."""
    logger.remove()
    logger.add(sys.stderr, colorize=True,
               format='<green>{time:HH:mm:ss}</green> <dim>|</dim> '
                      '<cyan>{extra[actor]:>15}</cyan> <dim>|</dim> {message}',
               filter=lambda r: r['extra'].get('actor') is not None)
    logger.configure(extra={'actor': 'operator'})


SEED_DIR = Path(__file__).resolve().parent / 'seed'


def _ts():
    return dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%SZ')


class Ctx:
    """Per-scenario context inside the shared batch repository.

    Every scenario gets its own evidence namespace (evidence/<SID>/), its own
    annotated release tag (release/<SID>, tagger date = that scenario's real
    cutoff) and its own snapshot file, so scenarios can share one repository
    without cross-contaminating each other's evidence universes.
    """

    def __init__(self, repo_dir: Path, full: str, login: str, sid: str, log=print):
        # resolve() everything: relative paths would otherwise be interpreted
        # against different working directories by this process and by the
        # seed tools it invokes as subprocesses
        self.repo = repo_dir.resolve()
        self.full, self.login, self.sid, self.log = full, login, sid, log
        self.ev = (repo_dir / 'evidence' / sid).resolve()
        self.pr_no = None
        self.tag_main = f'release/{sid}'
        self.tag_target = f'release/{sid}-target'
        self.snap_main = f'snapshots/{sid}.json'
        self.snap_target = f'snapshots/{sid}-target.json'
        self.signal_issues = {}  # signal_id -> issue number (recorded at creation)
        self.log_file = None     # set by the operator; every action is appended

    def say(self, actor, action):
        """Narrate one real action: console line + persistent actions.log."""
        logger.bind(actor=actor).info(action)
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open('a', encoding='utf-8') as f:
                f.write(f'{_ts()} [{actor}] {action}\n')

    def say_block(self, actor, action, block):
        """Narrate with a multi-line detail block (diff, command output ...)."""
        self.say(actor, action)
        for ln in block.splitlines():
            logger.bind(actor=actor).info(f'<dim>{ln}</dim>')
            if self.log_file:
                with self.log_file.open('a', encoding='utf-8') as f:
                    f.write(f'         {ln}\n')


def prepare_cycle(ctx: Ctx):
    """Make the shared repository deterministic for one scenario cycle.

    Abandons leftovers of a failed previous attempt of THIS cycle (open PRs,
    stale tags/releases, partial evidence), then restores the maintained
    product baseline. On a fresh cycle this is a no-op apart from the baseline
    check.
    """
    import shutil as _shutil
    import time as _time
    # 1) abandon open PRs from this cycle's feature branches, delete the branches
    for head in (f'feat/{ctx.sid}-release', f'fix/{ctx.sid}-finding'):
        rows = json.loads(G.sh(['gh', 'pr', 'list', '--repo', ctx.full, '--state', 'open',
                                '--head', head, '--json', 'number'], cwd=ctx.repo,
                               check=False).stdout or '[]')
        for r in rows:
            G.sh(['gh', 'pr', 'close', str(r['number']), '--repo', ctx.full,
                  '--comment', 'superseded by a retry of this release cycle'], cwd=ctx.repo,
                 check=False)
        G.sh(['git', 'branch', '-D', head], cwd=ctx.repo, check=False)
        G.sh(['git', 'push', 'origin', '--delete', head], cwd=ctx.repo, check=False)
    # 2) drop stale tags/releases of this cycle
    for tag in (ctx.tag_main, ctx.tag_target):
        G.sh(['gh', 'release', 'delete', tag, '--repo', ctx.full, '--yes'],
             cwd=ctx.repo, check=False)
        G.sh(['git', 'tag', '-d', tag], cwd=ctx.repo, check=False)
        G.sh(['git', 'push', 'origin', f':refs/tags/{tag}'], cwd=ctx.repo, check=False)
    # 3) clear this cycle's partial evidence and snapshots in the worktree
    _shutil.rmtree(ctx.ev, ignore_errors=True)
    for snap in (ctx.repo / ctx.snap_main, ctx.repo / ctx.snap_target):
        snap.unlink(missing_ok=True)
    # 4) restore the product baseline (and the repo-global collection rule,
    #    which a U-cycle story may have removed)
    for rel in ('app/service.py', 'tests/test_service.py', 'COLLECTION_RULE.md'):
        target = ctx.repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((SEED_DIR / rel).read_bytes())
    G.sh(['git', 'add', '-A'], cwd=ctx.repo)
    if G.sh(['git', 'diff', '--cached', '--quiet'], cwd=ctx.repo, check=False).returncode == 0:
        G.sh(['git', 'commit', '--allow-empty', '-m',
              f'chore: prepare release cycle {ctx.sid} (baseline anchor)'], cwd=ctx.repo)
        ctx.say('maintainer', f'prepared release cycle {ctx.sid} (baseline anchor commit)')
    else:
        G.sh(['git', 'commit', '-m', f'chore: prepare release cycle {ctx.sid} '
              '(restore baseline, clear partial state)'], cwd=ctx.repo)
        ctx.say('maintainer', f'prepared release cycle {ctx.sid} (baseline restored, workspace clean)')
    G.push(ctx.repo)


def append_jsonl(path: Path, row: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, sort_keys=True) + '\n')


def read_jsonl(path: Path):
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


# ---------------------------------------------------------------- developer
def dev_feature_branch(ctx: Ctx, kind: str = 'version-bump'):
    """Developer edits app/service.py on a feature branch and opens a PR.

    kind: version-bump (normal), plant-credential (N02: scan finding),
    break-health (B01: regression caught by the unit suite).
    """
    branch = G.git_out(ctx.repo, 'rev-parse', '--abbrev-ref', 'HEAD')
    ctx.say('dev', f'working on branch {branch}')
    svc = ctx.repo / 'app/service.py'
    text = svc.read_text()
    m = re.search(r"VERSION = '(\d+)\.(\d+)\.(\d+)'", text)
    bumped = f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"
    text = re.sub(r"VERSION = '[\d.]+'", f"VERSION = '{bumped}'", text)
    if kind == 'version-bump':
        title = f'feat: expose version {bumped} on /health and /version'
    elif kind == 'plant-credential':
        text += ("\n\n# inventory integration\n"
                 "INVENTORY_AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n")
        title = f'feat: expose version {bumped} and wire inventory integration'
    elif kind == 'break-health':
        text = text.replace('return 503 if broken else 200', 'return 500 if broken else 200')
        title = f'feat: expose version {bumped} (health status mapping update)'
    else:
        raise ValueError(kind)
    svc.write_text(text)
    ctx.say_block('dev', 'editing app/service.py — actual diff:', _diff_preview(ctx))
    sha = G.commit_all(ctx.repo, f'feat: prepare release {bumped}')
    G.push(ctx.repo, 'HEAD')
    ctx.say('dev', f"committed {sha} 'feat: prepare release {bumped}' and pushed")
    return bumped


def _diff_preview(ctx: Ctx, limit=18):
    diff = G.git_out(ctx.repo, 'diff', '--unified=0', '--', 'app', 'tests')
    lines = [l for l in diff.splitlines()
             if l.startswith(('diff ', '@@', '+', '-')) and not l.startswith(('+++', '---'))]
    return '\n'.join(lines[:limit]) or '(no textual diff)'


def dev_open_pr(ctx: Ctx, title: str, body: str):
    branch = G.git_out(ctx.repo, 'rev-parse', '--abbrev-ref', 'HEAD')
    G.sh(['git', 'push', 'origin', branch], cwd=ctx.repo)
    url = G.sh(['gh', 'pr', 'create', '--base', 'main', '--head', branch,
                '--title', title, '--body', body], cwd=ctx.repo).stdout.strip()
    ctx.pr_no = int(url.rstrip('/').split('/')[-1])
    ctx.say('dev', f'opened PR #{ctx.pr_no} ({branch} -> main): "{title}"')
    ctx.say('dev', f'  {url}')
    return ctx.pr_no


def fix_pr(ctx: Ctx, req_no: int, finding_no: int, good: bool):
    """Hotfix PR for a post-release bug. good=False: the fix breaks a unit
    test; CI on the PR fails."""
    svc = ctx.repo / 'app/service.py'
    text = svc.read_text()
    m = re.search(r"VERSION = '(\d+)\.(\d+)\.(\d+)'", text)
    bumped = f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"
    text = re.sub(r"VERSION = '[\d.]+'", f"VERSION = '{bumped}'", text)
    if not good:
        text = text.replace('return 503 if broken else 200', 'return 500 if broken else 200')
    svc.write_text(text)
    ctx.say('dev', 'hotfix edit — actual diff:')
    ctx.say_block('dev', '', _diff_preview(ctx))
    G.commit_all(ctx.repo, f'fix: address post-release finding (bump {bumped})')
    body = (f'Fixes #{req_no}\n\nkind: fix-pr\nrequirement: #{req_no}\nfinding: #{finding_no}\n\n'
            f'Hotfix for the reported finding; bumps service version to {bumped}.')
    return dev_open_pr(ctx, f'fix: address finding #{finding_no} ({bumped})', body)


# ---------------------------------------------------------------- pipeline
def run_gate(ctx: Ctx, gate_id: str, cmd: list) -> int:
    """Execute a gate command; record its exit code and timestamp."""
    ctx.say('pipeline', f'running gate "{gate_id}": $ {" ".join(cmd)}')
    p = G.sh(cmd, cwd=ctx.repo, check=False)
    append_jsonl(ctx.ev / 'gates.jsonl',
                 {'gate_id': gate_id, 'required': True,
                  'status': 'PASS' if p.returncode == 0 else 'FAIL',
                  'evaluated_at': G.utc_iso(), 'exit_code': p.returncode})
    tail = '\n'.join((p.stdout or '').strip().splitlines()[-3:]) or '(no output)'
    ctx.say_block('pipeline', f'gate {gate_id} -> '
                   f'{"PASS" if p.returncode == 0 else "FAIL"} (rc={p.returncode}), '
                   f'recorded to evidence/{ctx.sid}/gates.jsonl', tail)
    return p.returncode


def run_unit(ctx: Ctx):
    """Run the target unit suite; return (exit code, combined output)."""
    p = G.sh([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'],
             cwd=ctx.repo, check=False)
    output = (p.stdout or '') + (p.stderr or '')
    tail = '\n'.join(output.strip().splitlines()[-3:]) or '(no output)'
    ctx.say_block('pipeline', f'target release unit run on merged main: rc={p.returncode}', tail)
    return p.returncode, output


def record_waiver(ctx: Ctx, gate_id: str, pr_no: int):
    """Release manager waives a failed-but-waivable gate per policy: posts the
    waiver approval as a PR comment (solo accounts cannot approve their own
    PRs), then records it in the evidence."""
    body = (f'waiver: {gate_id} gate approved per release policy\n\n'
            f'approved by: @{ctx.login}\nscope: release PR #{pr_no}\n'
            f'justification: the value is the AWS documentation example key used in '
            f'SDK samples; no live credential is exposed. '
            f'Policy permits waiving the {gate_id} gate.')
    comment = G.sh(['gh', 'api', f'repos/{ctx.full}/issues/{pr_no}/comments',
                    '-f', f'body={body}'], cwd=ctx.repo)
    import json as _json
    created = _json.loads(comment.stdout)
    append_jsonl(ctx.ev / 'gates.jsonl',
                 {'gate_id': gate_id, 'waiver': {'approved_by': created['user']['login'],
                                                 'approved_at': created['created_at'],
                                                 'pr': pr_no, 'comment_id': created['id'],
                                                 'basis': 'policy-permitted waiver recorded as PR comment'}})
    ctx.say('release-manager', f'waived {gate_id} on PR #{pr_no} via formal approval comment:')
    ctx.say('release-manager', f"  {created['html_url']}")
    ctx.say('release-manager', f"  approved_by={created['user']['login']} "
                               f"approved_at={created['created_at']} (policy-checked at audit)")


# ---------------------------------------------------------------- review / merge
def merge_pr(ctx: Ctx, pr_no: int, admin=False):
    import time as _time
    cmd = ['gh', 'pr', 'merge', str(pr_no), '--merge']
    if admin:
        cmd.append('--admin')  # B01: check override, red code lands anyway
    ctx.say('release-manager', f'$ {" ".join(cmd)}'
            + ('  <-- overriding the red check on purpose' if admin else ''))
    last = None
    for attempt in range(5):
        p = G.sh(cmd, cwd=ctx.repo, check=False)
        if p.returncode == 0:
            break
        last = p
        blob = ((p.stderr or '') + (p.stdout or '')).lower()
        # server-side merge races right after pushing to the PR head are
        # common ("Base branch was modified"); they clear on retry
        if 'base branch was modified' not in blob and 'pull request is not mergeable' not in blob:
            raise G.CmdError(cmd, p.returncode, p.stdout, p.stderr)
        _time.sleep(5)
    else:
        raise G.CmdError(cmd, last.returncode, last.stdout, last.stderr)
    G.sh(['git', 'checkout', 'main'], cwd=ctx.repo)
    G.pull(ctx.repo)
    ctx.say('release-manager', f'merged PR #{pr_no} into main' + (' (check override)' if admin else ''))


# ---------------------------------------------------------------- build / release
def build_artifact(ctx: Ctx) -> str:
    out = G.sh([sys.executable, 'tools/build.py'], cwd=ctx.repo).stdout
    sha = json.loads(out)['sha256']
    source_commit = G.git_out(ctx.repo, 'rev-parse', 'HEAD')
    manifest = {'artifact': 'dist/app.pyz', 'sha256': 'sha256:' + sha,
                'source_commit': source_commit, 'built_at': G.utc_iso()}
    (ctx.repo / 'dist/manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    ctx.say('pipeline', f'built dist/app.pyz sha256={sha} '
                        f'(manifest source {source_commit[:12]})')
    return sha


def release_tag(ctx: Ctx, tag: str, sha: str, notes: str, snap_name: str = 'RELEASE_SNAPSHOT.json'):
    """Commit the release snapshot, create the annotated tag (tagger date is the
    real cutoff), publish a GitHub Release with the artifact as its asset."""
    snap = {'release_id': tag, 'artifact_sha256': 'sha256:' + sha,
            'policy_version': 'P-1', 'generated_at': G.utc_iso(), 'service': 'inventory-service'}
    snap_file = ctx.repo / snap_name
    snap_file.parent.mkdir(parents=True, exist_ok=True)
    snap_file.write_text(json.dumps(snap, indent=2, sort_keys=True) + '\n')
    sha = G.commit_all(ctx.repo, f'release: freeze snapshot for {tag}')
    G.push(ctx.repo)  # the snapshot commit must be on origin/main, not just local
    ctx.say('release-manager', f'frozen snapshot {snap_name} (artifact {snap["artifact_sha256"][:19]}...) '
                               f'committed {sha} — THIS TAG TIME IS THE CUTOFF')
    G.sh(['git', 'tag', '-a', tag, '-m', f'release {tag}'], cwd=ctx.repo)
    G.push(ctx.repo, tag)
    G.sh(['gh', 'release', 'create', tag, 'dist/app.pyz', '--title', tag, '--notes', notes], cwd=ctx.repo)
    ctx.say('release-manager', f'tagged {tag}; published GitHub Release with asset dist/app.pyz:')
    ctx.say('release-manager', f'  https://github.com/{ctx.full}/releases/tag/{tag}')


def deploy_and_probe(ctx: Ctx, env_extra: dict | None = None):
    """Deploy the three stages and probe HTTP /health; commits the observed
    events to this scenario's evidence store (the ops-bot commit)."""
    env = {**os.environ, **(env_extra or {})}
    before = len(read_jsonl(ctx.ev / 'raw/inventory.jsonl'))
    p = G.sh([sys.executable, 'tools/deploy_health.py', '--out', ctx.ev.as_posix()],
             cwd=ctx.repo, check=False, env=env)
    if not (ctx.ev / 'raw/inventory.jsonl').is_file():
        raise RuntimeError(f'deploy/probe produced no evidence (rc={p.returncode}):\n'
                           f'{(p.stderr or p.stdout)[-3000:]}')
    for ev in read_jsonl(ctx.ev / 'raw/inventory.jsonl')[before:]:
        pl = ev['payload']
        ctx.say('ops-bot', f"probed {pl['stage']:<8} HTTP {pl['http_status']} "
                           f"{pl['latency_ms']}ms slo_exceeded={pl['slo_exceeded']} "
                           f"-> signal {ev['signal_id']}")
    sha = G.commit_all(ctx.repo, f'ops: record stage health probe events ({ctx.sid})')
    G.push(ctx.repo)
    ctx.say('ops-bot', f"committed {sha} — probe events stored to evidence/{ctx.sid}/raw/inventory.jsonl")
    return p.returncode


# ---------------------------------------------------------------- signals & triage
def ensure_labels(ctx: Ctx):
    """Create the triage labels and verify they exist before issues reference
    them (creation is normally synchronous; verify anyway, fail loudly)."""
    import time as _time
    wanted = {'signal': 'd93f0b', 'finding': '5319e7', 'requirement': '0e8a16'}
    for name, color in wanted.items():
        G.sh(['gh', 'label', 'create', name, '--color', color], cwd=ctx.repo, check=False)
    for _ in range(10):
        listed = {x['name'] for x in G.gh_api(f'repos/{ctx.full}/labels?per_page=100')}
        if set(wanted) <= listed:
            return
        _time.sleep(2)
    raise RuntimeError(f'labels not available after retries: {set(wanted) - listed}')


def open_signal_issues(ctx: Ctx):
    """Automation opens one issue per observed signal, exactly like an alert
    router paging the on-call. Signals that already have an issue are skipped
    (repeated sweeps only page once per signal)."""
    ensure_labels(ctx)
    issues = []
    for ev in read_jsonl(ctx.ev / 'raw/inventory.jsonl'):
        sid = ev['signal_id']
        if sid in ctx.signal_issues:
            issues.append((sid, ctx.signal_issues[sid]))
            continue
        body = (f'kind: signal\nsignal_id: {sid}\ncycle: {ctx.sid}\nsource: {ev["source"]}\n'
                f'event_time: {ev["event_time"]}\n\nAutomation opened this alert from the stage health probe.')
        url = G.sh(['gh', 'issue', 'create', '--title', f'[signal] {sid}',
                    '--body', body, '--label', 'signal'], cwd=ctx.repo).stdout.strip()
        no = int(url.rstrip('/').split('/')[-1])
        ctx.signal_issues[sid] = no
        issues.append((sid, no))
        ctx.say('automation', f'opened signal issue #{no} for {sid}')
    return issues


def oncall_triage(ctx: Ctx, sid: str, issue_no: int, decision='RESOLVED', note=None,
                  close_issue=True, actor=None):
    """On-call triages: writes the triage record and closes the alert issue
    (the close event carries the same actor). Variants used by scenario
    stories: UNRESOLVED keeps the issue open; an exclusion can be recorded by
    an actor outside the policy's exclusion authorities."""
    existing = read_jsonl(ctx.ev / 'dispositions.jsonl')
    disp = {'disposition_id': f'DISP-{len(existing) + 1:03d}', 'signal_id': sid,
            'decision': decision, 'decision_time': G.utc_iso(),
            'actor': actor or ctx.login, 'effective': True,
            'reason': note or TRIAGE_NOTE.get(decision, 'triaged')}
    append_jsonl(ctx.ev / 'dispositions.jsonl', disp)
    sha = G.commit_all(ctx.repo, f'triage: {decision.lower()} {sid}')
    G.push(ctx.repo)
    if close_issue:
        G.sh(['gh', 'issue', 'close', str(issue_no), '--comment',
              f'{decision}: {disp["reason"]} (triage record {disp["disposition_id"]})'], cwd=ctx.repo)
    ctx.say('on-call', f'{decision} {sid} -> disposition {disp["disposition_id"]} '
                       f'(commit {sha}, decision_time {disp["decision_time"]}, '
                       f'actor={disp["actor"]}); '
                       f'{"closed" if close_issue else "LEFT OPEN"} issue #{issue_no}')
    return disp


def overwrite_release_asset(ctx: Ctx, tag: str):
    """B02 story: after the release was published, the stored artifact gets
    overwritten with different bytes (a registry/storage overwrite accident)."""
    artifact = ctx.repo / 'dist/app.pyz'
    before = 'sha256:' + __import__('hashlib').sha256(artifact.read_bytes()).hexdigest()
    with artifact.open('ab') as f:
        f.write(b'\nOVERWRITTEN_AFTER_RELEASE_B02\n')
    after = 'sha256:' + __import__('hashlib').sha256(artifact.read_bytes()).hexdigest()
    G.sh(['gh', 'release', 'upload', tag, 'dist/app.pyz', '--clobber'], cwd=ctx.repo)
    ctx.say('ops', f'overwrote the {tag} release asset bytes ({before[:19]}.. -> {after[:19]}..); '
                   f'the pinned digest still says {before[:19]}..')
    return before, after


def issue_no_for_signal(ctx: Ctx, sid: str, wait_s=60) -> int:
    """Prefer the number recorded at creation; the issues list API is only
    eventually consistent right after creation, so poll briefly as fallback."""
    if sid in ctx.signal_issues:
        return ctx.signal_issues[sid]
    import time as _time
    deadline = _time.monotonic() + wait_s
    while _time.monotonic() < deadline:
        for issue in G.gh_api(f'repos/{ctx.full}/issues?state=all&per_page=100'):
            if issue.get('pull_request') is None and f'signal_id: {sid}' in (issue.get('body') or ''):
                ctx.signal_issues[sid] = issue['number']
                return issue['number']
        _time.sleep(5)
    raise RuntimeError(f'no signal issue for {sid}')


# ---------------------------------------------------------------- post-release obligation
def report_finding(ctx: Ctx, description: str, attribute_release: bool = True) -> int:
    release_line = f'release: {ctx.tag_main}\n' if attribute_release else ''
    body = (f'kind: finding\n{release_line}\n{description}\n\n'
            'Reported after release; needs a bound requirement and a verified fix release.')
    url = G.sh(['gh', 'issue', 'create', '--title', f'[finding] post-release issue on {ctx.tag_main}',
                '--body', body, '--label', 'finding'], cwd=ctx.repo).stdout.strip()
    no = int(url.rstrip('/').split('/')[-1])
    if attribute_release:
        ctx.say('on-call', f'reported finding #{no} against {ctx.tag_main}: "{description}"')
    else:
        ctx.say('on-call', f'reported finding #{no} — the "release:" field was NEVER filled in '
                           f'(attribution unresolved)')
    ctx.say('on-call', f'  {url}')
    return no


def open_requirement(ctx: Ctx, finding_no: int, due_iso: str, from_finding_no: int | None = None):
    """Open the requirement issue bound to a finding. from_finding_no lets a
    story write a WRONG binding (A202: copy-paste typo referencing another
    finding); the auditor must judge the mis-binding."""
    ref = from_finding_no if from_finding_no is not None else finding_no
    body = (f'kind: requirement\nfrom-finding: #{ref}\ndue: {due_iso}\n\n'
            f'Fix immediately (P0 hotfix SLA); acceptance: fixed service released and verified healthy.')
    url = G.sh(['gh', 'issue', 'create', '--title', f'[requirement] fix finding #{finding_no}',
                '--body', body, '--label', 'requirement'], cwd=ctx.repo).stdout.strip()
    no = int(url.rstrip('/').split('/')[-1])
    if ref == finding_no:
        ctx.say('release-manager', f'opened requirement #{no} bound to finding #{finding_no} (due {due_iso})')
    else:
        ctx.say('release-manager', f'opened requirement #{no} for finding #{finding_no} but its '
                                   f'"from-finding:" line references #{ref} — a copy-paste typo')
    ctx.say('release-manager', f'  {url}')
    return no


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def verify_target(ctx: Ctx, test_passed: bool, unit_exit_code: int | None = None,
                  unit_output: str = ''):
    """Record successor-release verification from measured target evidence:
    the built manifest/artifact, the target tag path, the published release,
    and the unit suite run on merged main."""
    repo = ctx.repo
    policy_text = G.git_out(repo, 'show', f'{ctx.tag_target}:policy/release_policy.yaml')
    service = re.search(r'^service_id:\s*(\S+)', policy_text, re.MULTILINE)
    if not service:
        raise RuntimeError('policy has no service_id')
    target_sha = G.git_out(repo, 'rev-parse', f'{ctx.tag_target}^{{}}')
    on_main = G.sh(['git', 'merge-base', '--is-ancestor', target_sha, 'main'],
                   cwd=repo, check=False).returncode == 0
    release = G.sh(['gh', 'release', 'view', ctx.tag_target, '--repo', ctx.full,
                    '--json', 'tagName,assets'], cwd=repo, check=False)
    release_ok = release.returncode == 0
    assets = json.loads(release.stdout or '{}').get('assets', []) if release_ok else []
    asset_present = any(a.get('name') == 'app.pyz' for a in assets)
    artifact_path = repo / 'dist/app.pyz'
    manifest_path = repo / 'dist/manifest.json'
    snapshot_path = repo / ctx.snap_target
    artifact_sha = _sha256_file(artifact_path) if artifact_path.is_file() else None
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
    if manifest is not None:
        # keep the measured build manifest next to the verification record;
        # dist/ is git-ignored, so without this copy the digest is unverifiable
        (ctx.ev / 'target').mkdir(parents=True, exist_ok=True)
        (ctx.ev / 'target/build_manifest.json').write_bytes(manifest_path.read_bytes())
    snapshot = json.loads(snapshot_path.read_text()) if snapshot_path.is_file() else None
    source_sha = (manifest or {}).get('source_commit')
    source_ancestor = bool(source_sha) and G.sh(
        ['git', 'merge-base', '--is-ancestor', source_sha, target_sha],
        cwd=repo, check=False).returncode == 0
    source_unchanged = bool(source_sha) and G.sh(
        ['git', 'diff', '--quiet', source_sha, target_sha, '--', 'app/service.py'],
        cwd=repo, check=False).returncode == 0
    manifest_ok = bool(
        manifest and artifact_sha
        and manifest.get('artifact') == 'dist/app.pyz'
        and manifest.get('sha256') == artifact_sha
        and source_ancestor and source_unchanged
        and (snapshot or {}).get('artifact_sha256') == artifact_sha)
    match = re.search(r'^Ran\s+(\d+)\s+tests?\b', unit_output or '', re.MULTILINE)
    test_count = int(match.group(1)) if match else None
    coverage_ok = test_count is not None and test_count > 0
    selected_path_valid = on_main and release_ok and asset_present
    closed = bool(manifest_ok and coverage_ok and selected_path_valid and test_passed)
    row = {'release_id': ctx.tag_target, 'verified_at': G.utc_iso(),
           'test_passed': test_passed, 'manifest_complete': manifest_ok,
           'coverage_complete': coverage_ok, 'selected_path_valid': selected_path_valid,
           'closed': closed, 'service_id': service.group(1),
           'test_count': test_count, 'target_commit': target_sha,
           'manifest_sha256': (_sha256_file(ctx.ev / 'target/build_manifest.json')
                               if manifest is not None else None)}
    if unit_exit_code is not None:
        row['unit_exit_code'] = unit_exit_code
    append_jsonl(ctx.ev / 'target/verification.jsonl', row)
    sha = G.commit_all(ctx.repo, f'verify: successor release {ctx.tag_target} verification record')
    G.push(ctx.repo)
    ctx.say('release-manager', f"target verification for {ctx.tag_target}: test_passed={test_passed} "
                               f"manifest={manifest_ok} coverage={coverage_ok} "
                               f"selected_path={selected_path_valid} closed={closed} "
                               f"(unit rc={unit_exit_code}, tests={test_count}) "
                               f"-> {ctx.sid}/target/verification.jsonl ({sha})")
