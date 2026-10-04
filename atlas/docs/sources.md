# Surse primare

Documentația consultată pentru această implementare:

- [Aurora switchover/failover, limite RPO și split-brain](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html).
- [Global writer endpoint și conexiuni între regiuni](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-connecting.html).
- [Aurora CloudWatch metrics, inclusiv RPOLag](https://docs.aws.amazon.com/us_en/AmazonRDS/latest/AuroraUserGuide/Aurora.AuroraMonitoring.Metrics.html).
- [Monitoring Aurora Global Database](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-monitoring.html).
- [RDS FailoverGlobalCluster API](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_FailoverGlobalCluster.html).
- [AWS Backup feature availability](https://docs.aws.amazon.com/aws-backup/latest/devguide/backup-feature-availability.html).
- [AWS Backup encryption](https://docs.aws.amazon.com/aws-backup/latest/devguide/encryption.html).
- [DynamoDB global tables consistency, transactions și streams](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/V2globaltables_HowItWorks.html).
- [MRSC: două replici + witness sau trei replici](https://aws.amazon.com/blogs/aws/build-the-highest-resilience-apps-with-multi-region-strong-consistency-in-amazon-dynamodb-global-tables/).
- [Terraform aws_rds_global_cluster și endpoint](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/rds_global_cluster).

Disponibilitatea versiunilor engine-ului și quotas se verifică în contul de deploy; `tools/preflight.py` nu substituie `terraform validate`, planul sau testul AWS.
