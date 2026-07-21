#!/usr/bin/env python3
"""Migrate pertinent org skills into the aigent-squad skill registry.

Source skills (platform-agents-definition) carry only `name` + `description`
frontmatter. The squad's SkillRegistry selects by `keywords` and IGNORES a skill
with no keywords. So migration = copy the body verbatim + inject a generated
`keywords:` line derived from the skill name + notable terms in the description.

Idempotent: skips a target skill that already exists (preserves hand-tuned ones,
e.g. the 3 P0 catalogs). Run via Docker python:3.11-slim.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

SRC = Path("/src/platform-agents-definition")
DST = Path("/src/1-agentic/aigent-squad/skills")

# Pertinent categories (runtime ops knowledge). Skip development/documentation/
# workflows/projects — those are for BUILDING the agent, not answering ops.
PERTINENT = [
    SRC / "skills/apm-metrics",
    SRC / "skills/observability",
    SRC / "skills/sre",
    SRC / "skills/aws",
    SRC / "skills/finops",
    SRC / "skills/infrastructure",
    SRC / "skills/security",
    SRC / "overlays/bdc/skills",  # recurse: has subcategories
]

# name-suffixes that are generic (drop from keyword derivation)
DROP = {"metrics", "patterns", "config", "configuration", "strategy", "framework",
        "templates", "template", "integration", "management", "mgmt", "self",
        "deep", "cross", "runtime", "reference", "overview", "standard", "helper",
        "bdc", "apm"}
# high-signal acronyms/components to keep if present in the description
KEEP_TERMS = {
    "vminsert","vmselect","vmstorage","vmagent","vmalert","victoriametrics","prometheus",
    "loki","logql","tempo","traceql","pyroscope","grafana","otel","opentelemetry","collector",
    "karpenter","keda","kyverno","velero","cilium","istio","ambient","kiali","argocd","argo",
    "rollout","kafka","strimzi","harbor","nexus","sonarqube","backstage","crossplane","opensearch",
    "kubecost","keycloak","cert-manager","external-secrets","external-dns","reloader","descheduler",
    "ingress","traefik","nginx","redis","postgres","coredns","dns","eks","iam","rds","lambda",
    "route53","cloudfront","s3","cloudwatch","cost","savings","ec2","apko","melange","cosign",
    "trivy","sbom","cve","dependency-track","defectdojo","helmfile","terraform","oomkill","oom",
    "slo","sli","rca","incident","alert","alertmanager","gc","jit","threadpool","dotnet","golang",
    "go","python","nodejs","node","gitlab","pipeline","mtls","csi","ebs","efs","hpa","pvc",
}
FRONT_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# noise keywords to drop (emphatic English ALLCAPS + over-generic tech tokens)
NOISE = {"not", "is", "be", "no", "may", "can", "are", "the", "for", "use", "all",
         "and", "or", "to", "of", "in", "on", "red", "yellow", "new", "old", "raw",
         "per", "via", "api", "http", "https", "yes", "with", "was", "has", "its"}


def derive_keywords(name: str, desc: str) -> list[str]:
    kws: list[str] = []
    parts = [p for p in name.split("-") if p and p not in DROP and len(p) >= 2]
    # full de-suffixed compound (e.g. 'aws-load-balancer-controller')
    if parts:
        kws.append("-".join(parts))
    kws.extend(parts)
    # a couple of adjacent 2-word phrases from the name
    for i in range(len(parts) - 1):
        kws.append(f"{parts[i]} {parts[i+1]}")
    low = desc.lower()
    toks = set(re.findall(r"[a-z0-9_\-]+", low))
    for t in sorted(toks):
        if t in KEEP_TERMS:
            kws.append(t)
    # ALLCAPS acronyms (GC, JIT, SLO, IAM, EKS, mTLS...)
    for a in re.findall(r"\b[A-Z]{2,6}\b", desc):
        kws.append(a.lower())
    # dedupe, keep order, cap
    seen, out = set(), []
    for k in kws:
        k = k.strip().lower()
        if k and k not in seen and k not in DROP and k not in NOISE:
            seen.add(k); out.append(k)
    return out[:18]


def main() -> int:
    skill_files = []
    for base in PERTINENT:
        skill_files += list(base.rglob("SKILL.md"))
    migrated, skipped, agent_map = [], [], {}
    for f in sorted(skill_files):
        name = f.parent.name
        target = DST / name / "SKILL.md"
        m = FRONT_RE.match(f.read_text(encoding="utf-8"))
        if not m:
            print(f"  !! no frontmatter: {f}"); continue
        fm, body = m.group(1), m.group(2)
        nm = re.search(r"^name:\s*(.+)$", fm, re.M)
        dm = re.search(r"^description:\s*(.+)$", fm, re.M | re.S)
        sk_name = (nm.group(1).strip() if nm else name)
        desc = (dm.group(1).strip().replace("\n", " ") if dm else "")[:600]
        if target.exists():
            skipped.append(name); continue
        kws = derive_keywords(name, desc)
        target.parent.mkdir(parents=True, exist_ok=True)
        kw_yaml = "[" + ", ".join(f'"{k}"' if (" " in k) else k for k in kws) + "]"
        new_fm = f"name: {sk_name}\ndescription: {desc}\nkeywords: {kw_yaml}"
        target.write_text(f"---\n{new_fm}\n---\n{body}", encoding="utf-8")
        migrated.append((name, kws))
    print(f"MIGRATED {len(migrated)} | SKIPPED (exists) {len(skipped)}")
    for n, kws in migrated:
        print(f"  + {n}: {', '.join(kws[:8])}...")
    if skipped:
        print("SKIPPED:", ", ".join(skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
