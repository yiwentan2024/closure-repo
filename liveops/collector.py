"""Assemble a closure-audit case from a repository's own real records.

Reads, and only reads, the sources declared in the repo's COLLECTION_RULE.md:
committed evidence files, git history (tag times, frozen file versions) and
GitHub REST records (issues, PRs/reviews, release assets). Fails closed on
missing material and on contradictions between sources. Self-contained on
purpose so the identical file can be vendored into the audited repository and run there
(closure-audit.yml) — the try/except import keeps both contexts working.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

try:
    from verifier.closure_audit import sha  # operator side (harness checkout)
except ImportError:
    from closure_audit import sha  # vendored in-repo audit copy

COLLECTOR_VERSION = 'liveops-collector-2'
APPROVED_SOURCES = ['stage-deploy-http']


class CollectorError(RuntimeError):
    """Fail-closed: sources contradict or required material is absent."""


def _sh(cmd, cwd=None, check=True):
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise CollectorError(f"command failed: {' '.join(map(str, cmd))}\n{p.stderr}")
    return p


def _gh(endpoint):
    return json.loads(_sh(['gh', 'api', endpoint]).stdout or 'null')


def _jl(path: Path):
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def _body_field(body: str, key: str):
    m = re.search(rf'^{key}:\s*(.+)$', body or '', re.MULTILINE)
    return m.group(1).strip() if m else None


def _has_label(item: dict, name: str):
    return any(l.get('name') == name for l in item.get('labels') or [])


def _git_show_bytes(repo: Path, ref: str, path: str):
    p = subprocess.run(['git', 'show', f'{ref}:{path}'], cwd=repo, capture_output=True)
    return p.stdout if p.returncode == 0 else None


def _rule_declared(rule_text: str, key: str) -> bool:
    m = re.search(rf'^{re.escape(key)}:\s*(\S+)\s*$', rule_text or '', re.MULTILINE)
    return bool(m and m.group(1).lower() == 'complete')


def _paged(endpoint):
    """Fetch every page; a short page terminates, so no record is silently cut."""
    sep = '&' if '?' in endpoint else '?'
    rows, page = [], 1
    while True:
        batch = _gh(f'{endpoint}{sep}per_page=100&page={page}') or []
        rows.extend(batch)
        if len(batch) < 100:
            return rows
        page += 1


def _issues(full):
    return [x for x in _paged(f'repos/{full}/issues?state=all')
            if x.get('pull_request') is None]


def _close_event(full, number):
    for ev in _gh(f'repos/{full}/issues/{number}/timeline?per_page=100') or []:
        if ev.get('event') == 'closed':
            return ev.get('actor', {}).get('login'), ev.get('created_at')
    return None, None


def fetch_live(repo_dir, full, cutoff_tag='v0.0.1', target_tag='v0.0.2',
               evidence_sub='evidence', snapshot_name='RELEASE_SNAPSHOT.json', scenario_id=None):
    """Collect all facts from the repository and its GitHub records now.

    evidence_sub/snapshot_name/scenario_id scope the collection to one scenario
    inside a shared batch repository (defaults keep the single-scenario layout;
    with scenario_id, only that cycle's signal issues and findings are read).
    """
    repo = Path(repo_dir)
    ev = repo / evidence_sub
    inventory_path = ev / 'raw/inventory.jsonl'
    if not inventory_path.is_file():
        raise CollectorError(f'missing inventory evidence: {inventory_path}')
    inventory_bytes = inventory_path.read_bytes()
    inventory_source_ref = _sh(
        ['git', 'log', '-1', '--format=%H', '--',
         inventory_path.relative_to(repo).as_posix()],
        cwd=repo, check=False).stdout.strip() or None
    releases = {r['tag_name']: r for r in (_gh(f'repos/{full}/releases?per_page=100') or [])}
    if cutoff_tag not in releases:
        raise CollectorError(f'release {cutoff_tag} not found for {full}')
    with tempfile.TemporaryDirectory() as tmp:
        _sh(['gh', 'release', 'download', cutoff_tag, '--repo', full,
             '--pattern', '*.pyz', '--dir', tmp])
        blobs = list(Path(tmp).glob('*.pyz'))
        if len(blobs) != 1:
            raise CollectorError(
                f'expected exactly one .pyz asset in {cutoff_tag}, found {len(blobs)}')
        asset_sha = 'sha256:' + hashlib.sha256(blobs[0].read_bytes()).hexdigest()
    policy_bytes = _git_show_bytes(repo, cutoff_tag, 'policy/release_policy.yaml')
    if policy_bytes is None:
        raise CollectorError(f'policy/release_policy.yaml missing at {cutoff_tag}')
    policy_text = policy_bytes.decode('utf-8')
    rule_bytes = _git_show_bytes(repo, cutoff_tag, 'COLLECTION_RULE.md')
    rule_text = rule_bytes.decode('utf-8') if rule_bytes is not None else ''
    issues = _issues(full)
    pulls = _paged(f'repos/{full}/pulls?state=all')
    if scenario_id is not None:
        # collection window: [this cycle's baseline commit, the NEXT cycle's
        # baseline commit) — earlier records are leftovers of failed attempts,
        # later records belong to other cycles (matters when re-auditing a past
        # cycle of a shared batch repository)
        cycle_start = _sh(['git', 'log', '-1', f'--grep=prepare release cycle {scenario_id}',
                           '--format=%cI'], cwd=repo).stdout.strip() or None
        if not cycle_start:
            raise CollectorError(
                f'no "prepare release cycle {scenario_id}" commit; collection window is unbounded')
        anchor = dt.datetime.fromisoformat(cycle_start.replace('Z', '+00:00'))
        later = [dt.datetime.fromisoformat(x.replace('Z', '+00:00'))
                 for x in _sh(['git', 'log', '--grep=prepare release cycle',
                               '--format=%cI'], cwd=repo).stdout.split()
                 if x and dt.datetime.fromisoformat(x.replace('Z', '+00:00')) > anchor]
        upper = min(later) if later else None

        def _in_window(created: str) -> bool:
            when = dt.datetime.fromisoformat(created.replace('Z', '+00:00'))
            return when >= anchor and (upper is None or when < upper)

        issues = [i for i in issues if _in_window(i['created_at'])]
        # a requirement is in scope when it claims any collected finding — or
        # when a fix PR satisfies it (a wrongly-bound requirement — the fault
        # A202 stages — would otherwise be filtered out before the auditor
        # could ever judge it)
        findings = [i for i in issues if _has_label(i, 'finding')]
        finding_nos = {f'#{i["number"]}' for i in findings}
        req_refs = {_body_field(p.get('body') or '', 'requirement') for p in pulls
                    if _body_field(p.get('body') or '', 'kind') == 'fix-pr'}
        issues = [i for i in issues
                  if (_has_label(i, 'signal')
                      and _body_field(i.get('body') or '', 'cycle') == scenario_id)
                  or _has_label(i, 'finding')
                  or (_has_label(i, 'requirement')
                      and (_body_field(i.get('body') or '', 'from-finding') in finding_nos
                           or f'#{i["number"]}' in req_refs))]
    return {
        'audit_time': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'scenario_id': scenario_id,
        'inventory': _jl(ev / 'raw/inventory.jsonl'),
        'dispositions': _jl(ev / 'dispositions.jsonl'),
        'gates': _jl(ev / 'gates.jsonl'),
        'target_verification': _jl(ev / 'target/verification.jsonl'),
        'tag_time': _sh(['git', 'for-each-ref', f'refs/tags/{cutoff_tag}',
                         '--format=%(taggerdate:iso-strict)'], cwd=repo).stdout.strip(),
        'snapshot_frozen': json.loads(_sh(['git', 'show', f'{cutoff_tag}:{snapshot_name}'],
                                          cwd=repo).stdout),
        'snapshot_current': json.loads((repo / snapshot_name).read_text())
                            if (repo / snapshot_name).is_file() else None,
        'policy_text': policy_text,
        'collection_rule_sha': ('sha256:' + hashlib.sha256(rule_bytes).hexdigest())
                               if rule_bytes is not None else None,
        # the rule declares that findings are observed exhaustively; the
        # paginated issue/pull reads above cover every record, and any read
        # failure raises instead of silently attesting a partial scope
        'finding_observation_complete': (
            rule_bytes is not None
            and _rule_declared(rule_text, 'finding_observation')),
        'inventory_source_sha256': 'sha256:' + hashlib.sha256(inventory_bytes).hexdigest(),
        'inventory_source_ref': inventory_source_ref,
        'cutoff_tag': cutoff_tag,
        'target_tag': target_tag,
        'asset_sha': asset_sha,
        'issues': issues,
        'pulls': pulls,
        'reviews_by_pr': {str(p['number']): (_gh(f'repos/{full}/pulls/{p["number"]}/reviews') or [])
                          for p in pulls},
        'pr_comments_by_pr': {str(p['number']): (_gh(f'repos/{full}/issues/{p["number"]}/comments'
                                                     '?per_page=100') or [])
                              for p in pulls},
        'close_actors': {str(i['number']): _close_event(full, i['number']) for i in issues},
    }


def _parse_policy(text: str):
    def line(key):
        m = re.search(rf'^{key}:\s*\[(.*?)\]\s*$', text, re.MULTILINE)
        return [x.strip() for x in m.group(1).split(',')] if m else []
    version = re.search(r'^release_policy_version:\s*(\S+)', text, re.MULTILINE)
    service = re.search(r'^service_id:\s*(\S+)', text, re.MULTILINE)
    return {'version': version.group(1) if version else '',
            'service_id': service.group(1) if service else '',
            'waivable_gates': line('waivable_gates'),
            'waiver_approvers': line('waiver_approvers'),
            'exclusion_authorities': line('exclusion_authorities'),
            'approved_sources': line('approved_sources')}


def assemble(facts: dict):
    """Pure mapping facts -> case dict (schema-compatible with scenarios/build_case)."""
    cutoff, audit = facts['tag_time'], facts['audit_time']
    raw = [{'signal_id': r['signal_id'], 'source_id': r['source'],
            'event_time': r['event_time'], 'ingest_time': r['ingest_time'],
            'payload': r['payload'], 'payload_sha256': r['payload_sha256']}
           for r in facts['inventory']]
    ids = [r['signal_id'] for r in raw]
    if len(set(ids)) != len(ids):
        raise CollectorError('duplicate signal ids in inventory')
    policy = _parse_policy(facts['policy_text'])

    # dispositions are cross-checked against real issue closes (same actor)
    by_signal_issue = {}
    for i in facts['issues']:
        if _has_label(i, 'signal') and (i.get('body') or ''):
            sid = _body_field(i['body'], 'signal_id')
            if sid:
                by_signal_issue[sid] = i
    dispositions = []
    for d in facts['dispositions']:
        sid = d['signal_id']
        issue = by_signal_issue.get(sid)
        if issue is None:
            raise CollectorError(f'disposition for {sid} has no backing signal issue')
        if issue['state'] != 'closed' and d.get('decision') != 'UNRESOLVED':
            raise CollectorError(f'disposition for {sid} but signal issue #{issue["number"]} is open')
        closer, _ = facts['close_actors'].get(str(issue['number']), (None, None))
        if issue['state'] == 'closed' and closer != d['actor']:
            raise CollectorError(f'disposition actor {d["actor"]} != issue closer {closer} for {sid}')
        # authorization is a policy fact, not a claim made by the triage record
        trusted = dict(d)
        trusted['authorized'] = d['actor'] in policy['exclusion_authorities']
        dispositions.append(trusted)

    candidates = []
    for sid, issue in by_signal_issue.items():
        if sid not in ids:
            raise CollectorError(f'signal issue #{issue["number"]} references unknown signal {sid}')
        effective = [d for d in dispositions if d['signal_id'] == sid and d.get('effective') is True]
        disp_id = effective[0]['disposition_id'] if len(effective) == 1 else ''
        state = 'OPEN'
        if issue['state'] == 'closed' and len(effective) == 1:
            state = 'RESOLVED' if effective[0]['decision'] == 'RESOLVED' else 'EXCLUDED'
        candidates.append({'candidate_id': f'C-#{issue["number"]}', 'signal_id': sid,
                           'disposition_id': disp_id, 'state': state})

    # gates; a failed waivable gate with a policy-backed PR-review waiver -> WAIVED
    gates, waivers = [], {}
    for g in facts['gates']:
        if 'waiver' in g:
            waivers[g['gate_id']] = g['waiver']
        else:
            gates.append(g)
    case_gates = []
    for g in gates:
        entry = {'gate_id': g['gate_id'], 'required': g.get('required', True),
                 'status': g['status'], 'evaluated_at': g['evaluated_at']}
        w = waivers.get(g['gate_id'])
        if g['status'] != 'PASS' and w:
            if g['gate_id'] not in policy['waivable_gates']:
                raise CollectorError(f'waiver recorded for non-waivable gate {g["gate_id"]}')
            comment = next((c for c in facts['pr_comments_by_pr'].get(str(w.get('pr')), [])
                            if c.get('id') == w.get('comment_id')
                            and c['user']['login'] == w['approved_by']
                            and c['created_at'] == w['approved_at']), None)
            if comment is None:
                raise CollectorError(f'waiver for {g["gate_id"]} not backed by a real PR comment')
            entry['status'] = 'WAIVED'
            entry['waiver'] = {'approved_by': w['approved_by'], 'approved_at': w['approved_at']}
        case_gates.append(entry)

    frozen = facts['snapshot_frozen']
    snapshot = {'frozen_payload': frozen, 'frozen_sha256': sha(frozen),
                'current_payload': facts['snapshot_current'], 'frozen_at': cutoff}

    findings = []
    for i in facts['issues']:
        if not _has_label(i, 'finding'):
            continue
        fid = f'F-#{i["number"]}'
        fix_pr = next((p for p in facts['pulls']
                       if _body_field(p.get('body') or '', 'kind') == 'fix-pr'
                       and _body_field(p.get('body') or '', 'finding') == f'#{i["number"]}'), None)
        req_issue = next((r for r in facts['issues']
                          if _has_label(r, 'requirement')
                          and _body_field(r.get('body') or '', 'from-finding') == f'#{i["number"]}'), None)
        if req_issue is None and fix_pr is not None:
            # the requirement may be bound to the WRONG finding (a real fault the
            # auditor must judge); fall back to the fix PR's requirement reference
            ref = _body_field(fix_pr.get('body') or '', 'requirement')
            req_issue = next((r for r in facts['issues']
                              if _has_label(r, 'requirement') and f'#{r["number"]}' == ref), None)
        if req_issue is None or fix_pr is None:
            raise CollectorError(f'finding {fid} lacks requirement issue or fix PR')
        ver = [v for v in facts['target_verification']
               if v['release_id'] == facts['target_tag']]
        if not ver:
            raise CollectorError(f'finding {fid} lacks target verification record')
        v = ver[-1]
        findings.append({
            'finding_id': fid, 'observed_at': i['created_at'],
            'source_release_id': _body_field(i.get('body') or '', 'release'),
            'requirement_link': {
                'requirement_id': f'REQ-#{req_issue["number"]}',
                'created_by_finding_id': f'F-#{_body_field(req_issue["body"], "from-finding").lstrip("#")}',
                'accepted_at': req_issue['created_at']},
            'target': {'release_id': v['release_id'], 'service_id': v['service_id'],
                       'due_at': _body_field(req_issue['body'], 'due'),
                       'verified_at': v['verified_at'],
                       'manifest_complete': v['manifest_complete'],
                       'coverage_complete': v['coverage_complete'],
                       'selected_path_valid': v['selected_path_valid'],
                       'closed': v['closed'], 'test_passed': v['test_passed']}})

    return {
        'schema_version': 'liveops-case-1',
        'release_id': facts['cutoff_tag'],
        'source_cutoff': cutoff, 'audit_time': audit,
        'policy': {'version': policy['version'],
                   'waivable_gates': policy['waivable_gates'],
                   'waiver_approvers': policy['waiver_approvers'],
                   'exclusion_authorities': policy['exclusion_authorities']},
        'scope': {'service_id': policy['service_id'],
                  'approved_sources': policy['approved_sources'] or APPROVED_SOURCES,
                  'finding_observation_complete': facts.get('finding_observation_complete', True),
                  'collector_attestation': {
                      'complete': facts['collection_rule_sha'] is not None,
                      'query_rule_hash': facts['collection_rule_sha'],
                      'collector_version': COLLECTOR_VERSION,
                      'source_type': 'LIVE_REPO_RECORDS_SELF_ATTESTED',
                      'inventory_sha256': sha(raw),
                      'inventory_source_sha256': facts.get('inventory_source_sha256'),
                      'inventory_source_ref': facts.get('inventory_source_ref')}},
        'raw_signals': raw, 'dispositions': dispositions, 'candidates': candidates,
        'gates': case_gates,
        'artifact': {'expected_sha256': frozen.get('artifact_sha256'),
                     'deployed_sha256': facts['asset_sha']},
        'snapshot': snapshot, 'findings': findings,
        'time_model': 'LIVE_REPO_OPS_REAL_CLOCK',
    }
