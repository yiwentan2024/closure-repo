"""Materialize the provider's R4 evidence as a derived, verbatim-event copy.

The provider export carries two export-metadata columns in every data row
(`data_usage_boundary`, `publication_clearance`). This module removes exactly
those two columns and copies every event field verbatim; the provider package
(not included in this tree) is never modified. Output under
`scenarios/owner_records/R4_EVIDENCE/`:

  R4_*.csv       same file names, event fields verbatim
  provenance.csv original/derived SHA-256 and row counts
  README.md      what the copy is and what was removed

The copy is case-external context for the ZH-R4 record review; it is not an
input to the B2 closure verdict. Standard library only. Usage:
  python -m scenarios.r4_evidence_select [--raw DIR] [--out DIR]
"""
import argparse
import csv
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_PARENT = ROOT.parents[1] if len(ROOT.parents) > 1 else ROOT.parent
RAW_DEFAULT = RAW_PARENT / 'ZHX-RC-DS-V23-20260820_V1.8_source'
OUT_DEFAULT = ROOT / 'scenarios' / 'owner_records' / 'R4_EVIDENCE'
MANIFEST_NAME = 'R4_EVIDENCE_MANIFEST.csv'
REMOVE_COLUMNS = ('data_usage_boundary', 'publication_clearance')

README = """\
# R4 evidence (derived copy)

Ten provider CSV files covering the R4 release window. Every event field is
copied verbatim; the two export-metadata columns `data_usage_boundary` and
`publication_clearance` are removed. `provenance.csv` records the original and
derived SHA-256 for each file.

Identity note: operation `OP-SIG-FB-260820-010` appears here as a change event
on 2026-08-20 06:25Z, while the selected V23 ledger records the same id as a
deployment on 2026-08-18 14:12 local. The provider's v23 manifest marks the id
`EXCLUDED_AS_DUPLICATE_IDENTITY` / `EVIDENCE_IDENTITY_PENDING`. This copy is
context for the ZH-R4 record review; it is not a B2 closure input.
"""


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _provider_rows(raw_dir):
    manifest = Path(raw_dir) / 'received_manifests' / MANIFEST_NAME
    out = []
    for row in csv.DictReader(manifest.read_text(encoding='utf-8-sig').splitlines()):
        name = row['relative_path'].replace('\\', '/').split('/')[-1]
        src = Path(raw_dir) / 'R4_EVIDENCE' / name
        actual = sha256_file(src)
        if actual != row['sha256']:
            raise RuntimeError(f'{name}: provider bytes differ from manifest')
        out.append((name, src, row['sha256']))
    out.sort()
    if len(out) != 10:
        raise RuntimeError(f'expected 10 provider CSVs, got {len(out)}')
    return out


def select(raw_dir=RAW_DEFAULT, out_dir=OUT_DEFAULT):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    provenance = []
    for name, src, original_sha in _provider_rows(raw_dir):
        with src.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
        missing = [c for c in REMOVE_COLUMNS if c not in fields]
        if missing:
            raise RuntimeError(f'{name}: expected metadata columns {missing}')
        kept = [c for c in fields if c not in REMOVE_COLUMNS]
        dst = out_dir / name
        with dst.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=kept, lineterminator='\n')
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row[c] for c in kept})
        provenance.append({'file': name, 'data_rows': len(rows),
                           'original_sha256': original_sha,
                           'derived_sha256': sha256_file(dst),
                           'removed_columns': ';'.join(REMOVE_COLUMNS)})

    with (out_dir / 'provenance.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['file', 'data_rows', 'original_sha256',
                                               'derived_sha256', 'removed_columns'],
                                lineterminator='\n')
        writer.writeheader()
        writer.writerows(provenance)
    (out_dir / 'README.md').write_text(README, encoding='utf-8')
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', default=str(RAW_DEFAULT))
    parser.add_argument('--out', default=str(OUT_DEFAULT))
    args = parser.parse_args()
    rows = select(args.raw, args.out)
    print(f'derived {len(rows)} R4 evidence files -> {args.out}')


if __name__ == '__main__':
    main()
