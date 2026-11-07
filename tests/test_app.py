import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from app.__main__ import Handler


class SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_app_has_handler(self):
        self.assertTrue(hasattr(Handler, 'do_GET'))

    def test_health_and_version_routes(self):
        with urllib.request.urlopen(f'{self.base}/health') as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(json.loads(resp.read())['endpoint'], '/health')
        with urllib.request.urlopen(f'{self.base}/version') as resp:
            self.assertEqual(json.loads(resp.read())['endpoint'], '/version')

    def test_unknown_route_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f'{self.base}/missing')
        self.assertEqual(ctx.exception.code, 404)
