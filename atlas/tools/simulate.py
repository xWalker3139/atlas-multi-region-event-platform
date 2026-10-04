"""Reproducible local fault experiment; no AWS RTO/RPO claims."""
import argparse
import json
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atlas.local import connect


def run(count):
    random.seed(42)
    with tempfile.TemporaryDirectory() as temp:
        store = connect(temp + '/orders.db')
        store.migrate()
        start = time.perf_counter()
        receipts = []
        for i in range(0, count, 100):
            receipts.extend(store.accept([dict(idempotency_key=f'sim-{j}', customer_id='synthetic',
                amount_cents=100+j%1000) for j in range(i, min(i+100, count))]))
        ingestion_seconds = time.perf_counter()-start
        payloads = [r[0] for r in store.sql('SELECT payload FROM outbox ORDER BY event_id').fetchall()]
        start = time.perf_counter()
        # Primary consumer completes 20%, then its queue and publisher become unavailable.
        for p in payloads[:count//5]:
            store.consume(p)
        # Secondary delivery is independent of primary sent_at. Lost primary queues are irrelevant.
        secondary = []
        for shard in range(16):
            while True:
                rows = store.claim('eu-central-1', shard, limit=200)
                if not rows:
                    break
                for event_id, payload, token in rows:
                    secondary.append(payload)
                    store.mark_sent('eu-central-1', event_id, token)
        # Duplicate delivery, consumer lost ACK, then historical replay.
        duplicate_count = 0
        for p in secondary + payloads[:count//10] + random.sample(payloads, min(1000, count)):
            duplicate_count += int(not store.consume(p))
        processing_seconds = time.perf_counter()-start
        poison = '{"type":"OrderAccepted","version":999}'
        retries, dlq = 0, []
        for _ in range(5):
            try:
                store.consume(poison)
            except ValueError:
                retries += 1
        dlq.append(poison)
        # Compare identities, not just counts.
        accepted = {r['event_id'] for r in receipts}
        ledger = {r[0] for r in store.sql('SELECT event_id FROM ledger')}
        expected_amount = sum(100+j%1000 for j in range(count))
        actual_amount = store.sql('SELECT SUM(amount_cents) FROM ledger').fetchone()[0]
        snapshot = sqlite3.connect(temp + '/backup.db')
        store.conn.backup(snapshot)
        snapshot.close()
        restored = connect(temp + '/backup.db')
        restored_ids = {r[0] for r in restored.sql('SELECT event_id FROM ledger')}
        result = dict(environment='local SQLite; single database; regional workers emulated',
            accepted_events=count, ingestion_seconds=round(ingestion_seconds, 3),
            local_ingestion_events_per_second=round(count/ingestion_seconds, 1),
            processing_with_faults_seconds=round(processing_seconds, 3),
            local_processing_events_per_second=round(count/processing_seconds, 1),
            lost_orders=len(accepted-ledger), unexpected_effects=len(ledger-accepted),
            ledger_events=len(ledger), duplicate_deliveries_suppressed=duplicate_count,
            amount_conserved=actual_amount == expected_amount,
            poison_attempts=retries, dead_letter_events=len(dlq),
            restore_identity_match=restored_ids == accepted,
            aws_rto_seconds=None, aws_rpo_seconds=None,
            verdict='PASS' if accepted == ledger == restored_ids and expected_amount == actual_amount else 'FAIL')
        restored.conn.close()
        store.conn.close()
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--events', type=int, default=20000)
    parser.add_argument('--output', default='results/local-simulation.json')
    args = parser.parse_args()
    if args.events < 1:
        parser.error('--events must be positive')
    result = run(args.events)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    sys.exit(0 if result['verdict'] == 'PASS' else 1)
