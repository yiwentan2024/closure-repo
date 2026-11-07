"""Delete a liveops batch repository (or list them).

Repositories are deliberately NOT deleted automatically by the operator: they are
kept after each run so reviewers can audit the real operations on GitHub. Delete
one batch explicitly, or every lab-live-* repository with --all.

    python -m liveops.cleanup --root runs/live/<dir>         # show that batch
    python -m liveops.cleanup --root runs/live/<dir> --yes   # delete that batch
    python -m liveops.cleanup --repo owner/lab-live-batch-X --yes
    python -m liveops.cleanup --all                          # list every batch
    python -m liveops.cleanup --all --yes                    # delete every batch
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

from . import ghutil as G

PREFIX = 'lab-live-'


def list_scratch(owner: str):
    out = G.sh(['gh', 'repo', 'list', owner, '--limit', '200',
                '--json', 'name,url,updatedAt']).stdout
    return [r for r in json.loads(out) if r['name'].startswith(PREFIX)]


def _validated(spec: str, owner: str) -> str:
    parts = spec.strip().split('/')
    if len(parts) != 2 or not all(parts):
        raise SystemExit(f'refusing {spec!r}: expected owner/name')
    repo_owner, name = parts
    if repo_owner.lower() != owner.lower():
        raise SystemExit(f'refusing {spec}: not under the authenticated account {owner}')
    if not name.startswith(PREFIX):
        raise SystemExit(f'refusing {spec}: name does not start with {PREFIX}')
    return f'{repo_owner}/{name}'


def _from_root(root: Path, owner: str) -> str:
    meta = root / 'meta.json'
    if not meta.is_file():
        raise SystemExit(f'{root} has no meta.json; not a liveops evidence root')
    return _validated(json.loads(meta.read_text())['repo_full'], owner)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument('--repo', help='delete exactly this batch repository (owner/name)')
    scope.add_argument('--root', type=Path,
                       help='delete the batch repository recorded in <root>/meta.json')
    scope.add_argument('--all', action='store_true',
                       help=f'every {PREFIX}* repository under the authenticated account')
    ap.add_argument('--yes', action='store_true', help='actually delete (without it: list only)')
    args = ap.parse_args(argv)
    owner = G.gh_owner()

    if args.repo or args.root:
        full = (_validated(args.repo, owner) if args.repo
                else _from_root(args.root, owner))
        targets = [(full, f'https://github.com/{full}')]
    else:
        repos = sorted(list_scratch(owner), key=lambda x: x['updatedAt'])
        if not repos:
            print(f'no {PREFIX}* repositories under {owner}')
            return 0
        if not args.all:
            for r in repos:
                print(f"  {r['url']}  (updated {r['updatedAt']})")
            print(f'\n{len(repos)} batch repository(ies) under {owner}; select one with '
                  f'--root <evidence dir> or --repo owner/name, or pass --all.')
            if args.yes:
                raise SystemExit('--yes needs a scope: --root <evidence dir>, '
                                 '--repo owner/name, or --all')
            return 0
        targets = [(f"{owner}/{r['name']}", r['url']) for r in repos]

    if not args.yes:
        for full, url in targets:
            print(f'  would delete {url}')
        print(f'\n{len(targets)} repository(ies) selected; re-run with --yes to delete.')
        return 0

    failed = []
    for full, _url in targets:
        try:
            G.delete_repo(full)
        except Exception as exc:  # noqa: BLE001 — report each failure, keep going
            failed.append(full)
            print(f'FAILED   {full}: {exc}')
        else:
            print(f'deleted  {full}')
    print(f'\ndeleted {len(targets) - len(failed)} of {len(targets)} repository(ies).')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
