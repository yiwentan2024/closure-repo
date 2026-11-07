"""Golden replay of recorded live-operations evidence (no network).

Fixtures under liveops/fixtures/<SID>/facts.json are exported verbatim from a
completed live run (GitHub operations on a temporary repository, deleted
afterwards). This suite replays each fixture through the same collector
mapping and the unmodified closure verifier and requires agreement with the
recorded expected outcomes — the permanent offline regression that the live
evidence, the collection mapping, and the verifier all still agree.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from liveops.collector import assemble
from liveops.replay_check import check

GOLDEN_IDS = ['N01', 'N02', 'N03', 'B01', 'B02', 'A101', 'A102', 'A103', 'A104', 'A105',
              'A201', 'A202', 'A203', 'A204', 'U01', 'U02']
FIXTURES = Path(__file__).resolve().parents[2] / 'liveops' / 'fixtures'


class LiveGoldenReplay(unittest.TestCase):

    def test_live_operation_facts_match_recorded_outcomes(self):
        for sid in GOLDEN_IDS:
            with self.subTest(scenario_id=sid):
                facts_file = FIXTURES / sid / 'facts.json'
                self.assertTrue(facts_file.is_file(),
                                f'missing fixture {facts_file}; export from a live run first')
                row = check(sid, assemble(json.loads(facts_file.read_text())))
                self.assertEqual(row['observed_decision'], row['oracle_decision'])
                self.assertTrue(set(row['oracle_codes']).issubset(set(row['observed_codes'])))
                self.assertTrue(row['match'])

    def test_facts_are_live_operation_records(self):
        """Structural sanity: fixtures must carry provenance fields."""
        for sid in GOLDEN_IDS:
            with self.subTest(scenario_id=sid):
                facts = json.loads((FIXTURES / sid / 'facts.json').read_text())
                self.assertTrue(facts['inventory'], 'no stage-health events')
                self.assertTrue(facts['tag_time'], 'no tag time')
                self.assertTrue(facts['issues'], 'no GitHub issues')
                self.assertEqual(facts['snapshot_frozen']['release_id'],
                                 facts['cutoff_tag'], 'frozen snapshot must name its release tag')


if __name__ == '__main__':
    unittest.main()
