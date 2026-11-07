"""Unit tests for the inventory service (run by CI and by the release pipeline)."""
import unittest

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.service import VERSION, health_payload, health_status


class ServiceTests(unittest.TestCase):
    def test_version_exposed(self):
        self.assertRegex(VERSION, r'^\d+\.\d+\.\d+$')

    def test_health_ok_when_not_broken(self):
        self.assertEqual(health_status(False), 200)
        self.assertTrue(health_payload(False, 'dev')['ok'])

    def test_health_degrades_when_broken(self):
        self.assertEqual(health_status(True), 503)
        self.assertFalse(health_payload(True, 'qa')['ok'])


if __name__ == '__main__':
    unittest.main()
