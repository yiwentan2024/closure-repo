"""Offline regressions for the liveops integrity fixes (no network, local git).

Covers: frozen (tagged) policy/rule reads, policy-derived exclusion
authorization, policy-derived source scope, measured successor-release
verification, per-cycle baseline anchors, cleanup scoping, existence probing,
and the credential-file guard on commits.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from liveops import ghutil
from liveops import roles as R
from liveops.cleanup import _from_root, _validated
from liveops.collector import _git_show_bytes, _rule_declared, assemble
from liveops.operator import matrix_ok, merge_rows
from verifier.closure_audit import sha

NOW = '2026-01-01T00:00:00Z'
SEED = Path(__file__).resolve().parents[1] / 'seed'


def _git(cwd, *args, check=True):
    p = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
    return p


def _init_repo(root: Path, files: dict):
    origin = root / 'origin.git'
    subprocess.run(['git', 'init', '--bare', str(origin)],
                   check=True, capture_output=True)
    repo = root / 'repo'
    subprocess.run(['git', 'init', '-b', 'main', str(repo)],
                   check=True, capture_output=True)
    _git(repo, 'config', 'user.name', 'tester')
    _git(repo, 'config', 'user.email', 'tester@example.com')
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-m', 'init')
    _git(repo, 'remote', 'add', 'origin', str(origin))
    _git(repo, 'push', '-u', 'origin', 'main')
    return repo, origin


def _fake_gh(bin_dir: Path, response: str):
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / 'gh'
    script.write_text('#!/bin/sh\ncat <<\'EOF\'\n' + response + '\nEOF\n')
    script.chmod(0o755)
    return mock.patch.dict(os.environ,
                           {'PATH': f'{bin_dir}:{os.environ["PATH"]}'})


POLICY = ('release_policy_version: P-1\n'
          'service_id: inventory-service\n'
          'waivable_gates: [scan]\n'
          'waiver_approvers: [qa-lead]\n'
          'exclusion_authorities: [qa-lead]\n'
          'approved_sources: [stage-deploy-http]\n')


def _facts(policy_text=POLICY, disposition=None, source='stage-deploy-http',
           finding_obs=True, rule_sha='sha256:rule'):
    payload = {'stage': 'dev', 'http_status': 200}
    raw = {'signal_id': 'SIG-1', 'source': source,
           'event_time': NOW, 'ingest_time': NOW,
           'payload': payload, 'payload_sha256': sha(payload)}
    disp = {'disposition_id': 'DISP-001', 'signal_id': 'SIG-1',
            'decision': 'RESOLVED', 'decision_time': NOW, 'actor': 'qa-lead',
            'effective': True, 'reason': 'handled', 'authorized': True}
    disp.update(disposition or {})
    issue = {'number': 1, 'state': 'closed', 'created_at': NOW,
             'body': 'kind: signal\nsignal_id: SIG-1\ncycle: T',
             'labels': [{'name': 'signal'}]}
    frozen = {'release_id': 'v1', 'artifact_sha256': 'sha256:art'}
    return {
        'audit_time': NOW, 'scenario_id': 'T',
        'inventory': [raw], 'dispositions': [disp],
        'gates': [{'gate_id': 'unit', 'required': True, 'status': 'PASS',
                   'evaluated_at': NOW}],
        'target_verification': [],
        'tag_time': NOW, 'snapshot_frozen': frozen, 'snapshot_current': frozen,
        'policy_text': policy_text, 'collection_rule_sha': rule_sha,
        'finding_observation_complete': finding_obs,
        'inventory_source_sha256': 'sha256:inv', 'inventory_source_ref': 'abc123',
        'cutoff_tag': 'v1', 'target_tag': 'v2', 'asset_sha': 'sha256:art',
        'issues': [issue], 'pulls': [], 'reviews_by_pr': {},
        'pr_comments_by_pr': {},
        'close_actors': {'1': (disp['actor'], NOW)},
    }


class CollectorIntegrityTest(unittest.TestCase):

    def test_exclusion_authorization_is_derived_from_policy(self):
        case = assemble(_facts(disposition={'decision': 'AUTHORIZED_EXCLUDED',
                                            'actor': 'dev1', 'authorized': True}))
        self.assertIs(case['dispositions'][0]['authorized'], False,
                      'a record claiming authorization must not outrank the policy')

        case = assemble(_facts(disposition={'decision': 'AUTHORIZED_EXCLUDED',
                                            'actor': 'qa-lead', 'authorized': False}))
        self.assertIs(case['dispositions'][0]['authorized'], True,
                      'a policy authority must be recognized regardless of the record')

    def test_scope_sources_and_attestation_come_from_records(self):
        policy = POLICY.replace('approved_sources: [stage-deploy-http]',
                                'approved_sources: [other-source]')
        case = assemble(_facts(policy_text=policy))
        self.assertEqual(case['scope']['approved_sources'], ['other-source'])
        att = case['scope']['collector_attestation']
        self.assertEqual(att['query_rule_hash'], 'sha256:rule')
        self.assertEqual(att['inventory_source_sha256'], 'sha256:inv')
        self.assertEqual(att['inventory_source_ref'], 'abc123')

        fallback = assemble(_facts(policy_text=POLICY.replace(
            'approved_sources: [stage-deploy-http]\n', '')))
        self.assertEqual(fallback['scope']['approved_sources'], ['stage-deploy-http'])

        unavailable = assemble(_facts(finding_obs=False, rule_sha=None))
        self.assertIs(unavailable['scope']['finding_observation_complete'], False)
        self.assertIs(
            unavailable['scope']['collector_attestation']['complete'], False)

    def test_policy_and_rule_are_read_from_the_cutoff_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = _init_repo(Path(tmp), {
                'policy/release_policy.yaml': 'service_id: inventory-service\n',
                'COLLECTION_RULE.md': 'finding_observation: complete\n'})
            _git(repo, 'tag', '-a', 'release/T', '-m', 'freeze')
            (repo / 'policy/release_policy.yaml').write_text('service_id: rewritten\n')
            (repo / 'COLLECTION_RULE.md').write_text('# rewritten rule\n')
            frozen_policy = _git_show_bytes(
                repo, 'release/T', 'policy/release_policy.yaml')
            self.assertEqual(frozen_policy,
                             b'service_id: inventory-service\n')
            self.assertNotEqual(frozen_policy,
                                (repo / 'policy/release_policy.yaml').read_bytes())
            self.assertIsNotNone(
                _git_show_bytes(repo, 'release/T', 'COLLECTION_RULE.md'))
            self.assertIsNone(_git_show_bytes(repo, 'release/T', 'missing.md'))

    def test_rule_declaration(self):
        self.assertTrue(_rule_declared('finding_observation: complete\n', 'finding_observation'))
        self.assertFalse(_rule_declared('finding_observation: partial\n', 'finding_observation'))
        self.assertFalse(_rule_declared('', 'finding_observation'))


class VerifyTargetTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.artifact = b'PYZ-BYTES'
        self.artifact_sha = 'sha256:' + hashlib.sha256(self.artifact).hexdigest()
        self.repo, _ = _init_repo(self.root, {
            'policy/release_policy.yaml': POLICY,
            'app/service.py': "VERSION = '0.1.0'\n",
            'snapshots/T-target.json': json.dumps(
                {'release_id': 'release/T-target',
                 'artifact_sha256': self.artifact_sha})})
        self.source_sha = _git(self.repo, 'rev-parse', 'HEAD').stdout.strip()
        _git(self.repo, 'tag', '-a', 'release/T-target', '-m', 'target release')
        (self.repo / 'dist').mkdir()
        (self.repo / 'dist/app.pyz').write_bytes(self.artifact)
        (self.repo / 'dist/manifest.json').write_text(json.dumps({
            'artifact': 'dist/app.pyz', 'sha256': self.artifact_sha,
            'source_commit': self.source_sha, 'built_at': NOW}))
        self.ctx = R.Ctx(self.repo, 'tester/lab-live-batch-test', 'tester', 'T')

    def tearDown(self):
        self.tmp.cleanup()

    def _rows(self):
        return R.read_jsonl(self.ctx.ev / 'target/verification.jsonl')

    def test_measured_target_verification(self):
        with _fake_gh(self.root / 'bin', json.dumps(
                {'tagName': 'release/T-target',
                 'assets': [{'name': 'app.pyz'}]})):
            R.verify_target(self.ctx, test_passed=True, unit_exit_code=0,
                            unit_output='Ran 7 tests in 0.01s\n\nOK')
            row = self._rows()[-1]
            self.assertTrue(row['manifest_complete'])
            self.assertTrue(row['coverage_complete'])
            self.assertTrue(row['selected_path_valid'])
            self.assertTrue(row['closed'])
            self.assertEqual(row['test_count'], 7)
            archived = self.ctx.ev / 'target/build_manifest.json'
            self.assertTrue(archived.is_file(), 'measured manifest must be archived')
            self.assertEqual(row['manifest_sha256'],
                             'sha256:' + hashlib.sha256(archived.read_bytes()).hexdigest())

            R.verify_target(self.ctx, test_passed=False, unit_exit_code=1,
                            unit_output='Ran 7 tests in 0.01s\n\nFAILED (failures=1)')
            row = self._rows()[-1]
            self.assertTrue(row['manifest_complete'])
            self.assertFalse(row['closed'], 'a failed unit run must not close the target')

        with _fake_gh(self.root / 'bin', json.dumps(
                {'tagName': 'release/T-target', 'assets': []})):
            R.verify_target(self.ctx, test_passed=True, unit_exit_code=0,
                            unit_output='Ran 7 tests in 0.01s\n\nOK')
            row = self._rows()[-1]
            self.assertTrue(row['manifest_complete'])
            self.assertFalse(row['selected_path_valid'])
            self.assertFalse(row['closed'], 'a missing release asset must not close the target')

    def test_manifest_digest_mismatch_is_measured(self):
        manifest_path = self.repo / 'dist/manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['sha256'] = 'sha256:not-the-artifact'
        manifest_path.write_text(json.dumps(manifest))
        with _fake_gh(self.root / 'bin', json.dumps(
                {'tagName': 'release/T-target',
                 'assets': [{'name': 'app.pyz'}]})):
            R.verify_target(self.ctx, test_passed=True, unit_exit_code=0,
                            unit_output='Ran 2 tests in 0.01s\n\nOK')
        row = self._rows()[-1]
        self.assertFalse(row['manifest_complete'])
        self.assertFalse(row['closed'])


class PrepareCycleTest(unittest.TestCase):

    def test_every_cycle_gets_a_window_anchor_and_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = _init_repo(root, {'README.md': 'inventory service\n'})
            shutil.copytree(SEED, repo, dirs_exist_ok=True)
            _git(repo, 'add', '-A')
            _git(repo, 'commit', '-m', 'seed')
            _git(repo, 'push')
            (repo / 'COLLECTION_RULE.md').unlink()
            _git(repo, 'add', '-A')
            _git(repo, 'commit', '-m', 'drop stale ops doc')
            _git(repo, 'push')

            fake = _fake_gh(root / 'bin', '[]')
            with fake:
                ctx = R.Ctx(repo, 'tester/lab-live-batch-test', 'tester', 'T')
                R.prepare_cycle(ctx)
                self.assertTrue((repo / 'COLLECTION_RULE.md').is_file(),
                                'baseline rule must be restored')
                first = _git(repo, 'log', '-1', '--format=%s').stdout
                self.assertIn('prepare release cycle T', first)
                self.assertEqual(_git(repo, 'status', '--porcelain').stdout.strip(), '',
                                 'prepare_cycle must leave a clean worktree')

                before = int(_git(repo, 'rev-list', '--count', 'HEAD').stdout)
                R.prepare_cycle(ctx)
                after = int(_git(repo, 'rev-list', '--count', 'HEAD').stdout)
                self.assertEqual(after, before + 1,
                                 'a clean cycle still needs an anchor commit')
                self.assertIn('baseline anchor',
                              _git(repo, 'log', '-1', '--format=%s').stdout)


class GhutilAndCleanupTest(unittest.TestCase):

    def _cp(self, rc, out='', err=''):
        return subprocess.CompletedProcess(args=['gh'], returncode=rc,
                                           stdout=out, stderr=err)

    def test_repo_exists_rejects_unknown_failures(self):
        with mock.patch.object(ghutil, 'sh', return_value=self._cp(0, '{"name":"x"}')):
            self.assertTrue(ghutil.repo_exists('me/x'))
        with mock.patch.object(ghutil, 'sh', return_value=self._cp(
                1, '', 'GraphQL: Could not resolve to a Repository with the name me/x. (repository)')):
            self.assertFalse(ghutil.repo_exists('me/x'))
        with mock.patch.object(ghutil, 'sh', return_value=self._cp(
                1, '', 'error connecting to api.github.com')):
            with self.assertRaises(ghutil.CmdError):
                ghutil.repo_exists('me/x')

    def test_commit_all_refuses_credential_like_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'r'
            subprocess.run(['git', 'init', '-b', 'main', str(repo)],
                           check=True, capture_output=True)
            _git(repo, 'config', 'user.name', 'tester')
            _git(repo, 'config', 'user.email', 'tester@example.com')
            (repo / 'a.txt').write_text('a\n')
            ghutil.commit_all(repo, 'init')
            (repo / '.env').write_text('SECRET=1\n')
            with self.assertRaises(RuntimeError):
                ghutil.commit_all(repo, 'must not land')
            self.assertNotIn('.env', _git(repo, 'show', '--name-only', 'HEAD').stdout)
            self.assertEqual(_git(repo, 'status', '--porcelain').stdout.strip(), '?? .env')

    def test_cleanup_scope_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'meta.json').write_text(json.dumps(
                {'repo_full': 'me/lab-live-batch-x'}))
            self.assertEqual(_from_root(root, 'ME'), 'me/lab-live-batch-x')
            with self.assertRaises(SystemExit):
                _validated('you/lab-live-batch-x', 'me')
            with self.assertRaises(SystemExit):
                _validated('me/ordinary-repo', 'me')
            with self.assertRaises(SystemExit):
                _from_root(root / 'nope', 'me')

    def test_resume_keeps_failed_rows_and_replaces_retried_ones(self):
        prior = [{'scenario_id': 'N01', 'match': True},
                 {'scenario_id': 'A101', 'match': False}]
        rows = merge_rows(prior, [{'scenario_id': 'A101', 'match': True}])
        self.assertEqual([r['scenario_id'] for r in rows], ['N01', 'A101'])
        self.assertTrue(rows[1]['match'])
        rows = merge_rows(rows, [{'scenario_id': 'B02', 'match': True}])
        self.assertEqual([r['scenario_id'] for r in rows], ['N01', 'A101', 'B02'])

    def test_matrix_requires_agreeing_inrepo_verdict(self):
        self.assertTrue(matrix_ok([{'match': True, 'inrepo_match': True}], True))
        self.assertFalse(matrix_ok([{'match': True, 'inrepo_match': False}], True))
        self.assertTrue(matrix_ok([{'match': True, 'inrepo_match': False}], False))
        self.assertFalse(matrix_ok([{'match': False, 'inrepo_match': True}], False))


if __name__ == '__main__':
    unittest.main()
