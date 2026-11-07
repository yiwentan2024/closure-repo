"""One-command live operations harness (see the liveops section in the root README).

    python -m liveops.operator                       # all 16 controlled scenarios
    python -m liveops.operator --scenario N01,A101   # subset
    python -m liveops.operator --plan                # print stories, execute nothing

All scenarios of a run share ONE temporary private batch repository under your
own GitHub account (one repository creation per run, not per scenario). Each
scenario runs as its own release cycle inside that repository — own feature
branch, own evidence namespace (evidence/<SID>/), own annotated release/<SID>
tag whose tagger date is that scenario's real cutoff — and is audited by the
unmodified closure verifier against the recorded expectations, plus an in-repo
closure-audit workflow dispatch that must agree. The batch repository is kept
for review; clean it up manually with
`python -m liveops.cleanup --root <output dir> --yes`.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, shutil, sys, time
from pathlib import Path

from loguru import logger

from . import ghutil as G
from . import roles as R
from .roles import setup_logger
from .scenario_stories import STORIES
from .collector import fetch_live, assemble
from . import replay_check

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / 'liveops/seed'
VENDOR_FILES = ['closure_audit.py', 'collector.py', 'run_audit.py']


def log(actor, message):
    logger.bind(actor=actor).info(message)


def narrate_verdict(row: dict, out_root: Path):
    """Show HOW the observed verdict is grounded and how to check it yourself."""
    sid = row['scenario_id']
    out_dir = out_root / sid
    match = '<green>MATCH</green>' if row.get('match') else '<red>MISMATCH</red>'
    log('verdict', f'{match}  {sid}: expected={row.get("oracle_decision")} '
                   f'observed={row.get("observed_decision")}')
    log('verdict', f'observed failure codes: {row.get("observed_codes") or "[]"} '
                   f'(evidence_status={row.get("evidence_status")})')
    for f in (row.get('result') or {}).get('historical_failures', []) + \
             (row.get('result') or {}).get('audit_failures', []):
        log('verdict', f'<dim>  {f["code"]}: {f["reason"]} (status={f["evidence_status"]})</dim>')
    log('verdict', 'the verdict is only as trustworthy as you can recheck it:')
    log('verdict', f'<dim>  facts in    {out_dir / "case.json"}   (what the verifier consumed)</dim>')
    log('verdict', f'<dim>  verdict out {out_dir / "result.json"}  (verifier output, unmodified)</dim>')
    log('verdict', f'<dim>  recompute   python -m verifier.closure_audit --input '
                   f'{out_dir / "case.json"} --output /tmp/recheck.json --allow-open</dim>')
    inrepo = (row.get('inrepo') or {})
    if inrepo.get('url'):
        log('verdict', f'<dim>  in-repo audit (independent run, must agree): {inrepo["url"]}</dim>')


def utc_stamp():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def seed_scratch_repo(repo: Path, full: str, login: str, out_dir: Path):
    """Copy the product template, personalize the policy, vendor the audit
    files (byte-identical, digests recorded both in-repo and in the bundle)."""
    shutil.copytree(SEED, repo, dirs_exist_ok=True)
    policy = repo / 'policy/release_policy.yaml'
    policy.write_text(policy.read_text().replace('@OPERATOR_LOGIN@', login))
    audit = repo / 'audit'
    audit.mkdir(exist_ok=True)
    sources = [ROOT / 'verifier/closure_audit.py', ROOT / 'liveops/collector.py',
               ROOT / 'liveops/inrepo/run_audit.py']
    digests = {}
    for src, name in zip(sources, VENDOR_FILES):
        data = src.read_bytes()
        (audit / name).write_bytes(data)
        digests[name] = 'sha256:' + hashlib.sha256(data).hexdigest()
    (audit / 'MANIFEST.txt').write_text(
        'Vendored closure-audit module (byte-identical copies; digests below)\n'
        + ''.join(f'{v}  {k}\n' for k, v in digests.items()), encoding='utf-8')
    (out_dir / 'vendored_files.json').write_text(json.dumps(digests, indent=2) + '\n')
    G.commit_all(repo, 'chore: project setup (product repo + closure audit extension)')
    G.sh(['git', 'push', '-u', 'origin', 'main'], cwd=repo)


def refresh_audit_extension(repo: Path):
    """On resume, bring the batch repo's vendored audit module and workflows up
    to the current version (byte compare; commit only when something changed)."""
    import hashlib
    pairs = [(ROOT / 'verifier/closure_audit.py', repo / 'audit/closure_audit.py'),
             (ROOT / 'liveops/collector.py', repo / 'audit/collector.py'),
             (ROOT / 'liveops/inrepo/run_audit.py', repo / 'audit/run_audit.py'),
             (SEED / '.github/workflows/closure-audit.yml',
              repo / '.github/workflows/closure-audit.yml'),
             (SEED / '.github/workflows/ci.yml', repo / '.github/workflows/ci.yml')]
    changed = False
    for src, dst in pairs:
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            changed = True
    if changed:
        digests = {p.name: 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in [repo / 'audit/closure_audit.py', repo / 'audit/collector.py',
                             repo / 'audit/run_audit.py']}
        (repo / 'audit/MANIFEST.txt').write_text(
            'Vendored closure-audit module (byte-identical copies; digests below)\n'
            + ''.join(f'{v}  {k}\n' for k, v in digests.items()), encoding='utf-8')
        G.commit_all(repo, 'chore: refresh closure audit extension to current version')
        G.push(repo)
        log('operator', 'refreshed the vendored closure-audit extension in the batch repo')


def prepare_batch(owner: str, args, out_root: Path):
    """Create (or resume) the single batch repository for this run."""
    meta_path = out_root / 'meta.json'
    workspace = (args.workspace or out_root / 'workspaces')
    workspace.mkdir(parents=True, exist_ok=True)
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text())
        full = meta['repo_full']
        local = workspace / full.split('/')[-1]
        if G.repo_exists(full):
            if local.exists():
                log('operator', f'resuming batch repository {full} (existing clone)')
                G.sh(['git', 'checkout', 'main'], cwd=local)
                G.pull(local)
            else:
                log('operator', f'resuming batch repository {full} (fresh clone)')
                local = G.clone_repo(full, workspace)
            refresh_audit_extension(local)
            return local, full, meta['login']
        raise SystemExit(
            f'recorded batch repository {full} no longer exists; refusing to mix a new '
            f'batch into the existing evidence root {out_root}. Start a fresh run '
            f'without --root.')
    stamp = utc_stamp()
    full = f'{owner}/lab-live-batch-{stamp}'
    log('operator', f'batch repository: https://github.com/{full}')
    local = G.create_repo(full, workspace)
    login = G.setup_git_identity(local)
    out_root.mkdir(parents=True, exist_ok=True)
    seed_scratch_repo(local, full, login, out_root)
    meta_path.write_text(json.dumps({'repo_full': full, 'login': login,
                                     'created': utc_stamp()}, indent=2) + '\n')
    return local, full, login


def inrepo_audit(full: str, sid: str, out_dir: Path, timeout_s=900):
    """Dispatch the batch repo's closure-audit workflow for this scenario cycle;
    return its verdict and run URL."""
    started = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
    G.sh(['gh', 'workflow', 'run', 'closure-audit.yml', '--repo', full, '-f', f'scenario={sid}'])
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rows = json.loads(G.sh(['gh', 'run', 'list', '--repo', full, '--workflow',
                                'closure-audit.yml', '--limit', '10',
                                '--json', 'databaseId,status,conclusion,createdAt,url']).stdout)
        done = sorted((r for r in rows if r['status'] == 'completed'
                       and dt.datetime.fromisoformat(r['createdAt'].replace('Z', '+00:00'))
                       >= started),
                      key=lambda r: (r['createdAt'], r['databaseId']))
        if done:
            run = done[0]
            art = out_dir / 'inrepo_audit'
            G.sh(['gh', 'run', 'download', str(run['databaseId']), '--repo', full,
                  '--name', f'closure-audit-{sid}-{run["databaseId"]}',
                  '--dir', art.as_posix()], check=False)
            result_file = art / 'result.json'
            if result_file.is_file():
                r = json.loads(result_file.read_text())
                return {'run_id': run['databaseId'], 'url': run['url'],
                        'conclusion': run['conclusion'],
                        'decision': r['machine_decision'], 'codes': r['failure_codes'],
                        'result': r}
            return {'run_id': run['databaseId'], 'url': run['url'],
                    'conclusion': run['conclusion'], 'decision': 'NO_ARTIFACT', 'codes': []}
        time.sleep(10)
    return {'run_id': None, 'url': None, 'conclusion': 'TIMEOUT',
            'decision': 'TIMEOUT', 'codes': []}


def wait_until(iso: str):
    target = dt.datetime.fromisoformat(iso.replace('Z', '+00:00'))
    delta = (target - dt.datetime.now(dt.timezone.utc)).total_seconds() + 3
    if delta > 0:
        log('operator', f'waiting {delta:.0f}s for the obligation due time to pass '
                        f'(audit must be scheduled after due, not before)')
        time.sleep(delta)


def anchors(ctx: R.Ctx, full: str):
    """Per-scenario boundary anchors: the frozen release tag, the commit it
    points at, the GitHub Release page, (audit run URL added separately)."""
    tag = ctx.tag_main
    try:
        sha = G.git_out(ctx.repo, 'rev-parse', f'{tag}^{{}}')
    except Exception:
        return {}
    return {'tag': tag,
            'release_url': f'https://github.com/{full}/releases/tag/{tag}',
            'commit_url': f'https://github.com/{full}/commit/{sha}'}


def run_scenario(sid: str, local: Path, full: str, login: str, out_root: Path, oracle_row: dict,
                 args):
    out_dir = out_root / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    log('operator', f'========== {sid} — {STORIES[sid]["title"]} ==========')
    log('operator', f'cycle boundary: https://github.com/{full} · evidence/{sid}/ · tag release/{sid}')
    log('operator', f'expected (recorded outcome): {oracle_row["decision"]} '
                    f'{oracle_row["codes"] or ""}')
    try:
        ctx = R.Ctx(local, full, login, sid)
        ctx.log_file = out_dir / 'actions.log'
        R.prepare_cycle(ctx)
        story = STORIES[sid]['run'](ctx)
        if story.get('wait_until'):
            wait_until(story['wait_until'])
        facts = fetch_live(local.as_posix(), full,
                           cutoff_tag=ctx.tag_main, target_tag=ctx.tag_target,
                           evidence_sub=f'evidence/{sid}', snapshot_name=ctx.snap_main,
                           scenario_id=sid)
        case = assemble(facts)
        row = replay_check.check(sid, case)
        (out_dir / 'facts.json').write_text(json.dumps(facts, indent=2, sort_keys=True) + '\n')
        (out_dir / 'case.json').write_text(json.dumps(case, indent=2, sort_keys=True) + '\n')
        (out_dir / 'result.json').write_text(
            json.dumps(row['result'], indent=2, sort_keys=True) + '\n')
        row['repo'] = f'https://github.com/{full}'
        row['deviation'] = story['deviation']
        row['anchors'] = anchors(ctx, full)
        if not args.no_inrepo:
            log('operator', 'dispatching in-repo closure-audit workflow ...')
            row['inrepo'] = inrepo_audit(full, sid, out_dir)
            row['inrepo_match'] = (row['inrepo']['decision'] == row['observed_decision']
                                   and row['inrepo']['codes'] == row['observed_codes'])
            if row['inrepo'].get('url'):
                row['anchors']['audit_run_url'] = row['inrepo']['url']
        (out_dir / 'story.json').write_text(json.dumps(
            {'scenario_id': sid, 'repo': f'https://github.com/{full}',
             'title': STORIES[sid]['title'], **story, 'anchors': row['anchors']},
            indent=2) + '\n')
        (out_dir / 'git_log.txt').write_text(
            G.git_out(local, 'log', '--graph', '--oneline', '--decorate', '-20') + '\n')
        for label, url in (('release', row['anchors'].get('release_url')),
                           ('commit', row['anchors'].get('commit_url')),
                           ('audit run', row['anchors'].get('audit_run_url'))):
            if url:
                log('operator', f'anchor {label}: {url}')
        return row
    except Exception as exc:  # noqa: BLE001 — a failed scenario must not kill the batch
        import traceback
        traceback.print_exc()
        log('operator', f'{sid} failed inside the shared repository; later scenarios are '
                        f'unaffected (each starts from a restored baseline). Re-run this command '
                        f'with --scenario {sid} in a fresh run to retry it.')
        return {'scenario_id': sid, 'repo': f'https://github.com/{full}',
                'match': False, 'error': str(exc),
                'observed_decision': 'ERROR', 'observed_codes': []}


def write_matrix(out_root: Path, rows: list, full: str):
    lines = [f"{'scenario':6} {'expected':8} {'observed':10} {'match':6} codes_beyond_expected   inrepo"] + [
        f"{r['scenario_id']:6} {str(r.get('oracle_decision')):8} "
        f"{str(r.get('observed_decision')):10} {str(r.get('match')):6} "
        f"{','.join(r.get('extra_codes') or []) or '(none)':22.22} "
        f"{(r.get('inrepo') or {}).get('decision', '-')}" for r in rows]
    lines += ['', f'batch repository (kept for review): https://github.com/{full}',
              'per-scenario anchors:'] + [
        f"  {r['scenario_id']}: {((r.get('anchors') or {}).get('release_url')
                                  or r.get('repo') or '-')}"
        f"{'  audit: ' + r['anchors']['audit_run_url'] if (r.get('anchors') or {}).get('audit_run_url') else ''}"
        for r in rows]
    lines += ['', f'clean up manually with: python -m liveops.cleanup --repo {full} --yes']
    (out_root / 'matrix.txt').write_text('\n'.join(lines) + '\n')
    for ln in lines:
        log('matrix', ln)


def merge_rows(prior_rows: list, new_rows: list):
    """Resume appends newly run scenarios and replaces retried ones in place,
    keeping non-matching rows from the prior matrix instead of dropping them."""
    rows = list(prior_rows)
    index = {r['scenario_id']: i for i, r in enumerate(rows)}
    for row in new_rows:
        sid = row['scenario_id']
        if sid in index:
            rows[index[sid]] = row
        else:
            index[sid] = len(rows)
            rows.append(row)
    return rows


def matrix_ok(rows: list, require_inrepo: bool):
    for r in rows:
        if not r.get('match'):
            return False
        if require_inrepo and not r.get('inrepo_match'):
            return False
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--scenario', default=','.join(STORIES),
                    help='comma-separated scenario IDs (default: all 16)')
    ap.add_argument('--plan', action='store_true', help='print the plan, execute nothing')
    ap.add_argument('--no-inrepo', action='store_true',
                    help='skip the in-repo closure-audit workflow dispatch')
    ap.add_argument('--out', type=Path, default=ROOT / 'runs/live')
    ap.add_argument('--workspace', type=Path, default=None,
                    help='scratch clone directory (default: <out>/workspaces)')
    ap.add_argument('--root', type=Path, default=None,
                    help='resume into an existing evidence root (reuses its batch repository; '
                         'scenarios already MATCHed there are skipped)')
    ap.add_argument('--reaudit', action='store_true',
                    help='with --root: re-dispatch the in-repo closure audit for scenarios '
                         'whose in-repo verdict is missing or disagrees; no stories re-run')
    args = ap.parse_args(argv)
    setup_logger()
    if args.reaudit and args.root is None:
        ap.error('--reaudit requires --root <existing evidence root>')
    if args.reaudit and args.no_inrepo:
        ap.error('--reaudit and --no-inrepo contradict each other')
    ids = [x.strip() for x in args.scenario.split(',') if x.strip()]
    unknown = [x for x in ids if x not in STORIES]
    if unknown:
        ap.error(f'unknown scenario(s): {unknown}; available: {",".join(STORIES)}')
    oracle = replay_check.load_oracle()
    log('operator', 'scenario plan (recorded expected outcomes):')
    for sid in ids:
        log('plan', f'{sid}: {STORIES[sid]["title"]} -> expect {oracle[sid]["decision"]} '
               f'{oracle[sid]["codes"] or ""}')
    if args.plan:
        log('operator', '--plan: nothing executed.')
        return 0
    owner = G.gh_owner()
    G.ensure_git_cred()
    G.check_scopes()

    if args.reaudit:
        out_root = args.root.resolve()
        matrix_path = out_root / 'matrix.json'
        meta_path = out_root / 'meta.json'
        if not matrix_path.is_file() or not meta_path.is_file():
            ap.error(f'--reaudit: {out_root} is not a completed evidence root '
                     f'(matrix.json and meta.json are required)')
        rows = json.loads(matrix_path.read_text())
        full = json.loads(meta_path.read_text())['repo_full']
        if not G.repo_exists(full):
            log('operator', f'reaudit target {full} no longer exists; nothing to re-audit')
            return 2
        n = 0
        for row in rows:
            sid = row['scenario_id']
            out_dir = out_root / sid
            if not (out_dir / 'result.json').is_file():
                log('reaudit', f'{sid}: no local result.json; skipped (scenario never completed)')
                continue
            need = ((row.get('inrepo') or {}).get('decision') != row.get('observed_decision')
                    or (row.get('inrepo') or {}).get('codes') != row.get('observed_codes'))
            if not need:
                continue
            n += 1
            log('operator', f'--- {sid}: re-dispatch in-repo closure audit ---')
            row['inrepo'] = inrepo_audit(full, sid, out_dir)
            row['inrepo_match'] = (row['inrepo']['decision'] == row['observed_decision']
                                   and row['inrepo']['codes'] == row['observed_codes'])
            if row['inrepo'].get('url'):
                row.setdefault('anchors', {})['audit_run_url'] = row['inrepo']['url']
                log('operator', f"anchor audit run: {row['inrepo']['url']}")
            log('reaudit', f"{sid}: inrepo={row['inrepo']['decision']} "
                           f"codes={row['inrepo']['codes']} match={row['inrepo_match']}")
        log('reaudit', f'{n} scenario(s) re-dispatched, {len(rows) - n} left as recorded')
        matrix_path.write_text(json.dumps(rows, indent=2, default=str) + '\n')
        write_matrix(out_root, rows, full)
        return 0 if matrix_ok(rows, require_inrepo=True) else 2

    prior_rows = []
    if args.root is not None:
        out_root = args.root.resolve()
        if not out_root.is_dir():
            ap.error(f'--root {out_root} does not exist (omit --root to start a fresh run)')
        prior_matrix = out_root / 'matrix.json'
        if prior_matrix.is_file():
            prior_rows = json.loads(prior_matrix.read_text())
        done = {r['scenario_id'] for r in prior_rows if r.get('match')}
        ids = [x for x in ids if x not in done]
        log('operator', f'resuming {out_root}: {len(done)} scenario(s) already MATCHed, '
                        f'{len(ids)} to run')
    else:
        stamp = utc_stamp()
        out_root = (args.out / stamp).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    local, full, login = prepare_batch(owner, args, out_root)

    new_rows = []
    for sid in ids:
        row = run_scenario(sid, local, full, login, out_root, oracle[sid], args)
        new_rows.append(row)
        narrate_verdict(row, out_root)
    rows = merge_rows(prior_rows, new_rows)
    (out_root / 'matrix.json').write_text(json.dumps(rows, indent=2, default=str) + '\n')
    write_matrix(out_root, rows, full)
    log('operator', f'evidence root: {out_root}')
    ok = matrix_ok(rows, require_inrepo=not args.no_inrepo)
    if not ok:
        log('operator', 'batch is NOT complete: at least one scenario lacks a matching '
                        f'verdict{" (local and in-repo must both agree)" if not args.no_inrepo else ""}; '
                        f'resume with --root {out_root}, or re-dispatch in-repo audits with '
                        f'--root {out_root} --reaudit')
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
