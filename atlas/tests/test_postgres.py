"""Runs in CI on PostgreSQL 16. Local SQLite tests cannot validate PG locking."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from atlas.store import Store, Conflict
from tests.test_store import command


@unittest.skipUnless(os.getenv('TEST_POSTGRES_DSN'), 'requires PostgreSQL 16 and psycopg')
class PostgresTests(unittest.TestCase):
    def open(self):
        import psycopg
        return Store(psycopg.connect(os.environ['TEST_POSTGRES_DSN'], autocommit=True), postgres=True)

    def setUp(self):
        self.store = self.open()
        self.store.migrate()
        self.store.sql('TRUNCATE ledger,inbox,deliveries,outbox,orders CASCADE')

    def tearDown(self):
        self.store.conn.close()

    def test_concurrent_claims_and_dedup(self):
        receipts = self.store.accept([command(str(i)) for i in range(100)])
        def claim(_):
            store = self.open()
            try:
                return [r[0] for s in range(16) for r in store.claim('eu-west-1', s)]
            finally:
                store.conn.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            claimed = [v for batch in pool.map(claim, range(8)) for v in batch]
        self.assertEqual(len(claimed), 100)
        self.assertEqual(len(set(claimed)), 100)
        payload = self.store.sql('SELECT payload FROM outbox WHERE event_id=?', (receipts[0]['event_id'],)).fetchone()[0]
        def consume(_):
            store = self.open()
            try:
                return store.consume(payload)
            finally:
                store.conn.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            applied = list(pool.map(consume, range(20)))
        self.assertEqual(sum(applied), 1)

    def test_batch_rollback(self):
        self.store.accept([command('original')])
        with self.assertRaises(Conflict):
            self.store.accept([command('new'), command('original', 400)])
        self.assertEqual(self.store.reconcile()['orders'], 1)
