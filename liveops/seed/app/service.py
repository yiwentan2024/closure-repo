"""Tiny inventory HTTP service used by the release pipeline."""
import argparse, json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = '0.1.0'


def health_payload(broken: bool, stage: str):
    return {'ok': not broken, 'stage': stage, 'version': VERSION, 'endpoint': '/health'}


def health_status(broken: bool) -> int:
    return 503 if broken else 200


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            time.sleep(float(os.getenv('SLOW_MS', '0')) / 1000.0)  # optional delay for slow-path checks
            broken = os.getenv('FAIL_HEALTH', '0') == '1'
            body = json.dumps(health_payload(broken, os.getenv('STAGE_NAME', 'local')), sort_keys=True).encode()
            self.send_response(health_status(broken))
        elif self.path == '/version':
            body = json.dumps({'version': VERSION}, sort_keys=True).encode()
            self.send_response(200)
        else:
            self.send_error(404)
            return
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f'{self.address_string()} {fmt % args}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    ThreadingHTTPServer((os.getenv('BIND_HOST', '127.0.0.1'), args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
