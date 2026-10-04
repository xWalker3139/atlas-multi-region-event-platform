import argparse
import json


if __name__=='__main__':
    import boto3
    p=argparse.ArgumentParser()
    p.add_argument('--regions',nargs=2,default=['eu-west-1','eu-central-1'])
    p.add_argument('--version',required=True)
    p.add_argument('--class',dest='instance_class',default='db.r6g.large')
    args=p.parse_args()
    if not args.version.startswith('16.'):
        raise ValueError('Terraform parameter groups require PostgreSQL 16')
    report=[]
    for region in args.regions:
        rds=boto3.client('rds',region_name=region)
        versions=rds.describe_db_engine_versions(Engine='aurora-postgresql',EngineVersion=args.version)['DBEngineVersions']
        options=rds.describe_orderable_db_instance_options(Engine='aurora-postgresql',EngineVersion=args.version,
                                                          DBInstanceClass=args.instance_class)['OrderableDBInstanceOptions']
        ok=bool(versions and any(x.get('SupportsGlobalDatabases') for x in options))
        report.append({'region':region,'version':args.version,'global_supported':ok})
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if all(r['global_supported'] for r in report) else 1)
