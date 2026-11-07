"""ZH retrospective audit step for the GitHub workflow.

No pipeline execution and no constructed events: B0 reviews the recorded gate
evidence, B1 records N/A (the original system deployed no attestation tooling),
and B2 adjudicates closure over the frozen owner-record ledger. Writes the same
evidence layout the collector expects (case/, results/, summary, manifest,
sha256sums). Run by e2-19cases.yml for scenario_id values starting with ZH-.
"""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scenarios.build_case import build          # noqa: E402
from tools.zh_common import B1_NA, b0_review, require_b2  # noqa: E402
from verifier.closure_audit import verify        # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--scenario', required=True)
    ap.add_argument('--variant', required=True)
    ap.add_argument('--replicate', type=int, default=1)
    ap.add_argument('--github-run-id', default='LOCAL_NOT_GITHUB')
    args = ap.parse_args()
    require_b2(args.variant)
    d = args.out
    (d / 'case').mkdir(parents=True, exist_ok=True)
    (d / 'results').mkdir(exist_ok=True)

    case = build(args.scenario)
    result = verify(case)
    (d / 'case' / 'case.json').write_text(
        json.dumps(case, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (d / 'results' / 'b2_decision.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    (d / 'B0_record_review.md').write_text(b0_review(args.scenario, case), encoding='utf-8')
    (d / 'B1_na.md').write_text(B1_NA, encoding='utf-8')

    observed = 'FAIL' if any(g['status'] != 'PASS' for g in case['gates']) else 'PASS'
    summary = {
        'scenario_id': args.scenario, 'variant': args.variant,
        'replicate_index': args.replicate,
        'source_type': 'OWNER_RECORDS_RETROSPECTIVE_AUDIT',
        'github_run_id': args.github_run_id,
        'completed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'observed_baseline_gate_result': observed,
        'variant_actions': {
            'B0': 'record review (B0_record_review.md in this artifact)',
            'B1': 'N/A — no attestation tooling deployed (B1_na.md)',
            'B2': 'adjudication (results/b2_decision.json)'},
        'actual_exit_codes': {'unittest': 'NOT_EXECUTED_RETROSPECTIVE',
                              'three_stage_checks': 'NOT_EXECUTED_RETROSPECTIVE',
                              'pinned_digest': 'NOT_EXECUTED_RETROSPECTIVE',
                              'attestation': 'NOT_APPLICABLE'},
        'b2_machine_decision': result['machine_decision'],
        'b2_failure_codes': result['failure_codes'],
        'b2_evidence_status': result['evidence_status'],
        'real_live_health_events_in_case': False,
        'scripted_A2_timestamps_used': False,
        'owner_record_ledger': True,
        'oracle_recorded_before_runs': True,
        'oracle_expectation': 'NOT_PREJUDGED',
        'batch_protocol': 'protocols/batch_protocol.md',
    }
    (d / 'results' / 'observed_summary.json').write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    manifest = []
    for p in sorted(d.rglob('*')):
        if p.is_file() and p.name not in ('sha256sums.txt', 'manifest.json'):
            manifest.append({'path': str(p.relative_to(d)), 'bytes': p.stat().st_size,
                             'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
    (d / 'manifest.json').write_text(
        json.dumps({'files': manifest, 'summary': summary}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    mh = hashlib.sha256((d / 'manifest.json').read_bytes()).hexdigest()
    (d / 'sha256sums.txt').write_text(
        ''.join(f"{x['sha256']}  {x['path']}\n" for x in manifest) + f'{mh}  manifest.json\n')
    print(json.dumps({'observed_review': observed,
                      'decision': result['machine_decision'],
                      'evidence_status': result['evidence_status'],
                      'codes': result['failure_codes']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
