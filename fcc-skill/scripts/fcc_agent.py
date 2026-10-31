#!/usr/bin/env python3
"""FCC commissioning CLI for tool-using agents. stdlib only; local host exclusively."""
from __future__ import annotations
import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse


def run(argv=None):
    p=argparse.ArgumentParser(description="FCC 1.5 agent commissioning CLI (local only)")
    p.add_argument('--server',default='http://127.0.0.1:8099')
    sub=p.add_subparsers(dest='action',required=True)
    for a in ('health','questions','plans','create','answer','view','compile','activate','pause','resume','status',
              'observe','checkpoint','inbox','propose','interventions','applied','verify'):
        sp=sub.add_parser(a)
        sp.add_argument('--plan-id')
        sp.add_argument('--intervention-id',type=int)
        sp.add_argument('--json',help='JSON dict payload; alternative to --file')
        sp.add_argument('--file',help='JSON file payload')
    args=p.parse_args(argv)
    parsed=urlparse(args.server)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1'):
        p.error('FCC demo HTTP API has no authentication. For safety use local loopback only.')
    if args.json and args.file:p.error('Use only one of --json or --file')
    data=json.loads(args.json) if args.json else json.load(open(args.file,encoding='utf-8')) if args.file else {}
    if not isinstance(data,dict):p.error('JSON payload must be a dictionary')
    root='/api/onboarding/plans'
    if args.action=='health':route='/api/health';method='GET'
    elif args.action=='questions':route='/api/onboarding/questions';method='GET'
    elif args.action=='plans':route=root;method='GET'
    elif args.action=='create':route=root;method='POST'
    else:
        if not args.plan_id:p.error('--plan-id required for this operation')
        route=f'{root}/{args.plan_id}';method='GET'
        specs={
            'answer':('PATCH',''), 'view':('GET',''), 'compile':('GET','/compile'),
            'activate':('POST','/activate'),'pause':('POST','/pause'),'resume':('POST','/resume'),
            'status':('GET','/status'),'observe':('POST','/observations'),
            'checkpoint':('POST','/checkpoint'),'inbox':('GET','/outbox'),
            'propose':('POST','/interventions'),'interventions':('GET','/interventions'),
            'applied':('POST',f'/interventions/{args.intervention_id}/applied'),
            'verify':('POST',f'/interventions/{args.intervention_id}/verify')}
        method,suffix=specs[args.action]
        if args.action in ('applied','verify') and not args.intervention_id:p.error('--intervention-id required')
        route+=suffix
    url=args.server.rstrip('/')+route
    req=Request(url,method=method,data=json.dumps(data).encode('utf-8') if method in ('POST','PATCH') else None,
                headers={'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=20) as r:
            print(json.dumps(json.load(r),ensure_ascii=False,indent=2))
    except HTTPError as e:
        print(f'FCC API error {e.code}: {e.read().decode("utf-8",errors="replace")}',file=sys.stderr)
        return 1
    except (URLError,TimeoutError) as e:
        print(f'FCC unavailable: {e}. Install/start FCC Core 1.5, keep the process running, check port 8099.',file=sys.stderr)
        return 2
    return 0
if __name__=='__main__':sys.exit(run())
