import json
import os
import time
from contextlib import contextmanager
from .store import Store

_secret = None


def client(service):
    import boto3
    from botocore.config import Config
    return boto3.client(service, config=Config(connect_timeout=3, read_timeout=5,
                       retries={'mode': 'standard', 'max_attempts': 3}))


@contextmanager
def database():
    # New connection on each invocation: no stale writer socket across failover.
    import psycopg
    global _secret
    if _secret is None or time.time() - _secret[0] > 60:
        value = client('secretsmanager').get_secret_value(SecretId=os.environ['DB_SECRET_ARN'])
        _secret = (time.time(), json.loads(value['SecretString']))
    credentials = _secret[1]
    with psycopg.connect(host=os.environ['DB_HOST'], dbname='atlas',
                         user=credentials['username'], password=credentials['password'],
                         connect_timeout=3, sslmode=os.getenv('DB_SSLMODE', 'verify-full'),
                         sslrootcert='/var/task/rds-ca.pem', autocommit=True,
                         options='-c statement_timeout=10000 -c idle_in_transaction_session_timeout=15000') as conn:
        yield Store(conn, postgres=True, regions=tuple(os.environ['REGIONS'].split(',')))
