#!/usr/bin/env python3
"""Domain-neutral HTTP bridge for an existing local FCC service (stdlib only)."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def request(base: str, method: str, route: str, payload=None):
    body = None if payload is None else json.dumps(payload, allow_nan=False).encode('utf-8')
    req = Request(base.rstrip('/') + route, data=body, method=method,
                  headers={'Content-Type':'application/json'})
    try:
        with urlopen(req, timeout=15) as response:
            return json.load(response)
    except HTTPError as e:
        raise SystemExit(f'FCC HTTP {e.code}: {e.read().decode("utf-8", "replace")}') from e
    except URLError as e:
        raise SystemExit(f'FCC unavailable: {e.reason}') from e


def main(argv=None):
    parser=argparse.ArgumentParser(description='FCC universal skill HTTP helper')
    parser.add_argument('--url',default=os.getenv('FCC_URL','http://127.0.0.1:8099'))
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('health');sub.add_parser('capabilities')
    create=sub.add_parser('create');create.add_argument('--config',required=True)
    for cmd in ('state','cycle','history','verification'):
        c=sub.add_parser(cmd);c.add_argument('--loop',required=True)
    obs=sub.add_parser('observe');obs.add_argument('--loop',required=True)
    obs.add_argument('--metric',required=True);obs.add_argument('--value',required=True,type=float)
    obs.add_argument('--source',default='measured',choices=['measured','ai_inference','human_input'])
    obs.add_argument('--confidence',type=float)
    obs.add_argument('--evidence',action='append',default=[])
    args=parser.parse_args(argv)
    if args.command=='create':
        data=json.loads(Path(args.config).read_text(encoding='utf-8'))
        result=request(args.url,'POST','/api/loops',{'config':data})
    elif args.command=='observe':
        payload={'metric_id':args.metric,'value':args.value,'source_type':args.source}
        if args.source=='ai_inference':
            payload.update(confidence=args.confidence,evidence=args.evidence,is_ground_truth=False)
        result=request(args.url,'POST','/api/loops/'+quote(args.loop,safe='')+'/observations',payload)
    else:
        route={'health':'/api/health','capabilities':'/api/capabilities',
               'state':'/api/loops/{id}','cycle':'/api/loops/{id}/cycle',
               'history':'/api/loops/{id}/history','verification':'/api/loops/{id}/verification'}[args.command]
        if '{id}' in route:route=route.replace('{id}',quote(args.loop,safe=''))
        result=request(args.url,'POST' if args.command=='cycle' else 'GET',route)
    print(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
