"""Replay acknowledged event IDs from immutable archives; preserves event identity."""
import argparse
import json
from pathlib import Path


def main(args):
    import boto3
    s3=boto3.client('s3',region_name=args.region)
    sqs=boto3.client('sqs',region_name=args.region)
    lamb=boto3.client('lambda',region_name=args.region)
    ids=sorted({r['event_id'] for line in Path(args.receipts).read_text().splitlines()
                for r in json.loads(line).get('receipts',[])})
    sent=0
    for event_id in ids:
        body=s3.get_object(Bucket=args.bucket,Key=f'events/{event_id}.json')['Body'].read()
        event=json.loads(body)
        if event['event_id']!=event_id:
            raise ValueError('archive key/content mismatch')
        if args.execute:
            if args.recover_function:
                result=lamb.invoke(FunctionName=args.recover_function,
                    Payload=json.dumps({'action':'recover','event':event}).encode())
                if result.get('FunctionError'):
                    raise RuntimeError(result['Payload'].read().decode())
            sqs.send_message(QueueUrl=args.queue,MessageBody=body.decode())
            sent+=1
    print(json.dumps({'selected_events':len(ids),'sent':sent,'dry_run':not args.execute}))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--receipts',required=True)
    p.add_argument('--bucket',required=True)
    p.add_argument('--queue',required=True)
    p.add_argument('--region',default='eu-central-1')
    p.add_argument('--recover-function',help='explicit aggregate rehydration after snapshot restore')
    p.add_argument('--execute',action='store_true')
    main(p.parse_args())
