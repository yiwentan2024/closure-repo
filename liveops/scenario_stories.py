"""Sixteen scenario stories: a normal daily release sequence plus one disclosed,
plausible human/process deviation per scenario. Operations use git, GitHub,
Docker and HTTP; nothing edits the derived audit case directly.

Scenarios share one batch repository: each works in its own evidence namespace
(evidence/<SID>/), on its own feature branch, and freezes its own annotated
release/<SID> tag whose tagger date is that scenario's real cutoff.
"""
from __future__ import annotations
import datetime as dt
import json
import sys

from . import ghutil as G
from . import roles as R


def _fmt(t: dt.datetime):
    return t.astimezone(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def normal_release(ctx: R.Ctx, feature='version-bump', admin_merge=False, deploy_env=None):
    """The ordinary daily path every story starts from."""
    G.sh(['git', 'checkout', 'main'], cwd=ctx.repo)
    G.pull(ctx.repo)
    G.sh(['git', 'checkout', '-B', f'feat/{ctx.sid}-release'], cwd=ctx.repo)
    ctx.say('dev', f'created branch feat/{ctx.sid}-release from latest main')
    R.dev_feature_branch(ctx, kind=feature)
    pr = R.dev_open_pr(ctx, f'Release cycle {ctx.sid}',
                       f'kind: release-pr\ncycle: {ctx.sid}\n\nRegular version bump release.')
    R.run_gate(ctx, 'unit', [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'])
    R.run_gate(ctx, 'scan', [sys.executable, 'tools/scan_gate.py'])
    sha = G.commit_all(ctx.repo, f'ci: record gate verdicts for release PR ({ctx.sid})')
    G.push(ctx.repo, 'HEAD')
    ctx.say('pipeline', f'committed {sha} — gate verdicts stored to evidence/{ctx.sid}/gates.jsonl')
    R.merge_pr(ctx, pr, admin=admin_merge)
    sha = R.build_artifact(ctx)
    R.deploy_and_probe(ctx, env_extra=deploy_env)
    R.open_signal_issues(ctx)
    return {'pr': pr, 'sha': sha}


def triage_all(ctx: R.Ctx, skip=()):
    for sid, no in _signal_pairs(ctx):
        if sid in skip:
            continue
        R.oncall_triage(ctx, sid, no)


def story_n01(ctx):
    """Control: nothing deviates. Everyone does their job; release closes."""
    info = normal_release(ctx)
    triage_all(ctx)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Regular release; all gates green.',
                  snap_name=ctx.snap_main)
    return {'deviation': 'none — normal release'}


def story_n02(ctx):
    """Control: scan fails on a committed credential; the release manager
    waives it per policy (formal PR comment + waiver record)."""
    info = normal_release(ctx, feature='plant-credential')
    R.record_waiver(ctx, 'scan', info['pr'])
    sha = G.commit_all(ctx.repo, f'release: record scan waiver per policy ({ctx.sid})')
    G.push(ctx.repo)
    ctx.say('release-manager', f'committed {sha} — waiver record stored to evidence/{ctx.sid}/gates.jsonl')
    triage_all(ctx)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Scan finding waived per policy.',
                  snap_name=ctx.snap_main)
    return {'deviation': 'none — authorized waiver (scan gate failed, waiver policy-backed)'}


def story_n03(ctx):
    """Control: post-release bug handled properly end-to-end."""
    info = normal_release(ctx)
    triage_all(ctx)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Regular release.', snap_name=ctx.snap_main)
    finding = R.report_finding(ctx, 'Users report /version returns stale data after the release.')
    due = _fmt(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
    req = R.open_requirement(ctx, finding, due_iso=due)
    G.sh(['git', 'checkout', '-B', f'fix/{ctx.sid}-finding'], cwd=ctx.repo)
    ctx.say('dev', f'created branch fix/{ctx.sid}-finding for the hotfix')
    pr = R.fix_pr(ctx, req, finding, good=True)
    R.merge_pr(ctx, pr)
    rc, unit_output = R.run_unit(ctx)
    sha = R.build_artifact(ctx)
    R.release_tag(ctx, ctx.tag_target, sha, notes='Hotfix release for post-release finding.',
                  snap_name=ctx.snap_target)
    R.verify_target(ctx, test_passed=(rc == 0), unit_exit_code=rc,
                    unit_output=unit_output)
    return {'deviation': 'none — successor obligation fulfilled', 'wait_until': due}


def story_b01(ctx):
    """Baseline: a regression slips through an admin override, the required
    unit gate fails, the release ships anyway; audit blocks."""
    info = normal_release(ctx, feature='break-health', admin_merge=True)
    triage_all(ctx)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Release cut after check override.',
                  snap_name=ctx.snap_main)
    return {'deviation': 'red unit gate shipped via admin override (failing test on main)'}


def story_a101(ctx):
    """A1: gates stay green (HTTP 200) but the qa SLO-breach signal is never
    triaged — alert fatigue; no effective disposition exists at cutoff."""
    info = normal_release(ctx, deploy_env={'QA_SLOW_MS': '900'})
    slo = [sid for sid, no in _signal_pairs(ctx)
           if _payload(ctx, sid).get('slo_exceeded')]
    if not slo:
        raise RuntimeError('story precondition failed: qa probe did not exceed SLO')
    ctx.say('on-call', f'NOT triaging {slo[0]} — alert left sitting in the queue (nobody acts)')
    triage_all(ctx, skip=set(slo))
    R.release_tag(ctx, ctx.tag_main, info['sha'],
                  notes='Release cut; one alert still untriaged in the queue.',
                  snap_name=ctx.snap_main)
    return {'deviation': f'signal {slo[0]} left untriaged (no effective disposition)'}


def story_a105(ctx):
    """A1: after the release someone 'fixes a typo' in the stored probe log,
    editing the recorded payload without its digest — log doctoring."""
    info = normal_release(ctx)
    triage_all(ctx)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Regular release.', snap_name=ctx.snap_main)
    inv = ctx.ev / 'raw/inventory.jsonl'
    rows = [json.loads(x) for x in inv.read_text().splitlines() if x.strip()]
    ctx.say('ops', f"editing stored evidence {ctx.sid}/raw/inventory.jsonl: signal "
                   f"{rows[1]['signal_id']} payload latency_ms "
                   f"{rows[1]['payload']['latency_ms']}->120, slo_exceeded->False, "
                   f"digest field left untouched")
    rows[1]['payload']['latency_ms'] = 120
    rows[1]['payload']['slo_exceeded'] = False
    inv.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows))
    sha = G.commit_all(ctx.repo, f'ops: fix probe log formatting nit ({ctx.sid})')
    G.push(ctx.repo)
    ctx.say('ops', f'committed {sha} — the edit disguised as a log formatting fix')
    return {'deviation': 'stored raw payload edited post-release without re-computing digest'}


def story_a201(ctx):
    """A2: a routine post-release health sweep runs after the cutoff and its
    events are ingested and triaged — but they are late."""
    info = normal_release(ctx)
    triage_all(ctx)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Regular release.', snap_name=ctx.snap_main)
    ctx.say('ops', 'running the routine post-release health sweep (AFTER the cutoff tag)')
    G.sh([sys.executable, 'tools/deploy_health.py', '--out', ctx.ev.as_posix(), '--no-gates'],
         cwd=ctx.repo, check=False)
    for ev in R.read_jsonl(ctx.ev / 'raw/inventory.jsonl')[3:]:
        pl = ev['payload']
        ctx.say('ops-bot', f"probed {pl['stage']:<8} HTTP {pl['http_status']} "
                           f"{pl['latency_ms']}ms -> signal {ev['signal_id']} "
                           f"(event_time {ev['event_time']} > cutoff)")
    sha = G.commit_all(ctx.repo, f'ops: routine post-release health sweep ({ctx.sid})')
    G.push(ctx.repo)
    ctx.say('ops', f'committed {sha} — post-cutoff sweep events ingested into the evidence store')
    R.open_signal_issues(ctx)  # alert router pages on the new sweep signals too
    for sid, no in _signal_pairs(ctx):
        if not _already_triaged(ctx, sid):
            R.oncall_triage(ctx, sid, no, note='routine sweep healthy; acknowledged')
    return {'deviation': 'post-cutoff sweep events ingested as release evidence'}


def story_a202(ctx):
    """A2: the requirement ticket references the WRONG finding (a copy-paste
    typo); the fix itself is fine but the obligation binding is broken."""
    info = normal_release(ctx)
    triage_all(ctx)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Regular release.', snap_name=ctx.snap_main)
    finding = R.report_finding(ctx, 'Users report duplicated entries after the release.')
    wrong_ref = finding - 1 if finding > 1 else finding + 1  # a plausible typo target
    due = _fmt(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
    req = R.open_requirement(ctx, finding, due_iso=due, from_finding_no=wrong_ref)
    G.sh(['git', 'checkout', '-B', f'fix/{ctx.sid}-finding'], cwd=ctx.repo)
    ctx.say('dev', f'created branch fix/{ctx.sid}-finding for the hotfix')
    pr = R.fix_pr(ctx, req, finding, good=True)
    R.merge_pr(ctx, pr)
    rc, unit_output = R.run_unit(ctx)
    sha = R.build_artifact(ctx)
    R.release_tag(ctx, ctx.tag_target, sha, notes='Hotfix release for the reported finding.',
                  snap_name=ctx.snap_target)
    R.verify_target(ctx, test_passed=(rc == 0), unit_exit_code=rc,
                    unit_output=unit_output)
    return {'deviation': f'requirement #{req} bound to finding #{wrong_ref} instead of #{finding}',
            'wait_until': due}


def story_a203(ctx):
    """A2: the rushed hotfix is broken — the target release's unit run fails
    and the verification record says so."""
    info = normal_release(ctx)
    triage_all(ctx)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Regular release.', snap_name=ctx.snap_main)
    finding = R.report_finding(ctx, 'Checkout intermittently 500s on legacy clients.')
    due = _fmt(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
    req = R.open_requirement(ctx, finding, due_iso=due)
    G.sh(['git', 'checkout', '-B', f'fix/{ctx.sid}-finding'], cwd=ctx.repo)
    ctx.say('dev', f'created branch fix/{ctx.sid}-finding for the rushed hotfix')
    pr = R.fix_pr(ctx, req, finding, good=False)
    R.merge_pr(ctx, pr)
    rc, unit_output = R.run_unit(ctx)
    sha = R.build_artifact(ctx)
    R.release_tag(ctx, ctx.tag_target, sha, notes='Hotfix release (rushed).',
                  snap_name=ctx.snap_target)
    R.verify_target(ctx, test_passed=(rc == 0), unit_exit_code=rc,
                    unit_output=unit_output)
    return {'deviation': 'target release validation failed (bad hotfix)',
            'wait_until': due}


def story_b02(ctx):
    """Baseline: after the release is published, the stored artifact bytes are
    overwritten (storage/registry overwrite accident); the pinned digest and
    the deployed asset now disagree."""
    info = normal_release(ctx)
    triage_all(ctx)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Regular release.',
                  snap_name=ctx.snap_main)
    R.overwrite_release_asset(ctx, ctx.tag_main)
    return {'deviation': 'release asset bytes overwritten after publication (digest mismatch)'}


def story_a102(ctx):
    """A1: duplicate triage — during shift handover the second on-call also
    records an effective resolution for a signal that is already resolved."""
    info = normal_release(ctx)
    pairs = _signal_pairs(ctx)
    target = pairs[0]
    triage_all(ctx, skip={target[0]})
    R.oncall_triage(ctx, target[0], target[1])
    ctx.say('on-call-2', f'second on-call (did not see the first triage) ALSO resolves '
                         f'{target[0]} — duplicate effective disposition')
    R.oncall_triage(ctx, target[0], target[1], note='shift handover; also resolved')
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Regular release.',
                  snap_name=ctx.snap_main)
    return {'deviation': f'signal {target[0]} carries two effective dispositions'}


def story_a103(ctx):
    """A1: the alert is acknowledged but explicitly unresolved — the triage
    record says UNRESOLVED and the ticket stays open past the cutoff."""
    info = normal_release(ctx)
    pairs = _signal_pairs(ctx)
    target = pairs[1]
    triage_all(ctx, skip={target[0]})
    ctx.say('on-call', f'acknowledging {target[0]} as UNRESOLVED (still investigating); '
                       f'ticket stays open')
    R.oncall_triage(ctx, target[0], target[1], decision='UNRESOLVED', close_issue=False)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Release cut with an open alert.',
                  snap_name=ctx.snap_main)
    return {'deviation': f'signal {target[0]} disposition UNRESOLVED, candidate OPEN at cutoff'}


def story_a104(ctx):
    """A1: the developer marks an alert AUTHORIZED_EXCLUDED themselves, but the
    policy reserves exclusions for qa-lead — the exclusion is not authorized."""
    info = normal_release(ctx)
    pairs = _signal_pairs(ctx)
    target = pairs[2]
    triage_all(ctx, skip={target[0]})
    ctx.say('dev', f'excluding {target[0]} as "monitoring noise" — but policy reserves '
                   f'exclusions for qa-lead, and the actor is {ctx.login}')
    R.oncall_triage(ctx, target[0], target[1], decision='AUTHORIZED_EXCLUDED',
                    note='monitoring noise for this release; excluding', actor=ctx.login)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Release cut with an excluded alert.',
                  snap_name=ctx.snap_main)
    return {'deviation': f'signal {target[0]} excluded by an actor outside policy authorities'}


def story_a204(ctx):
    """A2: after the release someone "polishes" the frozen release record on
    main; the tagged (frozen) version in git history stays untouched."""
    info = normal_release(ctx)
    triage_all(ctx)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Regular release.',
                  snap_name=ctx.snap_main)
    snap = ctx.repo / ctx.snap_main
    row = json.loads(snap.read_text())
    row['artifact_sha256'] = 'sha256:unauthorized-rewrite-after-freeze'
    snap.write_text(json.dumps(row, indent=2, sort_keys=True) + '\n')
    sha = G.commit_all(ctx.repo, f'chore: tidy release record ({ctx.sid})')
    G.push(ctx.repo)
    ctx.say('ops', f'edited {ctx.snap_main} AFTER the freeze (artifact reference rewritten); '
                   f'committed {sha}; the tagged frozen copy in git history is untouched')
    return {'deviation': 'current snapshot payload rewritten after the freeze'}


def story_u01(ctx):
    """NFI: the evidence collection rule declaration is missing — the collector
    cannot attest the completeness of the observation scope."""
    info = normal_release(ctx)
    triage_all(ctx)
    rule = ctx.repo / 'COLLECTION_RULE.md'
    rule.unlink()
    sha = G.commit_all(ctx.repo, f'chore: tidy repo root, drop stale ops doc ({ctx.sid})')
    G.push(ctx.repo)
    ctx.say('ops', f'removed COLLECTION_RULE.md ({sha}) — collector can no longer attest '
                   f'observation-scope completeness (insufficiency, not a violation)')
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Regular release.',
                  snap_name=ctx.snap_main)
    return {'deviation': 'collection-rule declaration absent; scope completeness unverifiable'}


def story_u02(ctx):
    """NFI: a post-release bug is reported and fully remediated, but nobody ever
    filled in which release the finding belongs to (attribution unresolved)."""
    info = normal_release(ctx)
    triage_all(ctx)
    R.release_tag(ctx, ctx.tag_main, info['sha'], notes='Regular release.', snap_name=ctx.snap_main)
    finding = R.report_finding(ctx, 'Users report stale data after SOME release '
                                    '(reporter did not fill in the release field).',
                               attribute_release=False)
    due = _fmt(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
    req = R.open_requirement(ctx, finding, due_iso=due)
    G.sh(['git', 'checkout', '-B', f'fix/{ctx.sid}-finding'], cwd=ctx.repo)
    ctx.say('dev', f'created branch fix/{ctx.sid}-finding for the hotfix')
    pr = R.fix_pr(ctx, req, finding, good=True)
    R.merge_pr(ctx, pr)
    rc, unit_output = R.run_unit(ctx)
    sha = R.build_artifact(ctx)
    R.release_tag(ctx, ctx.tag_target, sha, notes='Hotfix release for the reported finding.',
                  snap_name=ctx.snap_target)
    R.verify_target(ctx, test_passed=(rc == 0), unit_exit_code=rc,
                    unit_output=unit_output)
    return {'deviation': 'finding source release attribution unresolved (field never filled)',
            'wait_until': due}


# ---------------------------------------------------------------- helpers
def _signal_pairs(ctx):
    pairs = []
    for ev in R.read_jsonl(ctx.ev / 'raw/inventory.jsonl'):
        pairs.append((ev['signal_id'], R.issue_no_for_signal(ctx, ev['signal_id'])))
    return pairs


def _payload(ctx, sid):
    for ev in R.read_jsonl(ctx.ev / 'raw/inventory.jsonl'):
        if ev['signal_id'] == sid:
            return ev['payload']
    raise KeyError(sid)


def _already_triaged(ctx, sid):
    return any(d['signal_id'] == sid for d in R.read_jsonl(ctx.ev / 'dispositions.jsonl'))


STORIES = {
    'N01': {'title': 'normal release, everything triaged', 'run': story_n01},
    'N02': {'title': 'scan finding waived per policy', 'run': story_n02},
    'N03': {'title': 'post-release bug fully remediated', 'run': story_n03},
    'B01': {'title': 'red unit gate shipped via override', 'run': story_b01},
    'B02': {'title': 'release asset overwritten after publication', 'run': story_b02},
    'A101': {'title': 'SLO-breach alert never triaged', 'run': story_a101},
    'A102': {'title': 'duplicate triage of one signal', 'run': story_a102},
    'A103': {'title': 'alert acknowledged but unresolved', 'run': story_a103},
    'A104': {'title': 'exclusion by unauthorized actor', 'run': story_a104},
    'A105': {'title': 'probe log altered after release', 'run': story_a105},
    'A201': {'title': 'post-cutoff sweep ingested as evidence', 'run': story_a201},
    'A202': {'title': 'requirement bound to the wrong finding', 'run': story_a202},
    'A203': {'title': 'broken hotfix fails target validation', 'run': story_a203},
    'A204': {'title': 'frozen snapshot rewritten after release', 'run': story_a204},
    'U01': {'title': 'collection-rule declaration missing', 'run': story_u01},
    'U02': {'title': 'finding attribution never filled in', 'run': story_u02},
}
