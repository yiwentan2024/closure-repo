"""Deploy the three stages via docker compose and probe HTTP /health.

Writes observed events to evidence/raw/inventory.jsonl (each with its canonical
payload digest) and the staging gate verdict to evidence/gates.jsonl. Events
come from HTTP requests against the running stage containers.
"""
import argparse, datetime as dt, hashlib, json, time, urllib.error, urllib.request
from pathlib import Path

STAGES = [('dev', 18091), ('qa', 18092), ('staging', 18093)]


def canonical_sha(obj):
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return 'sha256:' + hashlib.sha256(blob).hexdigest()


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


HEALTH_SLO_MS = 500  # gate passes on HTTP 200; SLO breaches remain signals to triage

# Stage probes target localhost: never route them through an environment proxy
# (http_proxy/https_proxy/all_proxy), whose error pages are not our JSON.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _parse_body(raw):
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {'raw': raw.decode('utf-8', 'replace')[:200]}


def probe(url):
    for _ in range(60):
        try:
            t0 = time.monotonic()
            with _NO_PROXY_OPENER.open(url, timeout=10) as res:
                body = _parse_body(res.read())
                return res.status, body, int((time.monotonic() - t0) * 1000)
        except urllib.error.HTTPError as exc:
            return exc.code, _parse_body(exc.read()), 0
        except (ConnectionError, TimeoutError, urllib.error.URLError):
            time.sleep(0.5)
    return None, {}, 0


def append_jsonl(path, row):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, sort_keys=True) + '\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, default=Path('evidence'))
    ap.add_argument('--skip-deploy', action='store_true', help='probe stages already running')
    ap.add_argument('--no-gates', action='store_true',
                    help='routine sweep: record events only, not release gate verdicts')
    args = ap.parse_args()
    import subprocess, os, sys
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ}
    if not args.skip_deploy:
        def run_retry(cmd, delays=(10, 20, 30, 60, 120), note=''):
            p = None
            for attempt, delay in enumerate((0,) + tuple(delays), 1):
                if delay:
                    time.sleep(delay)
                p = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True)
                if p.returncode == 0:
                    return p
                print(f'[deploy{note}] attempt {attempt} failed: '
                      f'{(p.stderr or p.stdout)[-500:]}', file=sys.stderr)
            return p
        # warm the base image first so flaky registry lookups do not kill the build
        run_retry(['docker', 'pull', 'python:3.12-alpine'], note=':pull')
        up = run_retry(['docker', 'compose', '-f', 'infra/compose.yml', 'up', '-d', '--build'],
                       delays=(5, 15, 30, 60))
        if up.returncode != 0:
            print(up.stderr[-4000:], file=sys.stderr)
            sys.exit(f'docker compose up failed rc={up.returncode}')
    verdicts = []
    try:
        for name, port in STAGES:
            url = f'http://127.0.0.1:{port}/health'
            status, body, latency_ms = probe(url)
            now = utc()
            payload = {'stage': name, 'http_status': status, 'response': body,
                       'latency_ms': latency_ms, 'slo_exceeded': latency_ms > HEALTH_SLO_MS,
                       'probe_url': url, 'probe_kind': 'live_HTTP_request'}
            event = {'signal_id': f'health:{name}:{now}', 'event_type': 'stage-health',
                     'source': 'stage-deploy-http', 'event_time': now, 'ingest_time': now,
                     'payload': payload, 'payload_sha256': canonical_sha(payload)}
            append_jsonl(args.out / 'raw/inventory.jsonl', event)
            verdict = 'PASS' if status == 200 else 'FAIL'
            verdicts.append(verdict)
            if not args.no_gates:
                append_jsonl(args.out / 'gates.jsonl',
                             {'gate_id': f'stage-{name}', 'required': True, 'status': verdict,
                              'evaluated_at': now, 'http_status': status, 'latency_ms': payload['latency_ms']})
    finally:
        if not args.skip_deploy:
            subprocess.run(['docker', 'compose', '-f', 'infra/compose.yml', 'down', '--remove-orphans'],
                           cwd=root, env=env, capture_output=True, text=True)
    ok = all(v == 'PASS' for v in verdicts)
    print('stage health:', 'PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
