import hmac
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from .aws_runtime import client, database
from .store import Conflict

log = logging.getLogger('atlas')
log.setLevel(logging.INFO)
_api_secret = None


def response(status, body):
    return {'statusCode': status, 'headers': {'content-type': 'application/json'},
            'body': json.dumps(body)}


def api(event, context):
    global _api_secret
    path = event.get('rawPath', '/')
    method = event.get('requestContext', {}).get('http', {}).get('method', 'GET')
    try:
        if path == '/health' and method == 'GET':
            # Regional service failure and DB failure can be distinguished by /live.
            with database() as store:
                ready = store.health()
                return response(200 if ready else 503, {'ready': ready})
        if path == '/live' and method == 'GET':
            return response(200, {'live': True})
        if _api_secret is None or time.time() - _api_secret[0] > 60:
            secret = client('secretsmanager').get_secret_value(SecretId=os.environ['API_SECRET_ARN'])
            _api_secret = (time.time(), json.loads(secret['SecretString'])['token'])
        supplied = event.get('headers', {}).get('x-atlas-token', '')
        if not hmac.compare_digest(supplied, _api_secret[1]):
            return response(401, {'error': 'unauthorized'})
        if method != 'POST' or path not in ('/orders', '/orders/batch'):
            return response(404, {'error': 'not found'})
        raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            import base64
            raw = base64.b64decode(raw).decode()
        body = json.loads(raw)
        with database() as store:
            receipts = store.accept(body if path.endswith('/batch') else [body])
        log.info(json.dumps({'metric': 'Accepted', 'count': len(receipts), 'region': os.environ['AWS_REGION']}))
        return response(202, {'receipts': receipts})
    except Conflict as exc:
        return response(409, {'error': str(exc)})
    except (ValueError, TypeError, KeyError):
        return response(400, {'error': 'invalid request'})
    except Exception:
        log.exception('request failed')
        return response(503, {'error': 'temporarily unavailable; retry with same idempotency key'})


def publisher(event, context):
    region = os.environ['AWS_REGION']
    shard = int(event.get('shard', 0))
    sqs, s3 = client('sqs'), client('s3')
    deadline = time.monotonic() + 45
    sent = 0
    with database() as store, ThreadPoolExecutor(max_workers=8) as pool:
        while time.monotonic() < deadline:
            rows = store.claim(region, shard, limit=200)
            if not rows:
                break

            def send(chunk):
                # Archive before enqueue: immutable event ID, identical retry contents.
                for event_id, payload, _ in chunk:
                    try:
                        s3.put_object(Bucket=os.environ['ARCHIVE_BUCKET'], Key=f'events/{event_id}.json',
                                      Body=payload.encode(), ContentType='application/json', IfNoneMatch='*')
                    except Exception as exc:
                        if getattr(exc, 'response', {}).get('Error', {}).get('Code') != 'PreconditionFailed':
                            raise
                result = sqs.send_message_batch(QueueUrl=os.environ['QUEUE_URL'], Entries=[
                    {'Id': str(i), 'MessageBody': payload} for i, (_, payload, _) in enumerate(chunk)])
                success = {entry['Id'] for entry in result.get('Successful', [])}
                return [row for i, row in enumerate(chunk) if str(i) in success]

            futures = [pool.submit(send, rows[i:i+10]) for i in range(0, len(rows), 10)]
            successful = []
            for future in futures:
                successful.extend(future.result())
            sent += store.mark_sent_batch(region, successful)
    return {'sent': sent, 'shard': shard, 'region': region}


def consumer(event, context):
    failures = []
    with database() as store:
        for record in event.get('Records', []):
            try:
                applied = store.consume(record['body'])
                log.info(json.dumps({'metric': 'Applied' if applied else 'Duplicate', 'count': 1}))
            except Exception:
                log.exception('event failed')
                failures.append({'itemIdentifier': record['messageId']})
    return {'batchItemFailures': failures}


def migrate(event, context):
    with database() as store:
        if event.get('action') == 'audit':
            return {'events': store.audit(event['event_ids']), 'counts': store.reconcile()}
        if event.get('action') == 'recover':
            receipt = store.recover_event(event['event'])
            if event.get('process') is True:
                receipt['applied'] = store.consume(json.dumps(event['event']))
            return receipt
        store.migrate()
    return {'migrated': True}
