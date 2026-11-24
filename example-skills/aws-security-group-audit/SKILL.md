---
name: aws-security-group-audit
description: Read-only review of AWS security groups and network ACLs — world-open ingress (0.0.0.0/0, ::/0), sensitive ports exposed, overly broad CIDRs, unused groups, and how to tell intended exposure from accidental exposure.
keywords: ["security group", "security groups", "open to the world", "open to the internet", "open port", "open ports", "exposed port", "publicly accessible", "ingress rule", "inbound rule", "egress rule", nacl, "network acl", "port 22", "port 3389", ssh, rdp, "unused security group"]
---

# Security group audit

Security groups are stateful allow-lists attached to network interfaces. The audit
question is always: **who can reach what, and is that intended?**

Posture: read-only. Use `ec2 describe-security-groups`, `describe-security-group-rules`,
`describe-network-interfaces` and `describe-network-acls`. Never add, revoke or modify a
rule — findings are recommendations for the owner.

## Step 1 — Find world-open ingress

From `describe-security-group-rules`, flag ingress rules where `CidrIpv4 = 0.0.0.0/0`
or `CidrIpv6 = ::/0`. Then grade by port:

| Exposure | Severity | Reasoning |
|----------|----------|-----------|
| 22 (SSH), 3389 (RDP) to the world | High | Remote admin should go through a bastion, session manager or VPN |
| Database ports (3306, 5432, 1433, 1521, 27017, 6379, 9200) to the world | Critical | Data stores should never be internet-reachable |
| Admin/management UIs (e.g. 8080, 9090, 2375/2376 Docker API, 10250 kubelet) | Critical | Often unauthenticated by default |
| All ports / all protocols (`IpProtocol = -1`) to the world | Critical | No port restriction at all |
| 80/443 on a group attached to a public load balancer | Usually intended | Verify it is attached to the LB, not to instances directly |

## Step 2 — Check what the group is attached to

A rule only matters if the group is in use. `describe-network-interfaces
--filters Name=group-id,Values=<sg>` shows instances, load balancers, RDS, Lambda ENIs, etc.

- Attached to a **public** subnet resource with a public IP → reachable from the internet.
- Attached only to private-subnet resources → the 0.0.0.0/0 rule is still broader than
  needed but not directly internet-exposed (inbound needs a path through an LB or NAT-less route).
- Not attached to anything → unused group (cleanup candidate, low risk).

## Step 3 — Broad but not world-open

Also worth flagging:

- Large private ranges (`10.0.0.0/8`, `172.16.0.0/12`) on sensitive ports — lateral
  movement risk; prefer security-group references (`UserIdGroupPairs`) over CIDRs.
- Wide port ranges (`0-65535`, `1024-65535`).
- Rules referencing another account's security group — confirm the peering is intended.
- Unrestricted egress (`0.0.0.0/0` all ports) on workloads handling sensitive data —
  normal by default, but a data-exfiltration path worth noting for sensitive tiers.

## Step 4 — Network ACLs

NACLs are stateless and subnet-wide. A permissive NACL is common and acceptable; a NACL
that **denies** expected traffic explains "the security group allows it but it still does
not connect". Remember return traffic needs ephemeral ports allowed in NACLs.

## Step 5 — Corroborate with managed findings

If AWS Security Hub, AWS Config rules (e.g. restricted SSH / restricted common ports) or
GuardDuty are enabled, cross-check — they may already track the same exposure and its age.
VPC Flow Logs (if enabled) show whether the open port actually receives external traffic.

## Answer format

Table: security group ID/name, rule (protocol, port, source), attached resources, internet
reachability (yes / no / unknown), severity, and recommended change for the owner
(restrict source CIDR, use SG reference, move admin access to a session manager, remove
unused group). State the regions and accounts scanned.
