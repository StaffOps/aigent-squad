"""Independent tests for scripts/mcp_rbac_audit.py — subprocess integration layer.

Tests the end-to-end flow from CLI invocation through subprocess mocking
to exit code validation. This file is independent from the existing
test_mcp_rbac_audit.py which tests parser/audit logic only.

Coverage targets:
  (1) read-only SA → PASS (exit 0)
  (2) create pods → FAIL (exit 1) naming the verb
  (3) delete/patch/update/deletecollection → FAIL
  (4) wildcard verb * → FAIL
  (5) wildcard resource * with get/list/watch only → PASS
  (6) kubectl-not-found / SA-not-found → exit 2, no crash, no false PASS
  (7) header-only / empty output → handled gracefully
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts/ to path so we can import the module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from mcp_rbac_audit import (
    audit_rules,
    main,
    parse_json_output,
    parse_table_output,
    run_kubectl_can_i,
)


# =============================================================================
# Fixture: JSON outputs from kubectl auth can-i --list -o json
# =============================================================================

def _make_json_output(resource_rules: list[dict], non_resource_rules: list[dict] | None = None) -> str:
    """Build a SelfSubjectRulesReview JSON response."""
    return json.dumps({
        "apiVersion": "authorization.k8s.io/v1",
        "kind": "SelfSubjectRulesReview",
        "status": {
            "resourceRules": resource_rules,
            "nonResourceRules": non_resource_rules or [],
            "incomplete": False,
        },
    })


JSON_READONLY_SA = _make_json_output([
    {"resources": ["pods"], "apiGroups": [""], "verbs": ["get", "list", "watch"]},
    {"resources": ["services"], "apiGroups": [""], "verbs": ["get", "list", "watch"]},
    {"resources": ["deployments"], "apiGroups": ["apps"], "verbs": ["get", "list", "watch"]},
    {"resources": ["nodes"], "apiGroups": [""], "verbs": ["get", "list", "watch"]},
], [
    {"verbs": ["get"], "nonResourceURLs": ["/healthz", "/version"]},
])

JSON_CREATE_PODS = _make_json_output([
    {"resources": ["pods"], "apiGroups": [""], "verbs": ["get", "list", "watch", "create"]},
    {"resources": ["services"], "apiGroups": [""], "verbs": ["get", "list", "watch"]},
])

JSON_DELETE_PATCH_UPDATE = _make_json_output([
    {"resources": ["pods"], "apiGroups": [""], "verbs": ["get", "list", "watch"]},
    {"resources": ["configmaps"], "apiGroups": [""], "verbs": ["get", "delete"]},
    {"resources": ["deployments"], "apiGroups": ["apps"], "verbs": ["get", "patch"]},
    {"resources": ["secrets"], "apiGroups": [""], "verbs": ["get", "update"]},
])

JSON_DELETECOLLECTION = _make_json_output([
    {"resources": ["pods"], "apiGroups": [""], "verbs": ["get", "list", "watch", "deletecollection"]},
])

JSON_WILDCARD_VERB = _make_json_output([
    {"resources": ["*"], "apiGroups": ["*"], "verbs": ["*"]},
])

JSON_WILDCARD_RESOURCE_READONLY = _make_json_output([
    {"resources": ["*"], "apiGroups": ["*"], "verbs": ["get", "list", "watch"]},
])


# =============================================================================
# Fixture: table outputs
# =============================================================================

TABLE_READONLY_SA = """\
Resources                                       Non-Resource URLs   Resource Names   Verbs
pods                                            []                  []               [get list watch]
services                                        []                  []               [get list watch]
deployments.apps                                []                  []               [get list watch]
nodes                                           []                  []               [get list watch]
"""

TABLE_HEADER_ONLY = """\
Resources                                       Non-Resource URLs   Resource Names   Verbs
"""

TABLE_COMPLETELY_EMPTY = ""


# =============================================================================
# Helpers
# =============================================================================

def _mock_subprocess_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Create a mock subprocess.CompletedProcess."""
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


# =============================================================================
# Case 1: read-only SA (get/list/watch only) → PASS (exit 0)
# =============================================================================

class TestReadOnlySAPasses:
    """Scenario: SA has only get/list/watch → exit code 0."""

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_json_readonly_sa_returns_exit_0(self, mock_which, mock_run):
        """Full read-only SA via JSON output → main() exits 0."""
        mock_run.return_value = _mock_subprocess_result(stdout=JSON_READONLY_SA)

        with patch("sys.argv", ["mcp_rbac_audit.py", "-s", "kube-mcp", "-n", "mcp-servers"]):
            exit_code = main()

        assert exit_code == 0

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_run_kubectl_can_i_readonly_returns_rules(self, mock_which, mock_run):
        """run_kubectl_can_i parses read-only JSON correctly."""
        mock_run.return_value = _mock_subprocess_result(stdout=JSON_READONLY_SA)

        rules, fmt = run_kubectl_can_i("kube-mcp", "mcp-servers")

        assert fmt == "json"
        assert len(rules) > 0
        violations = audit_rules(rules)
        assert violations == []

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_table_fallback_readonly_returns_exit_0(self, mock_which, mock_run):
        """When JSON fails, falls back to table; read-only → exit 0."""
        # First call (JSON) fails, second call (table) succeeds
        mock_run.side_effect = [
            _mock_subprocess_result(stdout="error: not json", returncode=1),
            _mock_subprocess_result(stdout=TABLE_READONLY_SA),
        ]

        with patch("sys.argv", ["mcp_rbac_audit.py", "-s", "kube-mcp", "-n", "mcp-servers"]):
            exit_code = main()

        assert exit_code == 0


# =============================================================================
# Case 2: create pods → FAIL (exit 1) naming the verb
# =============================================================================

class TestCreatePodsFails:
    """Scenario: SA can `create pods` → exit 1, violation names `create`."""

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_create_pods_exit_1(self, mock_which, mock_run):
        mock_run.return_value = _mock_subprocess_result(stdout=JSON_CREATE_PODS)

        with patch("sys.argv", ["mcp_rbac_audit.py", "-s", "bad-sa", "-n", "mcp-servers"]):
            exit_code = main()

        assert exit_code == 1

    def test_create_verb_named_in_violation(self):
        """audit_rules names 'create' specifically in the violation."""
        rules = parse_json_output(JSON_CREATE_PODS)
        violations = audit_rules(rules)

        assert len(violations) == 1
        assert "create" in violations[0].offending_verbs
        assert "pods" in violations[0].rule.resources

    def test_violation_reason_mentions_mutating(self):
        rules = parse_json_output(JSON_CREATE_PODS)
        violations = audit_rules(rules)

        assert "mutating" in violations[0].reason.lower() or "create" in violations[0].reason


# =============================================================================
# Case 3: delete/patch/update/deletecollection → FAIL
# =============================================================================

class TestMutatingVerbsFail:
    """All mutating verbs detected and reported."""

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_delete_patch_update_exit_1(self, mock_which, mock_run):
        mock_run.return_value = _mock_subprocess_result(stdout=JSON_DELETE_PATCH_UPDATE)

        with patch("sys.argv", ["mcp_rbac_audit.py", "-s", "bad-sa", "-n", "mcp-servers"]):
            exit_code = main()

        assert exit_code == 1

    def test_delete_detected(self):
        rules = parse_json_output(JSON_DELETE_PATCH_UPDATE)
        violations = audit_rules(rules)
        offending = {v for viol in violations for v in viol.offending_verbs}
        assert "delete" in offending

    def test_patch_detected(self):
        rules = parse_json_output(JSON_DELETE_PATCH_UPDATE)
        violations = audit_rules(rules)
        offending = {v for viol in violations for v in viol.offending_verbs}
        assert "patch" in offending

    def test_update_detected(self):
        rules = parse_json_output(JSON_DELETE_PATCH_UPDATE)
        violations = audit_rules(rules)
        offending = {v for viol in violations for v in viol.offending_verbs}
        assert "update" in offending

    def test_deletecollection_detected(self):
        rules = parse_json_output(JSON_DELETECOLLECTION)
        violations = audit_rules(rules)
        assert len(violations) == 1
        assert "deletecollection" in violations[0].offending_verbs

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_deletecollection_exit_1(self, mock_which, mock_run):
        mock_run.return_value = _mock_subprocess_result(stdout=JSON_DELETECOLLECTION)

        with patch("sys.argv", ["mcp_rbac_audit.py", "-s", "bad-sa", "-n", "default"]):
            exit_code = main()

        assert exit_code == 1

    def test_each_mutating_verb_independently_flagged(self):
        """Each of delete, patch, update, deletecollection produces its own violation."""
        rules = parse_json_output(JSON_DELETE_PATCH_UPDATE)
        violations = audit_rules(rules)
        # 3 rules with one mutating verb each
        assert len(violations) == 3
        offending_per_rule = [v.offending_verbs for v in violations]
        assert ["delete"] in offending_per_rule
        assert ["patch"] in offending_per_rule
        assert ["update"] in offending_per_rule


# =============================================================================
# Case 4: wildcard verb * → FAIL
# =============================================================================

class TestWildcardVerbFails:
    """Wildcard verb (*) grants all operations → must fail."""

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_wildcard_verb_exit_1(self, mock_which, mock_run):
        mock_run.return_value = _mock_subprocess_result(stdout=JSON_WILDCARD_VERB)

        with patch("sys.argv", ["mcp_rbac_audit.py", "-s", "admin-sa", "-n", "kube-system"]):
            exit_code = main()

        assert exit_code == 1

    def test_wildcard_verb_named_in_violation(self):
        rules = parse_json_output(JSON_WILDCARD_VERB)
        violations = audit_rules(rules)

        assert len(violations) == 1
        assert "*" in violations[0].offending_verbs

    def test_wildcard_reason_mentions_wildcard(self):
        rules = parse_json_output(JSON_WILDCARD_VERB)
        violations = audit_rules(rules)

        assert "wildcard" in violations[0].reason.lower()


# =============================================================================
# Case 5: wildcard resource * with only get/list/watch → PASS
# =============================================================================

class TestWildcardResourceReadonlyPasses:
    """Wildcard resource with read-only verbs is acceptable."""

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_wildcard_resource_readonly_exit_0(self, mock_which, mock_run):
        mock_run.return_value = _mock_subprocess_result(stdout=JSON_WILDCARD_RESOURCE_READONLY)

        with patch("sys.argv", ["mcp_rbac_audit.py", "-s", "reader-sa", "-n", "mcp-servers"]):
            exit_code = main()

        assert exit_code == 0

    def test_wildcard_resource_readonly_no_violations(self):
        rules = parse_json_output(JSON_WILDCARD_RESOURCE_READONLY)
        violations = audit_rules(rules)

        assert violations == []

    def test_wildcard_resource_parsed_correctly(self):
        rules = parse_json_output(JSON_WILDCARD_RESOURCE_READONLY)

        assert len(rules) == 1
        assert "*" in rules[0].resources
        assert set(rules[0].verbs) == {"get", "list", "watch"}


# =============================================================================
# Case 6: kubectl-not-found / SA-not-found → non-zero exit, no crash
# =============================================================================

class TestKubectlErrors:
    """Error handling: kubectl missing, SA missing, permission denied."""

    @patch("mcp_rbac_audit.shutil.which", return_value=None)
    def test_kubectl_not_found_exits_2(self, mock_which):
        """When kubectl is not in PATH → exit 2, not 0 (no false PASS)."""
        with pytest.raises(SystemExit) as exc_info:
            run_kubectl_can_i("kube-mcp", "mcp-servers")

        assert exc_info.value.code == 2

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_sa_not_found_exits_2(self, mock_which, mock_run):
        """kubectl returns error because SA doesn't exist → exit 2."""
        # JSON attempt fails
        mock_run.side_effect = [
            _mock_subprocess_result(stdout="", stderr="error: not found", returncode=1),
            _mock_subprocess_result(
                stdout="",
                stderr='error: User "system:serviceaccount:mcp-servers:nonexistent" not found',
                returncode=1,
            ),
        ]

        with pytest.raises(SystemExit) as exc_info:
            run_kubectl_can_i("nonexistent", "mcp-servers")

        assert exc_info.value.code == 2

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_kubectl_permission_denied_exits_2(self, mock_which, mock_run):
        """kubectl returns 'cannot' error → exit 2."""
        mock_run.side_effect = [
            _mock_subprocess_result(stdout="", returncode=1, stderr=""),
            _mock_subprocess_result(
                stdout="",
                stderr="error: cannot list resource in API group",
                returncode=1,
            ),
        ]

        with pytest.raises(SystemExit) as exc_info:
            run_kubectl_can_i("kube-mcp", "restricted-ns")

        assert exc_info.value.code == 2

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_generic_kubectl_error_exits_2(self, mock_which, mock_run):
        """Unknown kubectl error → exit 2, not 0."""
        mock_run.side_effect = [
            _mock_subprocess_result(stdout="", returncode=1, stderr=""),
            _mock_subprocess_result(
                stdout="",
                stderr="The connection to the server was refused",
                returncode=1,
            ),
        ]

        with pytest.raises(SystemExit) as exc_info:
            run_kubectl_can_i("kube-mcp", "mcp-servers")

        assert exc_info.value.code == 2

    @patch("mcp_rbac_audit.shutil.which", return_value=None)
    def test_kubectl_not_found_no_crash(self, mock_which):
        """Must not raise unhandled exception — clean SystemExit."""
        # SystemExit is expected (caught by pytest.raises); any other
        # exception type means a crash.
        with pytest.raises(SystemExit):
            run_kubectl_can_i("kube-mcp", "mcp-servers")

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_sa_not_found_never_returns_false_pass(self, mock_which, mock_run):
        """Critical security invariant: errors never produce exit 0."""
        mock_run.side_effect = [
            _mock_subprocess_result(stdout="", returncode=1, stderr="error"),
            _mock_subprocess_result(stdout="", returncode=1, stderr="SA not found"),
        ]

        with pytest.raises(SystemExit) as exc_info:
            run_kubectl_can_i("missing", "default")

        # Must not be 0 (which would be false PASS)
        assert exc_info.value.code != 0


# =============================================================================
# Case 7: header-only / empty output handled
# =============================================================================

class TestEmptyOutputHandled:
    """Empty or header-only kubectl output must not crash or false-PASS."""

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_header_only_table_exits_0(self, mock_which, mock_run):
        """Header-only = no rules = no violations → exit 0 (vacuously true)."""
        # JSON fails, table has only header
        mock_run.side_effect = [
            _mock_subprocess_result(stdout="not json", returncode=1),
            _mock_subprocess_result(stdout=TABLE_HEADER_ONLY),
        ]

        with patch("sys.argv", ["mcp_rbac_audit.py", "-s", "empty-sa", "-n", "default"]):
            exit_code = main()

        # No rules = no violations = PASS (consistent with "prove read-only")
        assert exit_code == 0

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_completely_empty_output_exits_0(self, mock_which, mock_run):
        """Empty string output → parsed as zero rules → exit 0."""
        mock_run.side_effect = [
            _mock_subprocess_result(stdout="", returncode=1),
            _mock_subprocess_result(stdout=TABLE_COMPLETELY_EMPTY),
        ]

        with patch("sys.argv", ["mcp_rbac_audit.py", "-s", "empty-sa", "-n", "default"]):
            exit_code = main()

        assert exit_code == 0

    def test_parse_table_header_only_no_crash(self):
        """parse_table_output on header-only input returns empty list."""
        rules = parse_table_output(TABLE_HEADER_ONLY)
        assert rules == []

    def test_parse_table_empty_no_crash(self):
        """parse_table_output on empty string returns empty list."""
        rules = parse_table_output(TABLE_COMPLETELY_EMPTY)
        assert rules == []

    def test_parse_json_empty_status(self):
        """JSON with empty status → empty rules list."""
        rules = parse_json_output(json.dumps({"status": {}}))
        assert rules == []

    def test_audit_empty_rules_is_pass(self):
        """Zero rules → zero violations (nothing to flag)."""
        violations = audit_rules([])
        assert violations == []


# =============================================================================
# Context flag and multi-SA config
# =============================================================================

class TestContextAndConfig:
    """Test --context flag and --config file support."""

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_context_flag_passed_to_kubectl(self, mock_which, mock_run):
        """--context is forwarded to kubectl command."""
        mock_run.return_value = _mock_subprocess_result(stdout=JSON_READONLY_SA)

        run_kubectl_can_i("kube-mcp", "mcp-servers", context="prod-cluster")

        # First call should include --context flag
        first_call_args = mock_run.call_args_list[0][0][0]
        assert any("--context=prod-cluster" in arg for arg in first_call_args)

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_sa_identity_format(self, mock_which, mock_run):
        """SA identity is formatted as system:serviceaccount:ns:name."""
        mock_run.return_value = _mock_subprocess_result(stdout=JSON_READONLY_SA)

        run_kubectl_can_i("kube-mcp", "mcp-servers")

        first_call_args = mock_run.call_args_list[0][0][0]
        assert any(
            "--as=system:serviceaccount:mcp-servers:kube-mcp" in arg
            for arg in first_call_args
        )

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_config_file_multiple_sas(self, mock_which, mock_run, tmp_path):
        """Config file with multiple SAs audits each one."""
        config_file = tmp_path / "mcp-servers.yaml"
        config_file.write_text(
            "service_accounts:\n"
            "  - name: kube-mcp\n"
            "    namespace: mcp-servers\n"
            "  - name: vm-mcp\n"
            "    namespace: mcp-servers\n"
        )
        # Both SAs are read-only
        mock_run.return_value = _mock_subprocess_result(stdout=JSON_READONLY_SA)

        with patch("sys.argv", ["mcp_rbac_audit.py", "--config", str(config_file)]):
            exit_code = main()

        assert exit_code == 0
        # Called for each SA (JSON attempt for each)
        assert mock_run.call_count >= 2

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_config_file_one_bad_sa_fails_all(self, mock_which, mock_run, tmp_path):
        """If any SA in config has mutations → exit 1."""
        config_file = tmp_path / "mcp-servers.yaml"
        config_file.write_text(
            "service_accounts:\n"
            "  - name: good-mcp\n"
            "    namespace: mcp-servers\n"
            "  - name: bad-mcp\n"
            "    namespace: mcp-servers\n"
        )
        # First SA good, second SA bad
        mock_run.side_effect = [
            _mock_subprocess_result(stdout=JSON_READONLY_SA),
            _mock_subprocess_result(stdout=JSON_CREATE_PODS),
        ]

        with patch("sys.argv", ["mcp_rbac_audit.py", "--config", str(config_file)]):
            exit_code = main()

        assert exit_code == 1

    def test_config_file_not_found_exits_2(self, tmp_path):
        """Non-existent config file → exit 2."""
        with patch("sys.argv", ["mcp_rbac_audit.py", "--config", "/nonexistent/file.yaml"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 2

    def test_missing_args_exits_error(self):
        """No --serviceaccount/--namespace and no --config → argparse error."""
        with patch("sys.argv", ["mcp_rbac_audit.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            # argparse exits with 2
            assert exc_info.value.code == 2


# =============================================================================
# JSON-to-table fallback behavior
# =============================================================================

class TestJsonToTableFallback:
    """When JSON output isn't valid, falls back to table parsing."""

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_json_parse_failure_falls_to_table(self, mock_which, mock_run):
        """If -o json returns non-JSON, fall back to table output."""
        table_output = (
            "Resources  Non-Resource URLs  Resource Names  Verbs\n"
            "pods       []                 []              [get list watch]\n"
        )
        mock_run.side_effect = [
            # JSON attempt returns OK but stdout isn't valid JSON
            _mock_subprocess_result(stdout="WARNING: something\nnot json", returncode=0),
            # Table attempt succeeds
            _mock_subprocess_result(stdout=table_output),
        ]

        rules, fmt = run_kubectl_can_i("kube-mcp", "mcp-servers")

        assert fmt == "table"
        assert len(rules) >= 1

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_json_returncode_nonzero_falls_to_table(self, mock_which, mock_run):
        """If -o json returns non-zero, try table."""
        table_output = (
            "Resources  Non-Resource URLs  Resource Names  Verbs\n"
            "pods       []                 []              [get list watch]\n"
        )
        mock_run.side_effect = [
            _mock_subprocess_result(stdout="", returncode=1, stderr="json not supported"),
            _mock_subprocess_result(stdout=table_output),
        ]

        rules, fmt = run_kubectl_can_i("kube-mcp", "mcp-servers")

        assert fmt == "table"


# =============================================================================
# Subprocess timeout handling
# =============================================================================

class TestSubprocessTimeout:
    """Ensure timeout is passed to subprocess.run calls."""

    @patch("mcp_rbac_audit.subprocess.run")
    @patch("mcp_rbac_audit.shutil.which", return_value="/usr/bin/kubectl")
    def test_timeout_30s_set(self, mock_which, mock_run):
        """subprocess.run is called with timeout=30."""
        mock_run.return_value = _mock_subprocess_result(stdout=JSON_READONLY_SA)

        run_kubectl_can_i("kube-mcp", "mcp-servers")

        for call in mock_run.call_args_list:
            assert call[1].get("timeout") == 30
