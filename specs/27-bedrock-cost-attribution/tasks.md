# Tasks: Bedrock Cost Attribution

- [x] Task 1: Terraform module `bedrock-aip/` — 1 AIP per model, FinOps tags, output model→arn map
- [x] Task 2: Extend `iam/` policy to allow invoke on `application-inference-profile/*`
- [x] Task 3: Wire `bedrock-aip` into `example/` + outputs
- [x] Task 4: App — propagate `agent_id` into token/cost metrics in `BedrockClient.invoke` (callers: GenericAgent, Classifier)
- [x] Task 5: Tests ≥90% (`tests/test_bedrock.py` +2 tests; 7/7 pass)
- [x] Task 6: Docs — `bedrock-aip/README.md` (manual prereq + MetricsQL rateio) + main terraform README

## Notes
- AIP `copy_from` = system `us.` inference profile (Sonnet 4.5 has no on-demand
  on the bare foundation-model ARN — confirmed empirically).
- Per-agent cost = showback (token-share × Cost Explorer model spend), not a
  per-agent AIP. Signal to revisit: if contractual chargeback per agent/tenant
  is needed.
- Manual prereq: activate the 4 cost allocation tags in Billing console (1x).
- App test note: `test_bedrock.py` needs the git+ssh `otel_helper`; runs in CI,
  or locally with a minimal stub on PYTHONPATH.
