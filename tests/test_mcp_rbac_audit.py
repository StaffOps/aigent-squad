"""Unit tests for scripts/mcp_rbac_audit.py parser and audit logic.

Tests exercise the PARSER with fixture strings — no live cluster required.
Validates that the read-only security boundary (spec 37) correctly identifies
mutating verbs and rejects them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add scripts/ to path so we can import the module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from mcp_rbac_audit import (
    MUTATING_VERBS,
    READONLY_VERBS,
    AuditViolation,
    RBACRule,
    audit_rules,
    parse_json_output,
    parse_table_output,
)


# --- Fixture: typical kubectl auth can-i --list table output ---

TABLE_READ_ONLY = """\
Resources                                       Non-Resource URLs   Resource Names   Verbs
selfsubjectreviews.authentication.k8s.io        []                  []               [create]
selfsubjectaccessreviews.authorization.k8s.io   []                  []               [create]
selfsubjectrulesreviews.authorization.k8s.io    []                  []               [create]
pods                                            []                  []               [get list watch]
services                                        []                  []               [get list watch]
deployments.apps                                []                  []               [get list watch]
events                                          []                  []               [get list watch]
nodes                                           []                  []               [get list watch]
"""

TABLE_WITH_MUTATION = """\
Resources                                       Non-Resource URLs   Resource Names   Verbs
selfsubjectreviews.authentication.k8s.io        []                  []               [create]
selfsubjectaccessreviews.authorization.k8s.io   []                  []               [create]
selfsubjectrulesreviews.authorization.k8s.io    []                  []               [create]
pods                                            []                  []               [get list watch]
configmaps                                      []                  []               [get list watch create update]
secrets                                         []                  []               [get list watch delete]
"""

TABLE_WILDCARD_VERB = """\
Resources                                       Non-Resource URLs   Resource Names   Verbs
pods                                            []                  []               [*]
"""

TABLE_WILDCARD_RESOURCE_READONLY = """\
Resources                                       Non-Resource URLs   Resource Names   Verbs
*.*                                             []                  []               [get list watch]
"""

TABLE_EMPTY_AFTER_HEADER = """\
Resources                                       Non-Resource URLs   Resource Names   Verbs
"""

TABLE_EMPTY = ""

TABLE_HEADER_ONLY_WHITESPACE = """\
Resources                                       Non-Resource URLs   Resource Names   Verbs

"""

# --- Fixture: JSON output (SelfSubjectRulesReview) ---

JSON_READ_ONLY = json.dumps({
    "apiVersion": "authorization.k8s.io/v1",
    "kind": "SelfSubjectRulesReview",
    "status": {
        "resourceRules": [
            {"resources": ["pods"], "apiGroups": [""], "verbs": ["get", "list", "watch"]},
            {"resources": ["services"], "apiGroups": [""], "verbs": ["get", "list", "watch"]},
            {"resources": ["deployments"], "apiGroups": ["apps"], "verbs": ["get", "list", "watch"]},
        ],
        "nonResourceRules": [
            {"verbs": ["get"], "nonResourceURLs": ["/healthz", "/version"]},
        ],
        "incomplete": False,
    },
})

JSON_WITH_MUTATION = json.dumps({
    "apiVersion": "authorization.k8s.io/v1",
    "kind": "SelfSubjectRulesReview",
    "status": {
        "resourceRules": [
            {"resources": ["pods"], "apiGroups": [""], "verbs": ["get", "list", "watch"]},
            {"resources": ["configmaps"], "apiGroups": [""], "verbs": ["get", "list", "watch", "create", "patch"]},
            {"resources": ["secrets"], "apiGroups": [""], "verbs": ["get", "list", "watch", "delete"]},
        ],
        "nonResourceRules": [],
        "incomplete": False,
    },
})

JSON_WILDCARD_VERB = json.dumps({
    "apiVersion": "authorization.k8s.io/v1",
    "kind": "SelfSubjectRulesReview",
    "status": {
        "resourceRules": [
            {"resources": ["*"], "apiGroups": ["*"], "verbs": ["*"]},
        ],
        "nonResourceRules": [],
        "incomplete": False,
    },
})

JSON_NON_RESOURCE_CREATE = json.dumps({
    "apiVersion": "authorization.k8s.io/v1",
    "kind": "SelfSubjectRulesReview",
    "status": {
        "resourceRules": [
            {"resources": ["pods"], "apiGroups": [""], "verbs": ["get", "list", "watch"]},
        ],
        "nonResourceRules": [
            {"verbs": ["get"], "nonResourceURLs": ["/healthz"]},
            {"verbs": ["create"], "nonResourceURLs": ["/api/v1/selfsubjectaccessreviews"]},
        ],
        "incomplete": False,
    },
})


# =============================================================================
# Table parser tests
# =============================================================================

class TestParseTableOutput:
    """Test parse_table_output with various fixture strings."""

    def test_read_only_table_parses_all_rules(self):
        rules = parse_table_output(TABLE_READ_ONLY)
        # selfsubjectreviews (3) + pods + services + deployments + events + nodes = 8
        assert len(rules) == 8

    def test_read_only_table_resources_correct(self):
        rules = parse_table_output(TABLE_READ_ONLY)
        # Find pods rule
        pod_rules = [r for r in rules if "pods" in r.resources]
        assert len(pod_rules) == 1
        assert set(pod_rules[0].verbs) == {"get", "list", "watch"}

    def test_table_with_mutation_detected(self):
        rules = parse_table_output(TABLE_WITH_MUTATION)
        # configmaps has create,update; secrets has delete
        cm_rules = [r for r in rules if "configmaps" in r.resources]
        assert len(cm_rules) == 1
        assert "create" in cm_rules[0].verbs
        assert "update" in cm_rules[0].verbs

    def test_wildcard_verb_parsed(self):
        rules = parse_table_output(TABLE_WILDCARD_VERB)
        assert len(rules) == 1
        assert "*" in rules[0].verbs

    def test_wildcard_resource_with_readonly_verbs(self):
        rules = parse_table_output(TABLE_WILDCARD_RESOURCE_READONLY)
        assert len(rules) == 1
        # Resource is *.* which splits to resource=* apigroup=*
        assert set(rules[0].verbs) == {"get", "list", "watch"}

    def test_empty_output_returns_empty(self):
        rules = parse_table_output(TABLE_EMPTY)
        assert rules == []

    def test_header_only_returns_empty(self):
        rules = parse_table_output(TABLE_EMPTY_AFTER_HEADER)
        assert rules == []

    def test_header_with_trailing_whitespace_returns_empty(self):
        rules = parse_table_output(TABLE_HEADER_ONLY_WHITESPACE)
        assert rules == []

    def test_deployments_apigroup_parsed(self):
        rules = parse_table_output(TABLE_READ_ONLY)
        deploy_rules = [r for r in rules if "deployments" in r.resources]
        assert len(deploy_rules) == 1
        assert "apps" in deploy_rules[0].api_groups


# =============================================================================
# JSON parser tests
# =============================================================================

class TestParseJsonOutput:
    """Test parse_json_output with SelfSubjectRulesReview fixtures."""

    def test_read_only_json(self):
        rules = parse_json_output(JSON_READ_ONLY)
        # 3 resource rules + 1 non-resource rule
        assert len(rules) == 4

    def test_read_only_json_pods(self):
        rules = parse_json_output(JSON_READ_ONLY)
        pod_rules = [r for r in rules if "pods" in r.resources]
        assert len(pod_rules) == 1
        assert set(pod_rules[0].verbs) == {"get", "list", "watch"}

    def test_mutation_json(self):
        rules = parse_json_output(JSON_WITH_MUTATION)
        cm_rules = [r for r in rules if "configmaps" in r.resources]
        assert len(cm_rules) == 1
        assert "create" in cm_rules[0].verbs
        assert "patch" in cm_rules[0].verbs

    def test_wildcard_json(self):
        rules = parse_json_output(JSON_WILDCARD_VERB)
        assert len(rules) == 1
        assert "*" in rules[0].verbs
        assert "*" in rules[0].resources

    def test_non_resource_urls_parsed(self):
        rules = parse_json_output(JSON_READ_ONLY)
        nr_rules = [r for r in rules if r.non_resource_urls]
        assert len(nr_rules) == 1
        assert "/healthz" in nr_rules[0].non_resource_urls

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_json_output("not json at all {{{")

    def test_empty_json_object(self):
        rules = parse_json_output("{}")
        assert rules == []


# =============================================================================
# Audit logic tests
# =============================================================================

class TestAuditRules:
    """Test the audit_rules function — core security boundary logic."""

    def test_readonly_rules_pass(self):
        rules = [
            RBACRule(resources=["pods"], verbs=["get", "list", "watch"]),
            RBACRule(resources=["services"], verbs=["get", "list", "watch"]),
            RBACRule(resources=["deployments"], verbs=["get", "list"]),
        ]
        violations = audit_rules(rules)
        assert violations == []

    def test_create_verb_fails(self):
        rules = [
            RBACRule(resources=["pods"], verbs=["get", "list", "watch"]),
            RBACRule(resources=["configmaps"], verbs=["get", "list", "watch", "create"]),
        ]
        violations = audit_rules(rules)
        assert len(violations) == 1
        assert "create" in violations[0].offending_verbs
        assert "configmaps" in violations[0].rule.resources

    def test_update_verb_fails(self):
        rules = [RBACRule(resources=["pods"], verbs=["get", "update"])]
        violations = audit_rules(rules)
        assert len(violations) == 1
        assert "update" in violations[0].offending_verbs

    def test_patch_verb_fails(self):
        rules = [RBACRule(resources=["pods"], verbs=["get", "patch"])]
        violations = audit_rules(rules)
        assert len(violations) == 1
        assert "patch" in violations[0].offending_verbs

    def test_delete_verb_fails(self):
        rules = [RBACRule(resources=["secrets"], verbs=["get", "delete"])]
        violations = audit_rules(rules)
        assert len(violations) == 1
        assert "delete" in violations[0].offending_verbs

    def test_deletecollection_verb_fails(self):
        rules = [RBACRule(resources=["pods"], verbs=["get", "deletecollection"])]
        violations = audit_rules(rules)
        assert len(violations) == 1
        assert "deletecollection" in violations[0].offending_verbs

    def test_wildcard_verb_fails(self):
        rules = [RBACRule(resources=["*"], verbs=["*"])]
        violations = audit_rules(rules)
        assert len(violations) == 1
        assert "*" in violations[0].offending_verbs
        assert "wildcard" in violations[0].reason.lower()

    def test_wildcard_resource_with_readonly_verbs_passes(self):
        """A wildcard RESOURCE (e.g. `*`) with only get/list/watch is OK."""
        rules = [RBACRule(resources=["*"], verbs=["get", "list", "watch"])]
        violations = audit_rules(rules)
        assert violations == []

    def test_multiple_violations_all_reported(self):
        rules = [
            RBACRule(resources=["pods"], verbs=["get", "create"]),
            RBACRule(resources=["secrets"], verbs=["delete"]),
            RBACRule(resources=["configmaps"], verbs=["get", "list", "watch"]),
        ]
        violations = audit_rules(rules)
        assert len(violations) == 2

    def test_non_resource_url_create_is_flagged(self):
        """Even non-resource URLs with mutating verbs should be flagged."""
        rules = [
            RBACRule(
                non_resource_urls=["/api/v1/something"],
                verbs=["create"],
            ),
        ]
        violations = audit_rules(rules)
        assert len(violations) == 1
        assert "non-resource URL" in violations[0].reason

    def test_non_resource_url_get_passes(self):
        rules = [
            RBACRule(
                non_resource_urls=["/healthz", "/version"],
                verbs=["get"],
            ),
        ]
        violations = audit_rules(rules)
        assert violations == []

    def test_empty_rules_passes(self):
        violations = audit_rules([])
        assert violations == []

    def test_empty_verbs_passes(self):
        rules = [RBACRule(resources=["pods"], verbs=[])]
        violations = audit_rules(rules)
        assert violations == []

    def test_all_mutating_verbs_covered(self):
        """Every verb in MUTATING_VERBS is caught by audit."""
        for verb in MUTATING_VERBS:
            rules = [RBACRule(resources=["test"], verbs=["get", verb])]
            violations = audit_rules(rules)
            assert len(violations) == 1, f"Failed to catch verb: {verb}"
            assert verb in violations[0].offending_verbs

    def test_readonly_verbs_never_flagged(self):
        """No verb in READONLY_VERBS triggers a violation."""
        for verb in READONLY_VERBS:
            rules = [RBACRule(resources=["test"], verbs=[verb])]
            violations = audit_rules(rules)
            assert violations == [], f"False positive for verb: {verb}"


# =============================================================================
# Integration: parse → audit pipeline
# =============================================================================

class TestParseAndAudit:
    """End-to-end: parse fixture output → run audit → verify result."""

    def test_table_read_only_passes_audit(self):
        rules = parse_table_output(TABLE_READ_ONLY)
        # selfsubjectreviews carry a system-default "create" but are skipped by
        # the audit (not operator-controlled, not a cluster-state mutation), so
        # a genuinely read-only SA passes cleanly even in table mode.
        violations = audit_rules(rules)
        assert violations == []

    def test_table_with_mutation_fails_audit(self):
        rules = parse_table_output(TABLE_WITH_MUTATION)
        violations = audit_rules(rules)
        # selfsubjectreviews skipped; the real mutations remain:
        # configmaps (create,update) + secrets (delete) = 2 violations.
        assert len(violations) == 2
        # Verify configmaps and secrets are among violations
        violation_resources = [
            r for v in violations for r in v.rule.resources
        ]
        assert "configmaps" in violation_resources
        assert "secrets" in violation_resources

    def test_json_read_only_passes_audit(self):
        rules = parse_json_output(JSON_READ_ONLY)
        violations = audit_rules(rules)
        # Pure read-only (no selfsubjectreviews in this fixture)
        assert violations == []

    def test_json_with_mutation_fails_audit(self):
        rules = parse_json_output(JSON_WITH_MUTATION)
        violations = audit_rules(rules)
        assert len(violations) == 2  # configmaps + secrets

    def test_json_wildcard_verb_fails_audit(self):
        rules = parse_json_output(JSON_WILDCARD_VERB)
        violations = audit_rules(rules)
        assert len(violations) == 1
        assert "*" in violations[0].offending_verbs

    def test_json_non_resource_create_flagged(self):
        rules = parse_json_output(JSON_NON_RESOURCE_CREATE)
        violations = audit_rules(rules)
        # The non-resource URL with "create" is flagged
        assert len(violations) == 1
        assert "create" in violations[0].offending_verbs
