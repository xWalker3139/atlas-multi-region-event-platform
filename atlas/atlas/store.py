"""Shared SQL implementation. SQLite is a local test backend, not a replica model."""
import hashlib
import json
import time
import uuid
from contextlib import contextmanager

NAMESPACE = uuid.UUID('6315c5f6-43a0-45e4-b17e-d51b4c1a9ba5')
REGIONS = ('eu-west-1', 'eu-central-1')


class Conflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def validate(command):
    if not isinstance(command, dict):
        raise ValueError('command must be an object')
    key = command.get('idempotency_key')
    customer = command.get('customer_id')
    cents = command.get('amount_cents')
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise ValueError('idempotency_key: 1..128 characters')
    if not isinstance(customer, str) or not 1 <= len(customer) <= 128:
        raise ValueError('customer_id: 1..128 characters')
    if type(cents) is not int or not 1 <= cents <= 100_000_000:
        raise ValueError('amount_cents: integer in 1..100000000')
    return dict(idempotency_key=key, customer_id=customer, amount_cents=cents)


SCHEMA = '''
CREATE TABLE IF NOT EXISTS orders (
 id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
 fingerprint TEXT NOT NULL, customer_id TEXT NOT NULL, amount_cents BIGINT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('ACCEPTED','COMPLETED')), created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
 event_id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
 payload TEXT NOT NULL, shard INTEGER NOT NULL, created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
 event_id TEXT NOT NULL REFERENCES outbox(event_id), region TEXT NOT NULL,
 sent_at DOUBLE PRECISION, lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
 token TEXT, attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(event_id,region)
);
CREATE INDEX IF NOT EXISTS delivery_pending ON deliveries(region,sent_at,lease_until);
CREATE INDEX IF NOT EXISTS outbox_shard ON outbox(shard,created_at);
CREATE TABLE IF NOT EXISTS inbox (
 event_id TEXT PRIMARY KEY REFERENCES outbox(event_id), processed_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
 event_id TEXT PRIMARY KEY REFERENCES inbox(event_id), order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
 amount_cents BIGINT NOT NULL, created_at DOUBLE PRECISION NOT NULL
);
'''


class Store:
    def __init__(self, connection, postgres=False, regions=REGIONS, shards=16):
        self.conn, self.postgres = connection, postgres
        self.regions, self.shards = regions, shards

    def sql(self, statement, args=()):
        return self.conn.execute(statement.replace('?', '%s') if self.postgres else statement, args)

    @contextmanager
    def transaction(self):
        self.sql('BEGIN' if self.postgres else 'BEGIN IMMEDIATE')
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def migrate(self):
        with self.transaction():
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    self.sql(statement)

    def accept(self, commands, now=None):
        commands = [validate(c) for c in commands]
        if not 1 <= len(commands) <= 100:
            raise ValueError('batch must contain 1..100 commands')
        now = time.time() if now is None else now
        prepared, unique = [], {}
        for command in commands:
            order_id = str(uuid.uuid5(NAMESPACE, command['idempotency_key']))
            event_id = str(uuid.uuid5(NAMESPACE, 'OrderAccepted:' + order_id))
            fingerprint = hashlib.sha256(canonical(command).encode()).hexdigest()
            if order_id in unique and unique[order_id][2] != fingerprint:
                raise Conflict('idempotency key reused with different content')
            row = (order_id, event_id, fingerprint, command)
            prepared.append(row)
            unique[order_id] = row
        receipts = []
        with self.transaction():
            values = [(oid, c['idempotency_key'], fp, c['customer_id'], c['amount_cents'], 'ACCEPTED', now)
                      for oid, _, fp, c in unique.values()]
            inserted = {r[0] for r in self.sql('INSERT INTO orders VALUES ' +
                ','.join('(?,?,?,?,?,?,?)' for _ in values) +
                ' ON CONFLICT(idempotency_key) DO NOTHING RETURNING id',
                [cell for row in values for cell in row]).fetchall()}
            existing = dict(self.sql('SELECT id,fingerprint FROM orders WHERE id IN (' +
                ','.join('?' for _ in unique) + ')', list(unique)).fetchall())
            for oid, _, fingerprint, _ in unique.values():
                if existing.get(oid) != fingerprint:
                    raise Conflict('idempotency key reused with different content')
            events, deliveries = [], []
            for order_id, event_id, _, command in unique.values():
                if order_id not in inserted:
                    continue
                event = dict(event_id=event_id, order_id=order_id, type='OrderAccepted', version=1,
                    customer_id=command['customer_id'], amount_cents=command['amount_cents'],
                    created_at=now, idempotency_key=command['idempotency_key'])
                events.append((event_id, order_id, canonical(event), uuid.UUID(event_id).int % self.shards, now))
                deliveries.extend((event_id, region) for region in self.regions)
            if events:
                self.sql('INSERT INTO outbox VALUES ' + ','.join('(?,?,?,?,?)' for _ in events),
                         [cell for row in events for cell in row])
                self.sql('INSERT INTO deliveries(event_id,region) VALUES ' + ','.join('(?,?)' for _ in deliveries),
                         [cell for row in deliveries for cell in row])
            seen = set()
            for order_id, event_id, _, _ in prepared:
                receipts.append(dict(order_id=order_id, event_id=event_id,
                                     duplicate=order_id not in inserted or order_id in seen))
                seen.add(order_id)
        return receipts

    def claim(self, region, shard, limit=100, now=None, lease_seconds=120):
        now = time.time() if now is None else now
        token = str(uuid.uuid4())
        if self.postgres:
            rows = self.sql('''WITH candidates AS (
                SELECT d.event_id FROM deliveries d JOIN outbox o USING(event_id)
                WHERE d.region=? AND d.sent_at IS NULL AND d.lease_until<=? AND o.shard=?
                ORDER BY o.created_at LIMIT ? FOR UPDATE OF d SKIP LOCKED
              ) UPDATE deliveries d SET token=?,lease_until=?,attempts=d.attempts+1
                FROM candidates c,outbox o WHERE d.event_id=c.event_id AND o.event_id=d.event_id AND d.region=?
                RETURNING d.event_id,o.payload''',
                (region, now, shard, limit, token, now+lease_seconds, region)).fetchall()
            return [(event_id, payload, token) for event_id, payload in rows]
        with self.transaction():
            query = '''SELECT d.event_id,o.payload FROM deliveries d JOIN outbox o USING(event_id)
                WHERE d.region=? AND d.sent_at IS NULL AND d.lease_until<=? AND o.shard=?
                ORDER BY o.created_at LIMIT ?'''
            rows = self.sql(query, (region, now, shard, limit)).fetchall()
            for event_id, _ in rows:
                self.sql('UPDATE deliveries SET token=?,lease_until=?,attempts=attempts+1 WHERE event_id=? AND region=?',
                         (token, now + lease_seconds, event_id, region))
        return [(event_id, payload, token) for event_id, payload in rows]

    def mark_sent(self, region, event_id, token, now=None):
        with self.transaction():
            return self.sql('''UPDATE deliveries SET sent_at=?,lease_until=0
                WHERE event_id=? AND region=? AND token=?''',
                (time.time() if now is None else now, event_id, region, token)).rowcount == 1

    def mark_sent_batch(self, region, rows, now=None):
        if not rows:
            return 0
        # Every claim has one token. One statement avoids a WAN round trip per event.
        token = rows[0][2]
        if any(row[2] != token for row in rows):
            raise ValueError('one lease token required per batch')
        with self.transaction():
            query = 'UPDATE deliveries SET sent_at=?,lease_until=0 WHERE region=? AND token=? AND event_id IN ('
            return self.sql(query + ','.join('?' for _ in rows) + ')',
                [time.time() if now is None else now, region, token] + [r[0] for r in rows]).rowcount

    def consume(self, payload, now=None):
        event = json.loads(payload)
        if not isinstance(event, dict) or event.get('version') != 1 or event.get('type') != 'OrderAccepted':
            raise ValueError('unsupported event')
        now = time.time() if now is None else now
        if self.postgres:
            # Data-modifying CTE: one atomic statement and one network round trip.
            result = self.sql('''WITH source AS (
                  SELECT o.event_id,o.order_id,r.amount_cents FROM outbox o JOIN orders r ON r.id=o.order_id
                  WHERE o.event_id=? AND o.payload=?
                ), claimed AS (
                  INSERT INTO inbox(event_id,processed_at) SELECT event_id,? FROM source
                  ON CONFLICT(event_id) DO NOTHING RETURNING event_id
                ), applied AS (
                  INSERT INTO ledger(event_id,order_id,amount_cents,created_at)
                  SELECT s.event_id,s.order_id,s.amount_cents,? FROM source s JOIN claimed c USING(event_id)
                  RETURNING order_id
                ), completed AS (
                  UPDATE orders SET status='COMPLETED' FROM applied a WHERE orders.id=a.order_id RETURNING orders.id
                ) SELECT (SELECT COUNT(*) FROM source),(SELECT COUNT(*) FROM completed)''',
                (event['event_id'], canonical(event), now, now)).fetchone()
            if result[0] != 1:
                raise ValueError('event missing from outbox or content mismatch')
            return result[1] == 1
        with self.transaction():
            row = self.sql('SELECT payload FROM outbox WHERE event_id=?', (event['event_id'],)).fetchone()
            if not row or canonical(json.loads(row[0])) != canonical(event):
                raise ValueError('event missing from outbox or content mismatch')
            new = self.sql('''INSERT INTO inbox VALUES(?,?) ON CONFLICT(event_id)
                DO NOTHING RETURNING event_id''', (event['event_id'], now)).fetchone()
            if not new:
                return False
            self.sql('INSERT INTO ledger VALUES(?,?,?,?)',
                     (event['event_id'], event['order_id'], event['amount_cents'], now))
            self.sql("UPDATE orders SET status='COMPLETED' WHERE id=?", (event['order_id'],))
        return True

    def health(self):
        if self.postgres:
            row = self.sql('SELECT pg_is_in_recovery()').fetchone()
            return not row[0]
        return self.sql('SELECT 1').fetchone()[0] == 1

    def recover_event(self, event):
        """Operator-only archive recovery; validate identity BEFORE restoring an aggregate."""
        command = validate(event)
        order_id = str(uuid.uuid5(NAMESPACE, command['idempotency_key']))
        event_id = str(uuid.uuid5(NAMESPACE, 'OrderAccepted:' + order_id))
        timestamp = event.get('created_at')
        if type(timestamp) not in (int, float) or not 0 < timestamp < 10_000_000_000:
            raise ValueError('invalid archive timestamp')
        expected = dict(event_id=event_id, order_id=order_id, type='OrderAccepted', version=1,
                        customer_id=command['customer_id'], amount_cents=command['amount_cents'],
                        created_at=timestamp, idempotency_key=command['idempotency_key'])
        if canonical(event) != canonical(expected):
            raise ValueError('archive identity mismatch')
        return self.accept([command], now=timestamp)[0]

    def audit(self, event_ids):
        if not isinstance(event_ids, list) or not 1 <= len(event_ids) <= 1000:
            raise ValueError('audit requires 1..1000 event IDs')
        rows = self.sql('''SELECT o.event_id, r.status, r.amount_cents,l.amount_cents, r.created_at,i.processed_at
            FROM outbox o JOIN orders r ON r.id=o.order_id LEFT JOIN ledger l ON l.event_id=o.event_id
            LEFT JOIN inbox i ON i.event_id=o.event_id
            WHERE o.event_id IN (''' + ','.join('?' for _ in event_ids) + ')', event_ids).fetchall()
        return [{'event_id': r[0], 'status': r[1], 'amount_cents': r[2],
                 'ledger_amount_cents': r[3], 'created_at': r[4], 'processed_at': r[5]} for r in rows]

    def reconcile(self):
        return {name: self.sql('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                for name in ('orders', 'outbox', 'inbox', 'ledger')}
