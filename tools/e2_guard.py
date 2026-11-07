"""Reject real-data case IDs without source-verified records."""
import sys
from pathlib import Path
from scenarios.build_case import SCENARIOS
sid=sys.argv[1] if len(sys.argv)>1 else ''
if sid not in SCENARIOS:
    raise SystemExit('UNKNOWN_SCENARIO:'+sid)
if SCENARIOS[sid]['source']!='CONTROLLED':
    derived=Path(__file__).resolve().parents[1]/'scenarios'/'owner_records'/'owner_release_records.xlsx'
    if not derived.is_file():
        raise SystemExit(f'BLOCKED_REAL_DATA:{sid}: project source export + independent facts not provided')
    print('ZH_RECORDS_PRESENT:', sid)
else:
    print('CONTROLLED_CASE_ENABLED:', sid)
