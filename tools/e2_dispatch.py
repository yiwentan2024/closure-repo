"""Print planned GitHub runs by default; dispatch only after the protocol freeze.

No inference about successes. Requires an installed and authenticated GitHub CLI
for --execute.
"""
import argparse,subprocess
from scenarios.build_case import MANIFEST

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--scenario',default='ALL',help='ALL or single controlled scenario ID')
    ap.add_argument('--variant',default='ALL',choices=['ALL','B0','B1','B2'])
    ap.add_argument('--replicate',default='ALL',help="ALL (1-3) or a single replicate index")
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--acknowledge-protocol-frozen',action='store_true')
    args=ap.parse_args()
    if args.replicate=='ALL':
        replicates=[1,2,3]
    else:
        try:
            replicates=[int(args.replicate)]
        except ValueError:
            ap.error('replicate must be ALL or an integer 1-3')
        if any(r<1 or r>3 for r in replicates):ap.error('replicate must be in 1-3')
    scenarios=[x for x in MANIFEST['scenarios'] if x['source']=='CONTROLLED' and (args.scenario=='ALL' or x['id']==args.scenario)]
    if not scenarios:ap.error('no eligible controlled scenario; real-data IDs require source records')
    variants=['B0','B1','B2'] if args.variant=='ALL' else [args.variant]
    commands=[['gh','workflow','run','e2-19cases.yml','-f',f'scenario_id={s["id"]}',
               '-f',f'variant={v}','-f',f'replicate_index={r}'] for s in scenarios for v in variants for r in replicates]
    if args.execute and not args.acknowledge_protocol_frozen:
        ap.error('--execute requires --acknowledge-protocol-frozen (the recorded expected outcomes and baseline protocols must be committed first)')
    for i,cmd in enumerate(commands,1):
        print(f'[{i:02d}/{len(commands)}]',*cmd)
        if args.execute:subprocess.run(cmd,check=True)
    if not args.execute:print('\nDRY RUN ONLY. Nothing dispatched. Use --execute --acknowledge-protocol-frozen after approvals.')
    return 0
if __name__=='__main__':raise SystemExit(main())
