"""Select and freeze owner-provided release records for the ZH audits.

The ZH cases are retrospective audits over release-disposition records
provided by the source project. The provider's export workbook is identified
by the SHA-256 recorded in the output; it is not included in this tree. This
module performs the declared one-time selection that produces
scenarios/owner_records/:

  owner_release_records.xlsx   the selected records, human-readable

Selection rules (fixed at generation time):
  1. Only the three releases that are the subjects of the three ZH audits
     are included (R-FB-2026-07-8.9.3.6, R-FB-2026-08-8.9.3.6-P1,
     R-FB-2026-08-NORMAL-07): 31 rows.
  2. All other releases are excluded, including R-FB-2026-08-SCRIPT1
     (labeled as scripted by the provider's own manifest).
  3. The redundant `status` column is dropped (the provider's dictionary
     declares it must equal `disposition` in every row).
  4. Role names are translated per the declared vocabulary; every other
     field value is copied verbatim.

Standard library only (the xlsx is written via zipfile+XML).  Usage:
  python -m scenarios.owner_records_select --raw <export-dir> [--out DIR]
"""
import argparse
import csv
import hashlib
import html
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_PARENT = ROOT.parents[1] if len(ROOT.parents) > 1 else ROOT.parent
RAW_DEFAULT = RAW_PARENT / 'ZHX-RC-DS-V23-20260820_V1.8_source'
OUT_DEFAULT = ROOT / 'scenarios' / 'owner_records'

XLSX_NAME = 'ZHX-RC-B-V23-RAW-01.xlsx'
PROVIDER_EXPORT_SHA256 = '5ba663605096de7b4ea65fb5d682ddbdb78b2e6aa3c064bf106637f632379459'
ROLE_EN = {'处置审核岗': 'Disposition Reviewer',
           '操作执行岗': 'Operations Executor',
           '复核岗': 'Verification Officer'}
INCLUDED_RELEASES = {'R-FB-2026-07-8.9.3.6', 'R-FB-2026-08-8.9.3.6-P1',
                     'R-FB-2026-08-NORMAL-07'}
OUTPUT_COLUMNS = ['operation_id', 'signal_id', 'release_id', 'signal_type',
                  'disposition', 'operation_time', 'operation_receive_time',
                  'effective_from', 'effective_to', 'actor_anon',
                  'role_at_time', 'version', 'supersedes', 'superseded_by',
                  'authorization_ref']


def parse_xlsx(path):
    z = zipfile.ZipFile(path)
    xml = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
    rows = re.findall(r'<row[^>]*>.*?</row>', xml, re.S)
    def cells(row):
        out = []
        for _t, body in re.findall(r'<c[^>]*?(?: t="(\w+)")?[^>]*>(.*?)</c>', row, re.S):
            m = re.search(r'<t[^>]*>([^<]*)</t>', body) or re.search(r'<v>([^<]*)</v>', body)
            out.append(html.unescape(m.group(1)) if m else '')
        return out
    hdr = cells(rows[0])
    return hdr, [dict(zip(hdr, cells(r))) for r in rows[1:]]


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def esc(s):
    return html.escape(str(s), quote=False)


def write_xlsx(path, columns, rows):
    """Write a minimal single-sheet xlsx with inline strings (stdlib only)."""
    col_letters = [chr(ord('A') + i) for i in range(len(columns))]
    xml_rows = []
    hdr_cells = ''.join(
        f'<c r="{L}1" t="inlineStr"><is><t>{esc(c)}</t></is></c>'
        for c, L in zip(columns, col_letters))
    xml_rows.append(f'<row r="1">{hdr_cells}</row>')
    for i, row in enumerate(rows, start=2):
        cells = ''.join(
            f'<c r="{L}{i}" t="inlineStr"><is><t>{esc(row.get(c, ""))}</t></is></c>'
            for c, L in zip(columns, col_letters))
        xml_rows.append(f'<row r="{i}">{cells}</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>' + ''.join(xml_rows) + '</sheetData></worksheet>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Release Disposition Records" sheetId="1" r:id="rId1"/></sheets>'
                '</workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '</Relationships>')
    # deterministic zip (fixed timestamp ensures byte-identical replay)
    import time as _time
    _fixed = _time.struct_time((2026, 1, 1, 0, 0, 0, 3, 1, 0))  # 2026-01-01 UTC
    parts = [('[Content_Types].xml', content_types),
             ('_rels/.rels', rels),
             ('xl/workbook.xml', workbook),
             ('xl/_rels/workbook.xml.rels', wb_rels),
             ('xl/worksheets/sheet1.xml', sheet)]
    with zipfile.ZipFile(path, 'w') as zf:
        for name, data in parts:
            zi = zipfile.ZipInfo(name, date_time=_fixed[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o600 << 16
            zf.writestr(zi, data)


def select(raw_dir=RAW_DEFAULT, out_dir=OUT_DEFAULT):
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    xlsx = raw_dir / XLSX_NAME
    xlsx_sha = sha256_file(xlsx)

    v23 = list(csv.DictReader((raw_dir / 'received_manifests' / 'v23_source_manifest.csv')
                              .read_text(encoding='utf-8-sig').splitlines()))[0]
    assert xlsx_sha == PROVIDER_EXPORT_SHA256, 'workbook bytes differ from the pinned provider export'
    assert xlsx_sha == v23['file_sha256'], 'workbook bytes differ from provider manifest'

    hdr, rows = parse_xlsx(xlsx)
    assert len(rows) == 52, f'unexpected row count {len(rows)}'

    row_no = {r['operation_id']: i + 2 for i, r in enumerate(rows)}
    selected = [r for r in rows if r['release_id'] in INCLUDED_RELEASES]
    assert len(selected) == 31, f'expected 31 selected rows, got {len(selected)}'

    vocab = {}
    entries = []
    for r in selected:
        orig = r['role_at_time']
        assert orig in ROLE_EN, f'undeclared role: {orig!r}'
        vocab.setdefault(orig, ROLE_EN[orig])
        entries.append({**r, 'role_at_time': ROLE_EN[orig],
                        'source_row': row_no[r['operation_id']]})

    out_dir.mkdir(parents=True, exist_ok=True)

    out_xlsx = out_dir / 'owner_release_records.xlsx'
    write_xlsx(out_xlsx, OUTPUT_COLUMNS,
               [{c: e[c] for c in OUTPUT_COLUMNS} for e in entries])
    return {'rows': len(entries), 'sha256': xlsx_sha, 'out': str(out_xlsx)}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--raw', default=str(RAW_DEFAULT))
    ap.add_argument('--out', default=str(OUT_DEFAULT))
    a = ap.parse_args()
    r = select(a.raw, a.out)
    print(f"selected {r['rows']} rows -> {r['out']} (source sha256 {r['sha256'][:12]})")
