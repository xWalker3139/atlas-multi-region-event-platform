import argparse
import json
from pathlib import Path
import time


def run(args):
    import boto3
    records = [json.loads(line) for line in Path(args.receipts).read_text().splitlines() if line]
    expected = {}
    for result in records:
        for command, receipt in zip(result['commands'], result['receipts']):
            expected[receipt['event_id']] = dict(amount=command['amount_cents'], accepted_at=result['accepted_at'])
    if not expected:
        raise ValueError('no acknowledged receipts to verify')
    lambda_client = boto3.client('lambda', region_name=args.region)
    deadline = time.monotonic()+args.wait
    while True:
        actual = {}
        event_ids = sorted(expected)
        for i in range(0,len(event_ids),1000):
            response = lambda_client.invoke(FunctionName=args.function, Payload=json.dumps(
                {'action':'audit', 'event_ids':event_ids[i:i+1000]}).encode())
            value = json.load(response['Payload'])
            if response.get('FunctionError'):
                raise RuntimeError(value)
            actual.update({x['event_id']:x for x in value['events']})
        missing = sorted(set(expected)-set(actual))
        incomplete = [key for key in expected if key in actual and actual[key]['status'] != 'COMPLETED']
        wrong = [key for key in expected if key in actual and (actual[key]['amount_cents'] != expected[key]['amount']
                 or (actual[key]['status']=='COMPLETED' and actual[key]['ledger_amount_cents'] != expected[key]['amount']))]
        if not missing and not incomplete and not wrong or time.monotonic() >= deadline:
            break
        time.sleep(min(10, max(0,deadline-time.monotonic())))
    processed_times = [x['processed_at'] for x in actual.values() if x['processed_at'] is not None]
    window = max(processed_times)-min(processed_times) if len(processed_times)>1 else 0
    processing_rate = (len(processed_times)-1)/window if window>0 else None
    report = dict(acknowledged_unique_events=len(expected), missing_orders=missing, incomplete_events=incomplete,
                  incorrect_effects=wrong, checked_at=time.time(), zero_loss_pass=not missing and not incomplete and not wrong,
                  processed_unique_events_per_second=processing_rate,
                  processing_rate_pass=processing_rate is not None and processing_rate>=args.min_rate,
                  rpo_seconds=None,
                  note='RPO cannot be inferred from row counts; use fault timestamp, missing receipts and Aurora lag metrics.')
    Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v if not isinstance(v,list) else len(v) for k,v in report.items()},indent=2))
    return 0 if report['zero_loss_pass'] else 1


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--receipts',required=True)
    p.add_argument('--function',required=True)
    p.add_argument('--region',default='eu-central-1')
    p.add_argument('--wait',type=int,default=600)
    p.add_argument('--output',default='results/reconciliation.json')
    p.add_argument('--min-rate',type=int,default=500)
    raise SystemExit(run(p.parse_args()))
