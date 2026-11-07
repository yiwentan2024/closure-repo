"""Secret/credential scan gate: exit 1 with findings when source contains keys.

Reads the worktree under app/; a committed credential makes the gate fail.
"""
import json, re, sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
PATTERNS = {
    'aws-access-key-id': r'AKIA[0-9A-Z]{16}',
    'private-key-block': r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
    'hardcoded-token': r'(?i)(api[_-]?key|secret[_-]?token)\s*=\s*["\'][A-Za-z0-9/+_-]{16,}["\']',
}
findings = []
for path in sorted((root / 'app').rglob('*.py')):
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        for name, pat in PATTERNS.items():
            if re.search(pat, line):
                findings.append({'rule': name, 'path': str(path.relative_to(root)), 'line': lineno})
print(json.dumps({'gate': 'scan', 'findings': findings}, indent=2))
sys.exit(1 if findings else 0)
