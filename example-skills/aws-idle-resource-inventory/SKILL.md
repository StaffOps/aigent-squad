---
name: aws-idle-resource-inventory
description: Cost-aware, read-only inventory of common AWS waste — stopped or idle EC2 instances, unattached EBS volumes, old snapshots, unused Elastic IPs, idle load balancers, NAT gateways and S3 buckets without lifecycle rules — and how to estimate what each costs.
keywords: [unattached, "unattached ebs", "unattached volumes", "orphaned", orphan, "idle resources", "idle instances", "stopped instances", "unused", "elastic ip", "unused eip", "old snapshots", snapshots, "gp2 to gp3", gp2, "lifecycle policy", "lifecycle rule", "incomplete multipart", "idle load balancer", "nat gateway", waste, "clean up", cleanup, inventory]
---

# Inventory of idle and orphaned AWS resources

Goal: list resources that cost money without doing work, with an estimated monthly cost
and the evidence that they are idle. The agent reports; owners decide and delete.

Posture: read-only. Use `describe-*`, `list-*` and `get-*` calls and CloudWatch metrics.
Never stop, delete or modify a resource; deletion is a recommendation with a safety note.

## Always scope first

Inventory is per region and per account. State which regions/accounts were scanned —
an "all clear" for one region says nothing about the others. Prefer tag-based ownership
(`owner`, `team`, cost allocation tags) to attribute each finding.

## Checklist

| Resource | How to detect (read-only) | Idle signal | Cost driver |
|----------|---------------------------|-------------|-------------|
| EBS volumes | `ec2 describe-volumes --filters Name=status,Values=available` | `available` = attached to nothing | Billed per GB-month (+ provisioned IOPS/throughput) regardless of use |
| EBS type | `describe-volumes` → `VolumeType` | `gp2` volumes | `gp3` is typically cheaper per GB with baseline performance included |
| Snapshots | `ec2 describe-snapshots --owner-ids self` | Old `StartTime`, source volume deleted, not referenced by an AMI or backup plan | Billed per GB-month of changed blocks |
| AMIs | `ec2 describe-images --owners self` | Not used by any instance/launch template | Their backing snapshots |
| Stopped EC2 | `describe-instances --filters Name=instance-state-name,Values=stopped` | Stopped for weeks (`StateTransitionReason` has the date) | Compute stops, **attached EBS and EIPs keep billing** |
| Idle running EC2 | CloudWatch `CPUUtilization` + `NetworkIn/Out` over 14+ days | Max CPU very low and near-zero network | Full instance price — see `ec2-rightsizing-patterns` |
| Elastic IPs | `ec2 describe-addresses` | No `AssociationId` | Public IPv4 addresses are billed per hour, associated or not |
| Load balancers | `elbv2 describe-load-balancers` + `describe-target-health` | No registered/healthy targets; CloudWatch request count ~0 | Hourly charge + capacity units |
| NAT gateways | `ec2 describe-nat-gateways` + CloudWatch `BytesOutToDestination` | Near-zero traffic, or one per AZ in a non-production VPC | Hourly charge + per-GB processing |
| S3 buckets | `s3api get-bucket-lifecycle-configuration` (error = none) | No lifecycle on log/backup buckets; versioning on with no noncurrent expiry | Storage class + noncurrent versions |
| S3 multipart | `s3api list-multipart-uploads` | Old incomplete uploads | Billed as storage, invisible in object listings |
| CloudWatch Logs | `logs describe-log-groups` → `retentionInDays` | Missing retention (never expire) on large groups | Storage per GB-month |

## Estimating cost

- Use the account's own pricing where available (Cost Explorer / CUR grouped by resource or
  usage type); otherwise give a range from public on-demand prices and say it is an estimate.
- Multiply by size and hours: an unattached volume costs size × GB-month price every month.
- Rank findings by monthly cost; ten small items rarely beat one idle NAT gateway.

## Avoiding false positives

- A stopped instance may be a deliberate standby or DR node — check tags and recent starts.
- Snapshots may be the only backup of a deleted volume — never call them "safe to delete"
  without the owner's confirmation and a retention policy check.
- Low CPU does not mean idle for memory-bound or bursty workloads; look at network and
  application metrics too.
- Load balancers with no targets can belong to a blue/green pair awaiting cutover.

## Answer format

A table: resource ID, type/size, region, owner tag, idle evidence (metric + window),
estimated monthly cost, and a recommended action for the owner (snapshot then delete,
migrate gp2→gp3, add lifecycle rule, release EIP). End with the total estimate, the scope
scanned and what was not checked.
