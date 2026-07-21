#!/usr/bin/env python3
"""Wire migrated skills into each agent's `skills:` allowlist by domain.

Selection at runtime is lazy + keyword-gated + capped (MAX_SKILLS_PER_TURN), so a
generous per-agent allowlist is safe: only keyword-matching skills load, ≤3/turn.
Overlaps across agents are intentional (a skill can help multiple domains).

Rewrites each agents/<agent>/agent.yaml: strips any existing top-level `skills:`
block and appends a fresh one. Comments elsewhere are preserved.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import os
SKILLS = Path(os.environ.get("SKILLS_DIR", str(ROOT / "skills")))
AGENTS = Path(os.environ.get("AGENTS_DIR", str(ROOT / "agents")))

# substring rules (skill name contains any) — overlaps allowed
RULES = {
    "observability": ["-metrics", "loki", "tempo", "pyroscope", "otel", "kubelet",
                       "multicluster", "streaming-agg", "alertmanager", "fluent-bit",
                       "vmalert", "vm-cardinality", "grafana-cross", "oomkill",
                       "alerting-strategy", "error-budget", "incident-response",
                       "post-mortem", "root-cause", "runbook", "sla-slo"],
    "kubernetes": ["k8s-workload", "karpenter", "keda", "cert-manager",
                   "external-secrets", "external-dns", "reloader", "kyverno-metrics",
                   "descheduler", "k8s-pvc", "aws-csi", "aws-load-balancer",
                   "kubescape", "scaleops", "metrics-server", "istio", "ingress-nginx",
                   "traefik", "backing-services", "helmfile", "oomkill"],
    "devops": ["argo", "gitlab-runner", "harbor", "nexus3", "sonarqube", "backstage",
               "crossplane", "strimzi-kafka", "opensearch", "datahub", "superset",
               "cosign", "helm-chart", "applicationset", "kyverno-bdc", "terraform",
               "monitoring-stack", "gitops-environment", "pipeline-template",
               "bdc-telemetry", "external-secrets-aws"],
    "aws": ["cloudfront", "cost-explorer", "eks-management", "iam-patterns",
            "lambda", "rds-patterns", "route53", "security-hub-patterns",
            "aws-csi", "aws-load-balancer"],
    "finops": ["ec2-rightsizing", "savings-plans", "untagged-resources",
               "kubecost", "cost-explorer"],
    "security": ["aws-ftr", "container-image-apko", "container-package-melange",
                 "dependency-track", "golden-ami", "sbom", "security-hub-findings",
                 "kubescape", "defectdojo", "harbor", "keycloak", "cosign"],
}
SKILLS_BLOCK_RE = re.compile(r"(?m)^skills:\n(?:[ \t]+-[ \t].*\n)*")


def main() -> int:
    names = sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").exists())
    for agent, subs in RULES.items():
        f = AGENTS / agent / "agent.yaml"
        if not f.exists():
            print(f"  !! no agent.yaml for {agent}"); continue
        sel = sorted({n for n in names if any(s in n for s in subs)})
        text = SKILLS_BLOCK_RE.sub("", f.read_text(encoding="utf-8")).rstrip() + "\n"
        block = "skills:\n" + "".join(f"  - {n}\n" for n in sel)
        f.write_text(text + block, encoding="utf-8")
        print(f"  {agent}: {len(sel)} skills")
    return 0


if __name__ == "__main__":
    sys.exit(main())
