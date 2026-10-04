"""Snapshot, cross-region copy and isolated restore. Never replaces a live cluster."""
import argparse
import json
import time


def main(args):
    import boto3
    rds=boto3.client('rds',region_name=args.region)
    if args.action=='snapshot':
        identifier=args.identifier or f'atlas-backup-{int(time.time())}'
        result=rds.create_db_cluster_snapshot(DBClusterIdentifier=args.cluster,DBClusterSnapshotIdentifier=identifier)
        rds.get_waiter('db_cluster_snapshot_available').wait(DBClusterSnapshotIdentifier=identifier)
        snapshot=result['DBClusterSnapshot']['DBClusterSnapshotArn']
        if args.copy_region:
            if not args.copy_kms_key:
                raise ValueError('cross-region encrypted copies require destination KMS key ARN')
            remote=boto3.client('rds',region_name=args.copy_region)
            remote.copy_db_cluster_snapshot(SourceDBClusterSnapshotIdentifier=snapshot,
                TargetDBClusterSnapshotIdentifier=identifier+'-copy',KmsKeyId=args.copy_kms_key,
                SourceRegion=args.region,CopyTags=True)
            remote.get_waiter('db_cluster_snapshot_available').wait(DBClusterSnapshotIdentifier=identifier+'-copy')
        print(json.dumps({'snapshot_arn':snapshot,'copy_region':args.copy_region}))
    else:
        if not args.identifier or not args.identifier.startswith('atlas-restore-'):
            raise ValueError('restore identifiers must start with atlas-restore-')
        if not args.execute:
            print(json.dumps({'dry_run':True,'restore_cluster':args.identifier,'snapshot':args.snapshot}))
            return
        result=rds.restore_db_cluster_from_snapshot(DBClusterIdentifier=args.identifier,
            SnapshotIdentifier=args.snapshot,Engine='aurora-postgresql',DBSubnetGroupName=args.subnet_group,
            VpcSecurityGroupIds=[args.security_group],DeletionProtection=True,Tags=[{'Key':'Project','Value':'atlas-restore'}])
        rds.get_waiter('db_cluster_available').wait(DBClusterIdentifier=args.identifier)
        rds.create_db_instance(DBInstanceIdentifier=args.identifier+'-writer',DBClusterIdentifier=args.identifier,
            Engine='aurora-postgresql',DBInstanceClass=args.instance_class,PubliclyAccessible=False)
        rds.get_waiter('db_instance_available').wait(DBInstanceIdentifier=args.identifier+'-writer')
        print(json.dumps({'restored_cluster':args.identifier,'endpoint':result['DBCluster']['Endpoint']}))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['snapshot','restore'])
    p.add_argument('--region',default='eu-west-1')
    p.add_argument('--cluster')
    p.add_argument('--identifier')
    p.add_argument('--copy-region')
    p.add_argument('--copy-kms-key')
    p.add_argument('--snapshot')
    p.add_argument('--subnet-group')
    p.add_argument('--security-group')
    p.add_argument('--instance-class',default='db.r6g.large')
    p.add_argument('--execute',action='store_true')
    args=p.parse_args()
    if args.action=='snapshot' and not args.cluster:
        p.error('--cluster required')
    if args.action=='restore' and not all([args.snapshot,args.subnet_group,args.security_group]):
        p.error('--snapshot --subnet-group --security-group required')
    main(args)
