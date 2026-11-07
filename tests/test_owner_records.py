"""Audit assertions for the selected owner release records (xlsx).

Tests 1-9 require the provider package (kept outside this repository) and
verify the frozen xlsx and R4 copy against it. The final classes run everywhere.
"""
import csv
import json
import re
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scenarios.owner_records_select import (        # noqa: E402
    parse_xlsx, select, sha256_file, RAW_DEFAULT, OUT_DEFAULT, XLSX_NAME,
    INCLUDED_RELEASES, OUTPUT_COLUMNS)
from scenarios.r4_evidence_select import select as select_r4  # noqa: E402
from scenarios.build_case import (                  # noqa: E402
    build, ZH_ANCHORS, PROVIDER_EXPORT_SHA256, ZH_R4_VOUCHER, ZH_RECORDS)
from verifier.closure_audit import verify, sha       # noqa: E402

RAW = RAW_DEFAULT
SRC = RAW / XLSX_NAME
OUT = OUT_DEFAULT
XLSX = OUT / 'owner_release_records.xlsx'
R4_OUT = OUT / 'R4_EVIDENCE'
ARCHIVE = Path(__file__).resolve().parents[1] / 'runs' / 'zh_retro'
COMMITTED_SHA256 = '1944b225e238e360c192c26915505143d5192ae2f47d2bc0df0ca4b45af56fac'
CJK = re.compile(r'[\u4e00-\u9fff]')
ZH_IDS = tuple(sorted(ZH_ANCHORS))


def sheet_rows():
    _hdr, rows = parse_xlsx(XLSX)
    return rows


@unittest.skipUnless(SRC.is_file(), 'provider package not present (kept outside this repository)')
class TestOwnerPackageAssertions(unittest.TestCase):

    def test_1_selected_rows_are_verbatim(self):
        _hdr, src_rows = parse_xlsx(SRC)
        src_by_op = {r['operation_id']: r for r in src_rows}
        for e in sheet_rows():
            src = src_by_op[e['operation_id']]
            for col in OUTPUT_COLUMNS:
                got = e[col]
                want = src[col]
                if col == 'role_at_time':
                    # declared vocabulary translation
                    from scenarios.owner_records_select import ROLE_EN
                    self.assertEqual(got, ROLE_EN[want], e['operation_id'])
                else:
                    self.assertEqual(got, want, f'{e["operation_id"]}.{col}')

    def test_2_selection_is_audited_releases_only(self):
        rows = sheet_rows()
        self.assertEqual(len(rows), 31)
        self.assertEqual({r['release_id'] for r in rows}, INCLUDED_RELEASES)

    def test_3_english_surface(self):
        for e in sheet_rows():
            for col in OUTPUT_COLUMNS:
                self.assertIsNone(CJK.search(e[col]), f'{e["signal_id"]}.{col}')

    def test_4_all_three_releases_present(self):
        rows = sheet_rows()
        from scenarios.owner_records_select import INCLUDED_RELEASES
        for rel in INCLUDED_RELEASES:
            self.assertTrue(any(r['release_id'] == rel for r in rows), rel)

    def test_5_case_closure_against_xlsx(self):
        rows = sheet_rows()
        for sid in ZH_IDS:
            case = build(sid)
            rel = ZH_ANCHORS[sid]['release']
            want = {r['signal_id'] for r in rows if r['release_id'] == rel}
            self.assertEqual({s['signal_id'] for s in case['raw_signals']}, want, sid)

    def test_6_replay_reproduces_xlsx(self):
        with tempfile.TemporaryDirectory() as td:
            select(RAW, Path(td))
            a = sha256_file(XLSX)
            b = sha256_file(Path(td) / 'owner_release_records.xlsx')
            self.assertEqual(a, b)

    def test_7_source_hash_matches_provider_manifest(self):
        import csv as _csv
        v23 = list(_csv.DictReader((RAW / 'received_manifests' / 'v23_source_manifest.csv')
                                   .read_text(encoding='utf-8-sig').splitlines()))[0]
        self.assertEqual(v23['file_sha256'], sha256_file(SRC))
        self.assertEqual(v23['file_sha256'], PROVIDER_EXPORT_SHA256)

    def test_8_r4_derived_copy_is_faithful_and_replayable(self):
        import csv as _csv, tempfile as _tempfile
        manifest = list(_csv.DictReader(
            (RAW / 'received_manifests' / 'R4_EVIDENCE_MANIFEST.csv')
            .read_text(encoding='utf-8-sig').splitlines()))
        self.assertEqual(len(manifest), 10)
        prov = list(_csv.DictReader((R4_OUT / 'provenance.csv').read_text().splitlines()))
        self.assertEqual({r['file'] for r in prov}, {r['relative_path'].split('\\')[-1]
                                                    for r in manifest})
        for row in manifest:
            name = row['relative_path'].split('\\')[-1]
            src = RAW / 'R4_EVIDENCE' / name
            self.assertEqual(sha256_file(src), row['sha256'], name)
            got = [r for r in prov if r['file'] == name][0]
            self.assertEqual(got['original_sha256'], row['sha256'], name)
            self.assertEqual(got['derived_sha256'], sha256_file(R4_OUT / name), name)
            text = (R4_OUT / name).read_text(encoding='utf-8')
            self.assertNotIn('data_usage_boundary', text, name)
            self.assertNotIn('publication_clearance', text, name)
        with _tempfile.TemporaryDirectory() as td:
            select_r4(RAW, Path(td))
            for p in sorted(R4_OUT.iterdir()):
                if p.is_file():
                    self.assertEqual(p.read_bytes(), (Path(td) / p.name).read_bytes(), p.name)

    def test_9_case_voucher_chain_matches_provider(self):
        import csv as _csv
        case = build('ZH-R4')
        voucher = case['zh_anchor']['r4_voucher']
        self.assertEqual(voucher['role'], 'case-external record-review context; not a B2 input')
        manifest = list(_csv.DictReader(
            (RAW / 'received_manifests' / 'R4_EVIDENCE_MANIFEST.csv')
            .read_text(encoding='utf-8-sig').splitlines()))
        for row in manifest:
            name = row['relative_path'].split('\\')[-1]
            self.assertEqual(voucher['provider_original_sha256'][name], row['sha256'])
            self.assertEqual(voucher['derived_sha256'][name], sha256_file(R4_OUT / name))
        change_010 = (R4_OUT / 'R4_CHANGE_RECORD_260820_010.csv').read_text(encoding='utf-8')
        self.assertIn('OP-SIG-FB-260820-010,2026-08-20T06:25:00Z', change_010)


class TestCommittedRecords(unittest.TestCase):
    """Runs in public clones; the selected xlsx and R4 copy ship with the repo."""

    def test_selected_xlsx_bytes_are_frozen(self):
        self.assertEqual(sha256_file(XLSX), COMMITTED_SHA256)
        self.assertEqual(sha256_file(ZH_RECORDS), COMMITTED_SHA256)

    def test_selection_shape_and_english_surface(self):
        rows = sheet_rows()
        self.assertEqual(len(rows), 31)
        self.assertEqual({r['release_id'] for r in rows}, INCLUDED_RELEASES)
        self.assertEqual(len({r['operation_id'] for r in rows}), 31)
        for e in rows:
            for col in OUTPUT_COLUMNS:
                self.assertIsNone(CJK.search(e[col]), f'{e["signal_id"]}.{col}')

    def test_source_hash_chain(self):
        for sid in ZH_IDS:
            case = build(sid)
            anchor = case['zh_anchor']['source_records']
            self.assertEqual(anchor['committed_sha256'], COMMITTED_SHA256, sid)
            self.assertEqual(anchor['provider_export_sha256'], PROVIDER_EXPORT_SHA256, sid)
            att = case['scope']['collector_attestation']
            self.assertEqual(att['materialized_records_sha256'], COMMITTED_SHA256, sid)
            self.assertEqual(att['declared_export_sha256'], PROVIDER_EXPORT_SHA256, sid)

    def test_r4_voucher_committed_and_stripped(self):
        prov = list(csv.DictReader((R4_OUT / 'provenance.csv').read_text().splitlines()))
        self.assertEqual(len(prov), 10)
        for row in prov:
            text = (R4_OUT / row['file']).read_text(encoding='utf-8')
            self.assertNotIn('data_usage_boundary', text)
            self.assertNotIn('publication_clearance', text)
            self.assertEqual(sha256_file(R4_OUT / row['file']), row['derived_sha256'])
        case = build('ZH-R4')
        self.assertEqual(len(case['zh_anchor']['r4_voucher']['derived_sha256']), 10)
        self.assertEqual(case['zh_anchor']['r4_voucher']['directory'],
                         'scenarios/owner_records/R4_EVIDENCE')


class TestZhAdjudicationsReproducible(unittest.TestCase):
    """Runs in public clones (the xlsx ships with the repo)."""

    RESULT_FIELDS = (
        'implementation', 'machine_decision', 'evidence_status',
        'failure_codes', 'input_sha256', 'historical_closed_as_of',
        'audit_closed_as_of',
    )

    def test_archived_case_and_result_reproduce(self):
        for sid in ZH_IDS:
            archived_case = ARCHIVE / sid / 'case.json'
            archived_result = ARCHIVE / sid / 'result.json'
            self.assertTrue(archived_case.is_file(), f'{sid}: archived case.json missing')
            self.assertTrue(archived_result.is_file(), f'{sid}: archived result.json missing')
            case = build(sid)
            self.assertEqual(case, json.loads(archived_case.read_text(encoding='utf-8')), sid)
            result = verify(case)
            self.assertEqual(result['input_sha256'], sha(case), sid)
            want = json.loads(archived_result.read_text(encoding='utf-8'))
            for field in self.RESULT_FIELDS:
                self.assertEqual(result[field], want[field], f'{sid}.{field}')


if __name__ == '__main__':
    unittest.main()
