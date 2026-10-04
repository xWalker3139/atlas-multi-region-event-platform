"""Standby-region supervisor. No automatic failback."""
import json
import os
import time
import urllib.request
from .aws_runtime import client


def probe(url):
    try:
        with urllib.request.urlopen(url, timeout=4) as result:
            return result.status == 200
    except Exception:
        return False


def decision(primary_ready, secondary_ready, secondary_live, failures, writer_is_primary):
    if not writer_is_primary:
        return 'already_failed_over', 0
    if primary_ready or secondary_ready:
        return 'healthy_database', 0
    if not secondary_live:
        return 'standby_service_unavailable', 0
    failures += 1
    return ('promote' if failures >= 3 else 'suspect'), failures


def handler(event, context):
    ddb, rds = client('dynamodb'), client('rds')
    key = {'id': {'S': 'supervisor'}}
    table = os.environ['CONTROL_TABLE']
    now = int(time.time())
    # Reserved concurrency 1 + DynamoDB conditional lease also protects manual invocations.
    try:
        ddb.update_item(TableName=table, Key=key,
            UpdateExpression='SET lease_until=:lease',
            ConditionExpression='attribute_not_exists(lease_until) OR lease_until < :now',
            ExpressionAttributeValues={':lease': {'N': str(now+55)}, ':now': {'N': str(now)}})
    except ddb.exceptions.ConditionalCheckFailedException:
        return {'action': 'locked'}
    try:
        state = ddb.get_item(TableName=table, Key=key, ConsistentRead=True).get('Item', {})
        topology = rds.describe_global_clusters(GlobalClusterIdentifier=os.environ['GLOBAL_CLUSTER'])['GlobalClusters'][0]
        writer = next((m['DBClusterArn'] for m in topology['GlobalClusterMembers'] if m['IsWriter']), '')
        if topology['Status'] != 'available' or int(state.get('cooldown', {'N': '0'})['N']) > now:
            return {'action': 'transition_or_cooldown', 'status': topology['Status']}
        primary_ready = probe(os.environ['PRIMARY_URL'] + '/health')
        secondary_live = probe(os.environ['SECONDARY_URL'] + '/live')
        secondary_ready = probe(os.environ['SECONDARY_URL'] + '/health')
        action, failures = decision(primary_ready, secondary_ready, secondary_live,
            int(state.get('failures', {'N': '0'})['N']), writer == os.environ['PRIMARY_CLUSTER_ARN'])
        ddb.update_item(TableName=table, Key=key, UpdateExpression='SET failures=:n',
                        ExpressionAttributeValues={':n': {'N': str(failures)}})
        if action == 'promote':
            # Managed failover may lose unreplicated commits. This is explicit in docs and reports.
            rds.failover_global_cluster(GlobalClusterIdentifier=os.environ['GLOBAL_CLUSTER'],
                TargetDbClusterIdentifier=os.environ['SECONDARY_CLUSTER_ARN'], AllowDataLoss=True)
            ddb.update_item(TableName=table, Key=key, UpdateExpression='SET cooldown=:n, failures=:zero',
                ExpressionAttributeValues={':n': {'N': str(now+600)}, ':zero': {'N': '0'}})
        print(json.dumps({'action': action, 'failures': failures, 'writer': writer, 'at': now}))
        return {'action': action}
    finally:
        ddb.update_item(TableName=table, Key=key, UpdateExpression='SET lease_until=:zero',
                        ExpressionAttributeValues={':zero': {'N': '0'}})
