"""In-repo closure audit entry (vendored into the audited repository).

Runs next to byte-identical copies of closure_audit.py and collector.py inside
the repository's audit/ directory. Dispatched by .github/workflows/
closure-audit.yml; assembles the case from THIS repository's own records only
(GITHUB_TOKEN-scoped reads) and writes case.json / result.json.
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))

from collector import fetch_live, assemble  # noqa: E402  (vendored copy)
from closure_audit import verify  # noqa: E402  (vendored copy)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', default='.', help='repository checkout (with full git history)')
    ap.add_argument('--out', default='audit_out')
    ap.add_argument('--full', default=os.environ.get('LIVEOPS_FULL_REPO'))
    ap.add_argument('--scenario', default=os.environ.get('LIVEOPS_SCENARIO'),
                    help='scenario cycle to audit (shared batch repository layout)')
    ap.add_argument('--allow-open', action='store_true',
                    help='exit 0 even when the verdict is OPEN (by default OPEN exits 2 so '
                         'the workflow run itself shows the blocking verdict as a red run)')
    args = ap.parse_args()
    full = args.full or os.environ.get('GITHUB_REPOSITORY')
    if not full:
        ap.error('target repository required (--full or LIVEOPS_FULL_REPO/GITHUB_REPOSITORY)')
    kwargs = {}
    if args.scenario:
        kwargs = {'cutoff_tag': f'release/{args.scenario}',
                  'target_tag': f'release/{args.scenario}-target',
                  'evidence_sub': f'evidence/{args.scenario}',
                  'snapshot_name': f'snapshots/{args.scenario}.json',
                  'scenario_id': args.scenario}
    facts = fetch_live(args.repo, full, **kwargs)
    case = assemble(facts)
    result = verify(case)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'case.json').write_text(json.dumps(case, indent=2, sort_keys=True, ensure_ascii=False) + '\n',
                                   encoding='utf-8')
    (out / 'result.json').write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + '\n',
                                     encoding='utf-8')
    print(f"{result['machine_decision']} {result['failure_codes']}")
    if result['machine_decision'] == 'OPEN' and not args.allow_open:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
