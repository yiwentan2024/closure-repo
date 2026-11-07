"""Thin gh/git/docker wrappers (no dependencies; standard library only).

Command shapes follow the patterns used by this project's CI automation (retry
on transient network errors, delete-capable token check); the module is
self-contained and imports nothing from outside the repository.
"""
from __future__ import annotations
import datetime as dt
import json
import re
import subprocess
import time
from pathlib import Path

from loguru import logger

RETRY_ATTEMPTS = 6
RETRY_SLEEP_S = 10


class CmdError(RuntimeError):
    def __init__(self, cmd, returncode, stdout, stderr):
        super().__init__(f"command failed ({returncode}): {' '.join(map(str, cmd))}\n{stderr or stdout}")
        self.cmd, self.returncode = list(map(str, cmd)), returncode


def sh(cmd, cwd=None, check=True, env=None):
    """Run a command; transient network failures are retried automatically
    (SSL/connection/dns blips), real errors fail immediately."""
    cmd = [str(c) for c in cmd]
    attempt = 0
    while True:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
        if p.returncode == 0 or not _transient(p) or attempt >= RETRY_ATTEMPTS:
            if check and p.returncode != 0:
                raise CmdError(cmd, p.returncode, p.stdout, p.stderr)
            return p
        attempt += 1
        time.sleep(RETRY_SLEEP_S)


def sh_retry(cmd, cwd=None, check=True):
    """Explicit alias kept for call sites that document their retry intent."""
    return sh(cmd, cwd=cwd, check=check)


def _transient(p):
    low = ((p.stderr or '') + (p.stdout or '')).lower()
    return any(k in low for k in ('could not resolve', 'could not connect', 'failed to connect',
                                  'connecting to api.', 'check your internet',
                                  'connection reset', 'connection refused', 'timed out',
                                  'ssl', 'tls', 'handshake', 'unexpected eof', 'proxy',
                                  'bad gateway', 'service unavailable'))


def gh_api(endpoint, cwd=None):
    """GET a GitHub REST endpoint via gh; returns parsed JSON."""
    p = sh_retry(['gh', 'api', endpoint], cwd=cwd)
    return json.loads(p.stdout or 'null')


def gh_owner():
    return sh_retry(['gh', 'api', 'user', '-q', '.login']).stdout.strip()


def gh_user():
    u = gh_api('user')
    return u['login'], u['id']


def utc_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


_NOT_FOUND_MARKERS = ('could not resolve to a repository', 'not found', 'http 404')


def repo_exists(full):
    """True only when GitHub positively reports the repository; network/permission
    failures raise instead of being mistaken for 'deleted'."""
    p = sh(['gh', 'repo', 'view', full, '--json', 'name'], check=False)
    if p.returncode == 0:
        return True
    low = ((p.stderr or '') + (p.stdout or '')).lower()
    if any(k in low for k in _NOT_FOUND_MARKERS):
        return False
    raise CmdError(['gh', 'repo', 'view', full], p.returncode, p.stdout, p.stderr)


def clone_repo(full, workspace: Path):
    """Clone an existing repository into workspace; returns the local path."""
    local = workspace / full.split('/')[-1]
    sh_retry(['git', 'clone', f'https://github.com/{full}.git', local.as_posix()])
    return local


def ensure_git_cred():
    """Route git HTTPS auth for github.com through the gh CLI credential helper
    (idempotent); avoids depending on any machine-local SSH key setup."""
    sh(['gh', 'auth', 'setup-git'], check=False)


def check_scopes(required=('repo', 'workflow')):
    """Fail fast with a clear fix when the gh token cannot do what the run needs
    (pushing workflow files requires the `workflow` scope; cleanup needs
    `delete_repo`). Returns the granted scope list."""
    p = sh(['gh', 'auth', 'status'], check=False)
    granted = set()
    for m in re.finditer(r"'([a-z_:]+)'", p.stdout + p.stderr):
        granted.add(m.group(1))
    missing = [s for s in required if s not in granted]
    if missing:
        raise SystemExit(
            f"[preflight] gh token is missing scope(s): {', '.join(missing)}\n"
            f"Fix (grants the scope to the ACTIVE gh account, interactive browser):\n"
            f"  gh auth refresh -h github.com -s {' '.join(missing)}\n"
            f"Then re-run this command.")
    if 'delete_repo' not in granted:
        logger.bind(actor='ghutil').info('NOTE: no delete_repo scope — the cleanup tool will '
                                         'not be able to delete repositories')
    return sorted(granted)


def create_repo(full, workspace: Path):
    """Create a private repo and clone it into workspace; returns local path.

    GitHub rate-limits rapid repository creation ('too many repositories, too
    quickly'); back off and retry — it typically clears within minutes.
    """
    local = workspace / full.split('/')[-1]
    delays = [5, 30, 60, 120, 240, 300]
    for delay in delays:
        p = sh(['gh', 'repo', 'create', full, '--private'], check=False)
        if p.returncode == 0:
            break
        if 'too many repositories' not in (p.stderr or '').lower():
            raise CmdError(['gh', 'repo', 'create', full], p.returncode, p.stdout, p.stderr)
        logger.bind(actor='ghutil').info(f'repository-creation rate limited; waiting {delay}s ...')
        time.sleep(delay)
    else:
        raise RuntimeError(f'repository creation still rate limited after {sum(delays)}s: {full}')
    sh_retry(['git', 'clone', f'https://github.com/{full}.git', local.as_posix()])
    return local


def delete_repo(full):
    sh(['gh', 'repo', 'delete', full, '--yes'])


def setup_git_identity(repo_dir: Path):
    """Pin per-repo author identity to the gh-authenticated account (authorship
    must not silently fall back to a machine-global git identity)."""
    login, uid = gh_user()
    sh(['git', 'config', 'user.name', login], cwd=repo_dir)
    sh(['git', 'config', 'user.email', f'{uid}+{login}@users.noreply.github.com'], cwd=repo_dir)
    return login


def git_out(repo_dir: Path, *args):
    return sh(['git', *args], cwd=repo_dir).stdout.strip()


_CREDENTIAL_LIKE = re.compile(
    r'(^|/)(\.env(\.[^/]*)?|id_rsa|id_ed25519|id_ecdsa|credentials(\.[^/]*)?|\.netrc'
    r'|[^/]*\.(pem|key|p12|pfx))$', re.IGNORECASE)


def commit_all(repo_dir: Path, message: str):
    """Stage and commit the scratch repository's full working tree. Refuses to
    stage credential-like files so an unexpected local secret can never travel
    into the (private but remote) batch repository."""
    sh(['git', 'add', '-A'], cwd=repo_dir)
    staged = sh(['git', 'diff', '--cached', '--name-only'], cwd=repo_dir).stdout.splitlines()
    risky = [p for p in staged if _CREDENTIAL_LIKE.search(p)]
    if risky:
        sh(['git', 'reset', '-q'], cwd=repo_dir)
        raise RuntimeError(f'refusing to commit credential-like file(s): {risky}')
    sh(['git', 'commit', '-m', message], cwd=repo_dir)
    return sh(['git', 'rev-parse', '--short', 'HEAD'], cwd=repo_dir).stdout.strip()


def push(repo_dir: Path, ref='HEAD'):
    sh_retry(['git', 'push', 'origin', ref], cwd=repo_dir)


def pull(repo_dir: Path):
    sh_retry(['git', 'pull', '--ff-only'], cwd=repo_dir)
