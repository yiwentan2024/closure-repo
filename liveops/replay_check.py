"""Verify an assembled live case against the recorded expected outcomes (read-only).

The verifier is imported unmodified from verifier.closure_audit; the recorded
outcomes are parsed read-only from protocols/oracle_locked.csv. Match rule equals
the frozen engineering suite (tests/test_e2_cases.py): decision equality plus
recorded codes being a subset of observed codes.
"""
from __future__ import annotations
import csv
from pathlib import Path

from verifier.closure_audit import verify

ROOT = Path(__file__).resolve().parents[1]


def load_oracle(repo_root: Path = ROOT):
    rows = {}
    with (repo_root / 'protocols/oracle_locked.csv').open(encoding='utf-8') as f:
        for row in csv.DictReader(line for line in f if not line.startswith('#')):
            codes = [c for c in (row.get('failure_codes') or '').split(';') if c]
            rows[row['scenario_id']] = {'decision': row['expected_decision'], 'codes': codes}
    return rows


def check(scenario_id: str, case: dict, repo_root: Path = ROOT):
    oracle = load_oracle(repo_root)[scenario_id]
    result = verify(case)
    matched = (result['machine_decision'] == oracle['decision']
               and set(oracle['codes']).issubset(set(result['failure_codes'])))
    return {'scenario_id': scenario_id,
            'oracle_decision': oracle['decision'], 'oracle_codes': oracle['codes'],
            'observed_decision': result['machine_decision'],
            'observed_codes': result['failure_codes'],
            'evidence_status': result['evidence_status'],
            'extra_codes': sorted(set(result['failure_codes']) - set(oracle['codes'])),
            'match': matched, 'result': result}
