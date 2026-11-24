---
name: guardduty-finding-triage
description: How to triage an Amazon GuardDuty finding read-only — read the finding type, identify the affected resource and actor, judge true vs false positive, and propose containment steps for a human to approve.
keywords: [guardduty, "guard duty", "threat detection", "finding type", unauthorizedaccess, recon, cryptocurrency, "bitcoin", "crypto mining", trojan, backdoor, "exfiltration", "credential exfiltration", "instancecredentialexfiltration", "malicious ip", "tor", "anomalous behavior", "suspicious api"]
---

# GuardDuty finding triage

A GuardDuty finding is a **lead**, not a verdict. Triage answers four questions:
what happened, to which resource, by whom, and is it expected?

Posture: read-only. Use `guardduty list-findings` / `get-findings`, CloudTrail lookups,
VPC Flow Logs and resource `describe-*` calls. Never archive findings, isolate instances,
revoke keys or change security groups — containment is proposed to a human, who approves.

## Step 1 — Decode the finding type

Format: `ThreatPurpose:ResourceTypeAffected/ThreatFamilyName.DetectionMechanism!Artifact`.

| Prefix | Meaning | Example |
|--------|---------|---------|
| `Recon` | Probing, discovery | `Recon:EC2/PortProbeUnprotectedPort` |
| `UnauthorizedAccess` | Access from unexpected source/credential misuse | `UnauthorizedAccess:IAMUser/InstanceCredentialExfiltration.OutsideAWS` |
| `CryptoCurrency` | Mining activity | `CryptoCurrency:EC2/BitcoinTool.B!DNS` |
| `Trojan` / `Backdoor` | Compromised host behaviour | `Backdoor:EC2/C&CActivity.B` |
| `Exfiltration` | Data leaving unusually | `Exfiltration:S3/AnomalousBehavior` |
| `Impact` | Resource abuse or tampering | `Impact:EC2/WinRMBruteForce` |
| `Policy` / `Stealth` / `PrivilegeEscalation` / `Discovery` / `Persistence` | Risky config or attacker tradecraft | `Stealth:IAMUser/CloudTrailLoggingDisabled` |

Severity: 7.0–8.9 High, 4.0–6.9 Medium, 1.0–3.9 Low (newer findings may also be Critical).

## Step 2 — Extract the facts from the finding JSON

- `Resource` → instance ID, access key ID / principal, bucket, EKS cluster/pod.
- `Service.Action` → API call, network connection, DNS request, port probe.
- `Service.Action.*.RemoteIpDetails` → source IP, country, ASN/org.
- `Service.Count`, `EventFirstSeen`, `EventLastSeen` → one-off or ongoing.
- `Service.Archived` and whether suppression rules already cover similar findings.

## Step 3 — Corroborate

| Finding area | Corroborating evidence |
|--------------|------------------------|
| IAM / credentials | CloudTrail events for the access key or role session: source IPs, user agents, API calls before and after; was the credential used from outside its expected environment? |
| EC2 network | VPC Flow Logs for the instance; security group exposure (see `aws-security-group-audit`); process/agent telemetry if available |
| S3 | CloudTrail data events (if enabled), bucket policy and public access settings |
| EKS / runtime | Kubernetes audit logs, the pod/image involved, its service account permissions |

## Step 4 — True or false positive?

Common benign explanations (verify, do not assume):
- Known vulnerability scanner or pentest from an approved IP range.
- Port probes on an intentionally public service (expected noise, but confirms exposure).
- CI/CD or admin tooling calling APIs from a new region or IP.
- A new legitimate integration resembling credential use "outside AWS".

Signals that it is real: activity from unexpected countries/ASNs, API calls the principal
never made before (`ListUsers`, `CreateAccessKey`, `StopLogging`), mining DNS lookups,
outbound connections to known command-and-control, findings that keep counting up.

## Step 5 — Propose containment (for human approval)

Typical proposals, ordered least to most disruptive: snapshot volumes for forensics,
restrict the instance's security group, disable/rotate the exposed access key, revoke
active role sessions, isolate or stop the instance, escalate to incident response.
State the blast radius of each step.

## Answer format

Finding type decoded in plain words, affected resource, actor and source, timeline
(first/last seen, count), corroborating evidence found and not found, verdict (likely true
positive / likely benign / undetermined) with confidence, and proposed next steps for a human.
