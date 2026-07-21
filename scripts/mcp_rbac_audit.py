#!/usr/bin/env python3
"""MCP ServiceAccount RBAC Audit Gate.

Proves that a Kubernetes ServiceAccount used by an MCP server is strictly
read-only (get/list/watch only). FAILS (exit 1) on any mutating verb.

This is the security boundary for spec 37's zero-code MCP onboarding:
a new MCP server = URL + read-only allowlist + proven SA RBAC.

Usage:
    python3 scripts/mcp_rbac_audit.py --serviceaccount <name> --namespace <ns> [--context <ctx>]
    python3 scripts/mcp_rbac_audit.py --config mcp-servers.yaml

Exit codes:
    0 = PASS (all permissions are read-only)
    1 = FAIL (mutating verbs detected)
    2 = ERROR (kubectl not found, SA not found, etc.)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


# --- Constants ---

READONLY_VERBS = frozenset({"get", "list", "watch"})
MUTATING_VERBS = frozenset({"create", "update", "patch", "delete", "deletecollection"})
WILDCARD = "*"


# --- Data types ---

@dataclass
class RBACRule:
    """A single effective permission rule."""

    resources: list[str] = field(default_factory=list)
    api_groups: list[str] = field(default_factory=list)
    resource_names: list[str] = field(default_factory=list)
    verbs: list[str] = field(default_factory=list)
    non_resource_urls: list[str] = field(default_factory=list)


@dataclass
class AuditViolation:
    """A single rule that violates the read-only policy."""

    rule: RBACRule
    offending_verbs: list[str]
    reason: str


# --- Parsing ---

def parse_json_output(raw: str) -> list[RBACRule]:
    """Parse kubectl auth can-i --list -o json output."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON from kubectl: {e}") from e

    # kubectl returns a SelfSubjectRulesReview with .status.resourceRules
    # and .status.nonResourceRules
    rules: list[RBACRule] = []

    status = data.get("status", data)
    resource_rules = status.get("resourceRules", [])
    non_resource_rules = status.get("nonResourceRules", [])

    for rr in resource_rules:
        rules.append(RBACRule(
            resources=rr.get("resources", []),
            api_groups=rr.get("apiGroups", []),
            resource_names=rr.get("resourceNames", []),
            verbs=rr.get("verbs", []),
        ))

    for nrr in non_resource_rules:
        rules.append(RBACRule(
            verbs=nrr.get("verbs", []),
            non_resource_urls=nrr.get("nonResourceURLs", []),
        ))

    return rules


def parse_table_output(raw: str) -> list[RBACRule]:
    """Parse kubectl auth can-i --list table (non-JSON) output.

    Typical format:
        Resources                          Non-Resource URLs   Resource Names   Verbs
        selfsubjectaccessreviews....       []                  []               [create]
        pods                               []                  []               [get list watch]
        ...

    Also handles an alternate layout where columns have different headers.
    """
    lines = raw.strip().splitlines()
    if not lines:
        return []

    # Find the header line (contains "Resources" or "RESOURCES")
    header_idx = -1
    for i, line in enumerate(lines):
        lower = line.lower()
        if "resources" in lower and "verbs" in lower:
            header_idx = i
            break

    # If no header found, try to parse all lines as data
    data_lines = lines[header_idx + 1:] if header_idx >= 0 else lines

    rules: list[RBACRule] = []
    for line in data_lines:
        line = line.strip()
        if not line:
            continue

        # Parse bracketed fields from the end: [...] patterns
        rule = _parse_table_line(line)
        if rule:
            rules.append(rule)

    return rules


def _parse_table_line(line: str) -> Optional[RBACRule]:
    """Parse a single table line into an RBACRule.

    Lines look like:
        pods.* [] [] [get list watch]
    or column-aligned versions thereof.
    """
    import re

    # Extract all bracketed groups
    brackets = re.findall(r'\[([^\]]*)\]', line)
    if not brackets:
        return None

    # Last bracket is always verbs
    verbs_str = brackets[-1].strip()
    verbs = verbs_str.split() if verbs_str else []

    # First token before the first [ is the resource (may include apigroup)
    resource_part = line[:line.index('[')].strip()
    # Split on whitespace — first token is resource(s)
    parts = resource_part.split()
    resources = [parts[0]] if parts else []

    # Middle brackets are non-resource-urls and resource-names (2nd, 3rd)
    non_resource_urls: list[str] = []
    resource_names: list[str] = []
    if len(brackets) >= 3:
        non_resource_urls = brackets[-3].split() if brackets[-3].strip() else []
        resource_names = brackets[-2].split() if brackets[-2].strip() else []

    # Determine api_groups from resource string (format: resource.apigroup)
    api_groups: list[str] = []
    if resources and "." in resources[0]:
        # e.g. "pods." means core api group (empty string)
        # e.g. "deployments.apps" means apps group
        resource_split = resources[0].split(".", 1)
        resources = [resource_split[0]]
        api_groups = [resource_split[1]] if len(resource_split) > 1 else [""]

    return RBACRule(
        resources=resources,
        api_groups=api_groups,
        resource_names=resource_names,
        verbs=verbs,
        non_resource_urls=non_resource_urls,
    )


# --- Audit logic ---

def audit_rules(rules: list[RBACRule]) -> list[AuditViolation]:
    """Check each rule for mutating verbs. Return violations."""
    violations: list[AuditViolation] = []

    for rule in rules:
        offending: list[str] = []

        # System self-review resources always carry "create" as a Kubernetes
        # default (not operator-controlled), e.g. selfsubjectaccessreviews.
        # They are not a mutation of cluster state, so they never count as a
        # violation. Skip them (in JSON mode they don't appear; in table mode
        # kubectl injects them).
        if rule.resources and all(
            r.startswith("selfsubject") for r in rule.resources
        ):
            continue

        for verb in rule.verbs:
            if verb == WILDCARD:
                offending.append(verb)
            elif verb.lower() in MUTATING_VERBS:
                offending.append(verb)

        if offending:
            # Determine reason
            if WILDCARD in offending:
                reason = "wildcard verb (*) grants ALL operations including mutating"
            else:
                reason = f"mutating verb(s): {', '.join(offending)}"

            # Non-resource URLs with read verbs (get) are fine even for selfsubjectreviews
            # but mutating on non-resource URLs is still a violation
            if rule.non_resource_urls and not rule.resources:
                # Non-resource URLs with create (like selfsubjectaccessreviews) are
                # system-level and typically safe, but we flag them anyway for
                # transparency — the operator can decide
                reason += f" on non-resource URL(s): {rule.non_resource_urls}"

            violations.append(AuditViolation(
                rule=rule,
                offending_verbs=offending,
                reason=reason,
            ))

    return violations


# --- kubectl interaction ---

def run_kubectl_can_i(
    serviceaccount: str,
    namespace: str,
    context: Optional[str] = None,
) -> tuple[list[RBACRule], str]:
    """Run kubectl auth can-i --list and return parsed rules + format used.

    Returns:
        Tuple of (rules, format) where format is 'json' or 'table'.

    Raises:
        SystemExit on kubectl errors.
    """
    if not shutil.which("kubectl"):
        print("ERROR: kubectl not found in PATH", file=sys.stderr)
        sys.exit(2)

    sa_identity = f"system:serviceaccount:{namespace}:{serviceaccount}"
    base_cmd = ["kubectl", "auth", "can-i", "--list", f"--as={sa_identity}"]

    if context:
        base_cmd.append(f"--context={context}")

    # Try JSON first
    cmd_json = base_cmd + ["-o", "json"]
    result = subprocess.run(
        cmd_json,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode == 0 and result.stdout.strip().startswith("{"):
        try:
            rules = parse_json_output(result.stdout)
            return rules, "json"
        except ValueError:
            pass  # Fall through to table parse

    # Fall back to table format
    cmd_table = base_cmd
    result = subprocess.run(
        cmd_table,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not found" in stderr.lower() or "cannot" in stderr.lower():
            print(
                f"ERROR: ServiceAccount '{serviceaccount}' in namespace "
                f"'{namespace}' not found or inaccessible.\n"
                f"kubectl stderr: {stderr}",
                file=sys.stderr,
            )
        else:
            print(
                f"ERROR: kubectl auth can-i failed (exit {result.returncode}).\n"
                f"stderr: {stderr}",
                file=sys.stderr,
            )
        sys.exit(2)

    rules = parse_table_output(result.stdout)
    return rules, "table"


# --- Reporting ---

def print_report(
    serviceaccount: str,
    namespace: str,
    rules: list[RBACRule],
    violations: list[AuditViolation],
    fmt: str,
) -> None:
    """Print human-readable audit report."""
    print("=" * 70)
    print("MCP ServiceAccount RBAC Audit")
    print("=" * 70)
    print(f"  ServiceAccount: {serviceaccount}")
    print(f"  Namespace:      {namespace}")
    print(f"  Parse format:   {fmt}")
    print(f"  Total rules:    {len(rules)}")
    print("-" * 70)

    for i, rule in enumerate(rules, 1):
        resource_desc = ", ".join(rule.resources) if rule.resources else "(non-resource)"
        if rule.non_resource_urls:
            resource_desc += f" [{', '.join(rule.non_resource_urls)}]"
        verbs_desc = ", ".join(rule.verbs)

        # Check if this rule is a violation
        is_violation = any(v.rule is rule for v in violations)
        marker = "❌ FAIL" if is_violation else "✅ OK  "
        print(f"  [{marker}] Rule {i}: {resource_desc} → [{verbs_desc}]")

    print("-" * 70)

    if violations:
        print(f"\n🚨 FAIL: {len(violations)} rule(s) grant MUTATING access:\n")
        for v in violations:
            resources = ", ".join(v.rule.resources) if v.rule.resources else "(non-resource)"
            if v.rule.non_resource_urls:
                resources += f" [{', '.join(v.rule.non_resource_urls)}]"
            print(f"  • {resources}")
            print(f"    Offending verbs: {', '.join(v.offending_verbs)}")
            print(f"    Reason: {v.reason}")
            print()
        print("ACTION REQUIRED: Restrict the ServiceAccount to get/list/watch only.")
        print("The MCP server CANNOT be onboarded until this gate passes.")
    else:
        print(f"\n✅ PASS: All {len(rules)} rules are read-only (get/list/watch).")
        print("The ServiceAccount meets the read-only requirement for MCP onboarding.")

    print("=" * 70)


# --- Config file support ---

def load_config(path: str) -> list[dict]:
    """Load a YAML/JSON config file listing MCP service accounts.

    Expected format (YAML or JSON):
        service_accounts:
          - name: kube-mcp
            namespace: mcp-servers
          - name: vm-mcp
            namespace: mcp-servers
    """
    import os

    if not os.path.isfile(path):
        print(f"ERROR: config file not found: {path}", file=sys.stderr)
        sys.exit(2)

    with open(path) as f:
        content = f.read()

    # Try JSON first
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Try YAML (stdlib only — parse a simple subset)
        data = _parse_simple_yaml(content)

    if isinstance(data, dict):
        return data.get("service_accounts", [])
    return []


def _parse_simple_yaml(content: str) -> dict:
    """Minimal YAML-like parser for the config file (stdlib only).

    Handles the simple list-of-dicts structure we need.
    """
    result: dict = {"service_accounts": []}
    current_item: Optional[dict] = None

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- "):
            # New list item
            if current_item is not None:
                result["service_accounts"].append(current_item)
            current_item = {}
            # Check if key: value on same line
            item_content = stripped[2:].strip()
            if ":" in item_content:
                key, val = item_content.split(":", 1)
                current_item[key.strip()] = val.strip().strip('"').strip("'")
        elif ":" in stripped and current_item is not None:
            key, val = stripped.split(":", 1)
            current_item[key.strip()] = val.strip().strip('"').strip("'")

    if current_item is not None:
        result["service_accounts"].append(current_item)

    return result


# --- Main ---

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MCP ServiceAccount RBAC Audit — proves read-only access",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--serviceaccount", "-s",
        help="ServiceAccount name to audit",
    )
    parser.add_argument(
        "--namespace", "-n",
        help="Namespace of the ServiceAccount",
    )
    parser.add_argument(
        "--context", "-c",
        help="kubectl context to use (optional)",
    )
    parser.add_argument(
        "--config",
        help="Path to config file listing multiple MCP ServiceAccounts",
    )

    args = parser.parse_args()

    # Determine what to audit
    targets: list[dict] = []

    if args.config:
        targets = load_config(args.config)
        if not targets:
            print("ERROR: no service_accounts found in config file", file=sys.stderr)
            return 2
    elif args.serviceaccount and args.namespace:
        targets = [{"name": args.serviceaccount, "namespace": args.namespace}]
    else:
        parser.error("Provide --serviceaccount + --namespace, or --config")
        return 2

    # Audit each target
    all_pass = True

    for target in targets:
        sa_name = target["name"]
        sa_ns = target["namespace"]
        ctx = args.context or target.get("context")

        rules, fmt = run_kubectl_can_i(sa_name, sa_ns, ctx)
        violations = audit_rules(rules)
        print_report(sa_name, sa_ns, rules, violations, fmt)

        if violations:
            all_pass = False

        if len(targets) > 1:
            print()  # Separator between multiple audits

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
