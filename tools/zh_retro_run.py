"""Run the three ZH retrospective audits locally and archive verdicts.

For each ZH scenario: build the case from the frozen owner-record ledger
(scenarios/owner_records), run the B2 closure auditor, and archive
case.json / result.json / B0 record review / B1 N-A record under
runs/zh_retro/<scenario>/.  ZH cases are adjudication records: B0 reviews the
recorded gate evidence, B1 is N/A per the batch specification (the original
system deployed no attestation tooling), B2 adjudicates. No injection step.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scenarios.build_case import build              # noqa: E402
from tools.zh_common import B1_NA, ZH_SCENARIOS, b0_review  # noqa: E402
from verifier.closure_audit import verify            # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'runs' / 'zh_retro'


def main():
    results = {}
    for sid in ZH_SCENARIOS:
        case = build(sid)
        result = verify(case)
        d = OUT / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / 'case.json').write_text(json.dumps(case, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                                     encoding='utf-8')
        (d / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                                       encoding='utf-8')
        (d / 'B0_record_review.md').write_text(b0_review(sid, case), encoding='utf-8')
        (d / 'B1_na.md').write_text(B1_NA, encoding='utf-8')
        results[sid] = result
        print(f"{sid}: {result['machine_decision']}/{result['evidence_status']} "
              f"codes={result['failure_codes']}")
    print(f"\narchived under {OUT}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
