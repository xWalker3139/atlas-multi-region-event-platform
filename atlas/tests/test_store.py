import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from atlas.local import connect
from atlas.store import Conflict


def command(key='order-1', amount=1999):
    return dict(idempotency_key=key, customer_id='test-customer', amount_cents=amount)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = self.temp.name + '/atlas.db'
        self.store = connect(self.path)
        self.store.migrate()

    def tearDown(self):
        self.store.conn.close()
        self.temp.cleanup()

    def payload(self):
        return self.store.sql('SELECT payload FROM outbox LIMIT 1').fetchone()[0]

    def test_atomic_outbox(self):
        self.store.accept([command()])
        self.assertEqual(self.store.reconcile(), dict(orders=1, outbox=1, inbox=0, ledger=0))
        self.assertEqual(self.store.sql('SELECT COUNT(*) FROM deliveries').fetchone()[0], 2)

    def test_duplicate_request_and_conflict(self):
        first = self.store.accept([command()])[0]
        second = self.store.accept([command()])[0]
        self.assertEqual(first['event_id'], second['event_id'])
        self.assertTrue(second['duplicate'])
        with self.assertRaises(Conflict):
            self.store.accept([command(amount=2000)])
        self.assertEqual(self.store.reconcile()['orders'], 1)

    def test_batch_rollback(self):
        self.store.accept([command('existing')])
        with self.assertRaises(Conflict):
            self.store.accept([command('new'), command('existing', 55)])
        self.assertEqual(self.store.reconcile()['orders'], 1)
        self.assertEqual(self.store.reconcile()['outbox'], 1)

    def test_invalid_commands(self):
        for bad in [None, {}, command(amount=True), command(amount=0), command('x'*129)]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.accept([bad])
        self.assertEqual(self.store.reconcile()['orders'], 0)

    def test_duplicate_keys_within_batch(self):
        receipts = self.store.accept([command(), command()])
        self.assertFalse(receipts[0]['duplicate'])
        self.assertTrue(receipts[1]['duplicate'])
        self.assertEqual(self.store.reconcile()['outbox'], 1)
        with self.assertRaises(Conflict):
            self.store.accept([command('other'), command('other', 300)])
        self.assertEqual(self.store.reconcile()['orders'], 1)

    def test_duplicate_event_atomic_effect(self):
        self.store.accept([command()])
        self.assertTrue(self.store.consume(self.payload()))
        self.assertFalse(self.store.consume(self.payload()))
        self.assertEqual(self.store.reconcile(), dict(orders=1, outbox=1, inbox=1, ledger=1))

    def test_effect_failure_rolls_back_inbox(self):
        self.store.accept([command()])
        self.store.sql("CREATE TRIGGER reject_ledger BEFORE INSERT ON ledger BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(Exception):
            self.store.consume(self.payload())
        self.assertEqual(self.store.reconcile()['inbox'], 0)
        self.store.sql('DROP TRIGGER reject_ledger')
        self.assertTrue(self.store.consume(self.payload()))

    def test_tampered_and_unknown_events(self):
        self.store.accept([command()])
        value = json.loads(self.payload())
        value['amount_cents'] += 1
        with self.assertRaises(ValueError):
            self.store.consume(json.dumps(value))
        value['event_id'] = 'missing'
        with self.assertRaises(ValueError):
            self.store.consume(json.dumps(value))
        self.assertEqual(self.store.reconcile()['ledger'], 0)

    def test_send_crash_then_lease_retry(self):
        self.store.accept([command()])
        shard = self.store.sql('SELECT shard FROM outbox').fetchone()[0]
        first = self.store.claim('eu-west-1', shard, now=0)
        self.assertEqual(len(first), 1)
        self.assertEqual(self.store.claim('eu-west-1', shard, now=119), [])
        second = self.store.claim('eu-west-1', shard, now=121)
        self.assertEqual(second[0][0], first[0][0])
        self.assertFalse(self.store.mark_sent('eu-west-1', first[0][0], first[0][2]))
        self.assertTrue(self.store.mark_sent('eu-west-1', second[0][0], second[0][2]))
        self.assertEqual(self.store.claim('eu-west-1', shard, now=999), [])

    def test_independent_region_deliveries(self):
        self.store.accept([command()])
        shard = self.store.sql('SELECT shard FROM outbox').fetchone()[0]
        primary = self.store.claim('eu-west-1', shard)[0]
        self.store.mark_sent('eu-west-1', primary[0], primary[2])
        secondary = self.store.claim('eu-central-1', shard)[0]
        self.assertEqual(primary[0], secondary[0])

    def test_concurrent_requests_same_key(self):
        def accept(_):
            store = connect(self.path)
            try:
                return store.accept([command()])[0]
            finally:
                store.conn.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(accept, range(24)))
        self.assertEqual(sum(not r['duplicate'] for r in receipts), 1)
        self.assertEqual(self.store.reconcile()['outbox'], 1)

    def test_concurrent_consume_same_event(self):
        self.store.accept([command()])
        payload = self.payload()
        def consume(_):
            store = connect(self.path)
            try:
                return store.consume(payload)
            finally:
                store.conn.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            applied = list(pool.map(consume, range(24)))
        self.assertEqual(sum(applied), 1)
        self.assertEqual(self.store.reconcile()['ledger'], 1)

    def test_backup_restore(self):
        import sqlite3
        self.store.accept([command()])
        self.store.consume(self.payload())
        backup = sqlite3.connect(self.temp.name + '/snapshot.db')
        self.store.conn.backup(backup)
        backup.close()
        restored = connect(self.temp.name + '/snapshot.db')
        self.assertEqual(restored.reconcile(), self.store.reconcile())
        self.assertFalse(restored.consume(self.payload()))
        restored.conn.close()
