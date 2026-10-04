"""Bounded AWS lab experiment with rollback. Requires explicit --execute."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import urllib.request
import uuid


def tagged(ec2, group, project):
    value=ec2.describe_security_groups(GroupIds=[group])['SecurityGroups'][0]
    if not any(t['Key']=='Project' and t['Value']==project for t in value.get('Tags',[])):
        raise ValueError('refusing experiment on a security group without matching Project tag')
    return value


def main(args):
    import boto3
    if args.duration < 300:
        raise ValueError('use at least 300 seconds to observe RTO < 5 minutes')
    ec2=boto3.client('ec2',region_name=args.region)
    lamb=boto3.client('lambda',region_name=args.region)
    out=Path(args.output)
    out.mkdir(parents=True,exist_ok=True)
    if (out/'rollback.json').exists():
        raise ValueError('output already contains an experiment; preserve evidence')
    if args.mode=='db-isolation':
        original=tagged(ec2,args.security_group,args.project)
        ingress=[p for p in original['IpPermissions'] if p.get('FromPort')==5432 and p.get('ToPort')==5432]
        if not ingress:
            raise ValueError('no DB ingress rules found')
        rollback={'mode':args.mode,'region':args.region,'security_group':args.security_group,'permissions':ingress}
    else:
        config=lamb.get_function(FunctionName=args.function)
        if config.get('Tags',{}).get('Project') != args.project:
            raise ValueError('function must have matching Project tag')
        concurrency=lamb.get_function_concurrency(FunctionName=args.function).get('ReservedConcurrentExecutions')
        rollback={'mode':args.mode,'region':args.region,'function':args.function,'concurrency':concurrency}
    print(json.dumps({'planned_fault':args.mode,'duration_seconds':args.duration,'execute':args.execute}))
    if not args.execute:
        return
    token=os.environ['ATLAS_TOKEN']
    (out/'rollback.json').write_text(json.dumps(rollback,indent=2)+'\n')
    changed=False
    report={'scenario':args.mode, 'model':'injected service fault; not a physical AWS Region outage',
            'fault_at':datetime.now(timezone.utc).isoformat(), 'samples':[], 'rto_seconds':None,
            'rpo_seconds':None, 'rollback_complete':False}
    started=time.monotonic()
    stable_since=None
    recovery_candidate=None
    try:
        changed=True # Also rollback when the mutation response is lost.
        if args.mode=='db-isolation':
            ec2.revoke_security_group_ingress(GroupId=args.security_group,IpPermissions=ingress)
        else:
            lamb.put_function_concurrency(FunctionName=args.function,ReservedConcurrentExecutions=0)
        while time.monotonic()-started < args.duration:
            elapsed=time.monotonic()-started
            command={'idempotency_key':f'chaos:{uuid.uuid4()}', 'customer_id':'synthetic-chaos','amount_cents':100}
            sample={'elapsed':elapsed,'command':command,'write_ok':False}
            try:
                request=urllib.request.Request(args.url.rstrip('/')+'/orders', data=json.dumps(command).encode(),
                    headers={'content-type':'application/json','x-atlas-token':token})
                with urllib.request.urlopen(request,timeout=10) as response:
                    sample['write_ok']=response.status==202
                    sample['receipts']=json.load(response)['receipts']
                if stable_since is None:
                    stable_since=time.monotonic()
                    recovery_candidate=elapsed
                if time.monotonic()-stable_since >= 30 and report['rto_seconds'] is None:
                    report['rto_seconds']=round(recovery_candidate,2)
            except Exception as exc:
                sample['error']=type(exc).__name__
                stable_since=None
                # A later outage invalidates a previous 30-second candidate.
                report['rto_seconds']=None
            report['samples'].append(sample)
            (out/'experiment.json').write_text(json.dumps(report,indent=2)+'\n')
            time.sleep(5)
    finally:
        if changed:
            restore(rollback)
            report['rollback_complete']=True
        report['rto_pass']=report['rto_seconds'] is not None and report['rto_seconds']<300
        (out/'experiment.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='samples'},indent=2))


def restore(value):
    import boto3
    if value['mode']=='db-isolation':
        ec2=boto3.client('ec2',region_name=value['region'])
        # Crash recovery may already have restored some rules; handle one permission at a time.
        for permission in value['permissions']:
            try:
                ec2.authorize_security_group_ingress(GroupId=value['security_group'],IpPermissions=[permission])
            except ec2.exceptions.ClientError as exc:
                if exc.response['Error']['Code']!='InvalidPermission.Duplicate':
                    raise
    else:
        lamb=boto3.client('lambda',region_name=value['region'])
        if value['concurrency'] is None:
            lamb.delete_function_concurrency(FunctionName=value['function'])
        else:
            lamb.put_function_concurrency(FunctionName=value['function'],ReservedConcurrentExecutions=value['concurrency'])


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=['api-outage','db-isolation'])
    p.add_argument('--project',default='atlas')
    p.add_argument('--region',default='eu-west-1')
    p.add_argument('--function',default='atlas-primary-api')
    p.add_argument('--security-group')
    p.add_argument('--url')
    p.add_argument('--duration',type=int,default=360)
    p.add_argument('--output',default='results/aws-chaos')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--restore',help='rollback.json from an interrupted experiment')
    args=p.parse_args()
    if args.restore:
        restore(json.loads(Path(args.restore).read_text()))
    else:
        if not args.mode or not args.url or args.mode=='db-isolation' and not args.security_group:
            p.error('mode, url, and security-group for db-isolation are required')
        main(args)
