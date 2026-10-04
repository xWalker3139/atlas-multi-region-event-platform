"""Open-loop batch load with durable intentions/receipts and stable retry keys."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import json
import math
import os
from pathlib import Path
import random
import time
import urllib.request
import uuid


def main(args):
    if args.rate <= 0 or args.duration <= 0 or not 1 <= args.batch <= 100:
        raise ValueError('positive rate/duration and batch in 1..100 required')
    token = os.environ['ATLAS_TOKEN']
    run_id = args.run_id or str(uuid.uuid4())
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.glob('*.json*')):
        raise ValueError('output directory must be empty; keep previous experiment evidence')
    count = int(args.rate*args.duration)
    requests = math.ceil(count/args.batch)
    started = time.monotonic()
    outcomes, pending = [], set()

    def send(commands):
        begin = time.monotonic()
        retries = 0
        error = ''
        while time.monotonic()-begin < args.retry_budget:
            try:
                request = urllib.request.Request(args.url.rstrip('/')+'/orders/batch',
                    data=json.dumps(commands).encode(), headers={'content-type':'application/json', 'x-atlas-token':token})
                with urllib.request.urlopen(request, timeout=15) as response:
                    if response.status == 202:
                        data = json.load(response)
                        if len(data['receipts']) != len(commands):
                            raise ValueError('invalid receipt count')
                        return dict(commands=commands, receipts=data['receipts'], accepted_at=time.time(),
                                    elapsed_seconds=time.monotonic()-begin, retries=retries)
            except Exception as exc:
                error = type(exc).__name__
                if getattr(exc, 'code', 0) in (400, 401, 403, 409):
                    break
            retries += 1
            time.sleep(random.uniform(0, min(5, 0.1*2**min(retries, 8))))
        return dict(commands=commands, receipts=[], error=error, retries=retries)

    with ThreadPoolExecutor(max_workers=args.workers) as pool, \
         (destination/'intentions.jsonl').open('w') as intentions, \
         (destination/'receipts.jsonl').open('w') as receipts:
        max_schedule_lag = 0
        for n in range(requests):
            due = started + n*args.batch/args.rate
            delay = due-time.monotonic()
            if delay > 0:
                time.sleep(delay)
            while len(pending) >= args.workers*2:
                completed, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    value = future.result()
                    receipts.write(json.dumps(value)+'\n'); receipts.flush()
                    outcomes.append(value)
            max_schedule_lag = max(max_schedule_lag, time.monotonic()-due)
            commands = [dict(idempotency_key=f'{run_id}:{i}', customer_id='synthetic-load',
                amount_cents=100+i%1000) for i in range(n*args.batch, min((n+1)*args.batch,count))]
            intentions.write(json.dumps({'commands':commands})+'\n'); intentions.flush()
            os.fsync(intentions.fileno())
            pending.add(pool.submit(send, commands))
        for future in pending:
            value = future.result()
            receipts.write(json.dumps(value)+'\n'); receipts.flush()
            outcomes.append(value)
    elapsed = time.monotonic()-started
    accepted = {r['event_id'] for result in outcomes for r in result['receipts']}
    summary = dict(run_id=run_id, offered_unique_events=count, accepted_unique_events=len(accepted),
        unknown_or_rejected_events=count-len(accepted), requested_unique_events_per_second=args.rate,
        duration_including_retries_seconds=elapsed, acceptance_events_per_second=len(accepted)/elapsed,
        max_schedule_lag_seconds=max_schedule_lag, retries=sum(x['retries'] for x in outcomes),
        processing_rate_verified=False,
        acceptance_load_pass=len(accepted)==count and len(accepted)/elapsed >= args.rate*0.95 and max_schedule_lag<1)
    (destination/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    return 0 if len(accepted)==count else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--url', required=True)
    p.add_argument('--rate', type=int, default=1000, help='unique events per second')
    p.add_argument('--duration', type=int, default=600)
    p.add_argument('--batch', type=int, default=100)
    p.add_argument('--workers', type=int, default=64)
    p.add_argument('--retry-budget', type=int, default=360)
    p.add_argument('--run-id')
    p.add_argument('--output', default='results/aws-load')
    raise SystemExit(main(p.parse_args()))
