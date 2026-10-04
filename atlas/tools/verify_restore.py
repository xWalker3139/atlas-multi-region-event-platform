"""Audit and replay against a restored DB via a temporary private Lambda."""
import argparse
import json
from pathlib import Path
import time


def main(args):
    import boto3
    records=[json.loads(line) for line in Path(args.receipts).read_text().splitlines() if line]
    expected={r['event_id']:c['amount_cents'] for row in records
              for c,r in zip(row['commands'],row.get('receipts',[]))}
    ids=sorted(expected)
    if args.max_events:
        ids=ids[:args.max_events]
    if not ids or not args.name.startswith('atlas-restore-'):
        raise ValueError('receipts and atlas-restore-* function name required')
    lamb=boto3.client('lambda',region_name=args.region)
    source=lamb.get_function_configuration(FunctionName=args.source_function)
    if args.db_host==source['Environment']['Variables']['DB_HOST']:
        raise ValueError('refusing live writer hostname')
    print(json.dumps({'selected':len(ids),'all_receipts':len(expected),'host':args.db_host,'execute':args.execute}))
    if not args.execute:
        return 0
    created=False
    try:
        lamb.create_function(FunctionName=args.name,Runtime='python3.12',Role=source['Role'],
            Handler='atlas.handlers.migrate',Code={'ZipFile':Path(args.package).read_bytes()},
            Timeout=60,MemorySize=1024,VpcConfig={k:source['VpcConfig'][k] for k in ('SubnetIds','SecurityGroupIds')},
            Environment={'Variables':dict(source['Environment']['Variables'],DB_HOST=args.db_host)},
            Tags={'Project':'atlas-restore'})
        created=True
        lamb.get_waiter('function_active_v2').wait(FunctionName=args.name)
        def invoke(payload):
            response=lamb.invoke(FunctionName=args.name,Payload=json.dumps(payload).encode())
            body=json.load(response['Payload'])
            if response.get('FunctionError'):
                raise RuntimeError(body)
            return body
        def audit():
            return {x['event_id']:x for i in range(0,len(ids),1000)
                    for x in invoke({'action':'audit','event_ids':ids[i:i+1000]})['events']}
        before=audit()
        s3=boto3.client('s3',region_name=args.region)
        rehydrated=0
        for event_id in ids:
            event=json.loads(s3.get_object(Bucket=args.archive_bucket,Key=f'events/{event_id}.json')['Body'].read())
            if event['event_id']!=event_id:
                raise ValueError('archive identity mismatch')
            result=invoke({'action':'recover','event':event,'process':True})
            rehydrated+=int(not result['duplicate'])
            # Lost ACK + replay must not cause a second effect on the restored database.
            if invoke({'action':'recover','event':event,'process':True})['applied']:
                raise AssertionError('duplicate replay applied an additional effect')
        after=audit()
        bad=[key for key in ids if key not in after or after[key]['status']!='COMPLETED'
             or after[key]['amount_cents']!=expected[key] or after[key]['ledger_amount_cents']!=expected[key]]
        report={'checked_at':time.time(),'sample':len(ids)<len(expected),'selected_events':len(ids),
                'acknowledged_events':len(expected),'present_before_replay':len(before),
                'rehydrated_events':rehydrated,'duplicate_replay_suppressed':len(ids),
                'incorrect_or_missing':bad,'pass':not bad}
    finally:
        if created:
            lamb.delete_function(FunctionName=args.name)
    report['temporary_function_removed']=True
    Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if report['pass'] else 1


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--db-host',required=True)
    p.add_argument('--receipts',required=True)
    p.add_argument('--archive-bucket',required=True)
    p.add_argument('--source-function',default='atlas-secondary-migrate')
    p.add_argument('--package',default='dist/atlas-lambda.zip')
    p.add_argument('--region',default='eu-central-1')
    p.add_argument('--name',default='atlas-restore-verifier')
    p.add_argument('--max-events',type=int,default=1000)
    p.add_argument('--output',default='results/restore-audit.json')
    p.add_argument('--execute',action='store_true')
    args=p.parse_args()
    if args.max_events<0:
        p.error('max-events must be nonnegative')
    raise SystemExit(main(args))
