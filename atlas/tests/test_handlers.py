from contextlib import contextmanager
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from atlas import handlers
from atlas.local import connect
from tests.test_store import command


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=self.temp.name+'/db'
        self.store=connect(self.path)
        self.store.migrate()
        @contextmanager
        def database():
            store=connect(self.path)
            try:
                yield store
            finally:
                store.conn.close()
        self.patch=patch.object(handlers,'database',database)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.store.conn.close()
        self.temp.cleanup()

    def test_partial_batch_failure_and_redelivery(self):
        self.store.accept([command()])
        payload=self.store.sql('SELECT payload FROM outbox').fetchone()[0]
        records=[{'messageId':'good','body':payload},{'messageId':'bad','body':'invalid json'}]
        with self.assertLogs('atlas',level='INFO'):
            result=handlers.consumer({'Records':records},None)
            repeated=handlers.consumer({'Records':records[:1]},None)
        self.assertEqual(result,{'batchItemFailures':[{'itemIdentifier':'bad'}]})
        self.assertEqual(repeated,{'batchItemFailures':[]})
        self.assertEqual(self.store.reconcile()['ledger'],1)

    def test_only_successful_sqs_entries_marked_sent(self):
        self.store.accept([command(str(i)) for i in range(40)])
        shard=self.store.sql('SELECT shard FROM outbox GROUP BY shard HAVING COUNT(*)>=2 LIMIT 1').fetchone()[0]
        expected=self.store.sql('SELECT COUNT(*) FROM outbox WHERE shard=?',(shard,)).fetchone()[0]
        class SQS:
            def send_message_batch(self,**kwargs):
                return {'Successful':[{'Id':kwargs['Entries'][0]['Id']}],
                        'Failed':[{'Id':x['Id']} for x in kwargs['Entries'][1:]]}
        class S3:
            def put_object(self,**kwargs):
                pass
        with patch.dict(os.environ,{'AWS_REGION':'eu-west-1','QUEUE_URL':'queue','ARCHIVE_BUCKET':'archive'}), \
             patch.object(handlers,'client',side_effect=lambda name:SQS() if name=='sqs' else S3()):
            result=handlers.publisher({'shard':shard},None)
        self.assertEqual(result['sent'],1)
        pending=self.store.sql("SELECT COUNT(*) FROM deliveries d JOIN outbox o USING(event_id) WHERE d.region='eu-west-1' AND o.shard=? AND sent_at IS NULL",(shard,)).fetchone()[0]
        self.assertEqual(pending,expected-1)

    def test_recovery_from_archive_preserves_id(self):
        receipt=self.store.accept([command()])[0]
        archived=json.loads(self.store.sql('SELECT payload FROM outbox').fetchone()[0])
        restored=connect(self.temp.name+'/restored')
        restored.migrate()
        self.assertEqual(restored.recover_event(archived)['event_id'],receipt['event_id'])
        self.assertTrue(restored.consume(json.dumps(archived)))
        tampered=dict(archived,event_id='forged')
        with self.assertRaises(ValueError):
            restored.recover_event(tampered)
        self.assertEqual(restored.reconcile()['orders'],1)
        restored.conn.close()
