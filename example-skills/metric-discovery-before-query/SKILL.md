---
name: metric-discovery-before-query
description: How to discover the real metric names, label keys and label values in a Prometheus-compatible backend before writing a PromQL/MetricsQL query, so an empty result is never mistaken for "no problem".
keywords: [promql, metricsql, "metric name", "metric names", "which metric", "what metric", "no data", "empty result", "no results", "query returns nothing", "label values", "label names", "series", cardinality, "find the metric", "discover metrics", "_total suffix", "otel metric"]
---

# Discover metrics before you query

The most common observability mistake is querying a metric name that does not exist in
this environment. The backend returns an **empty result, not an error**, and an empty
result reads like "no errors, all good". Always prove the name and labels exist first.

Posture: read-only. Discovery uses the backend's metadata APIs; nothing is written.

## The discovery sequence

1. **Find candidate names** — search by substring instead of guessing the full name:
   - `GET /api/v1/label/__name__/values` (optionally with `match[]=` and a time range)
   - or a regex selector: `count by (__name__) ({__name__=~".*http.*request.*"})`
2. **Inspect one series** to learn its labels:
   `topk(1, <metric_name>)` or `GET /api/v1/series?match[]=<metric_name>`.
3. **List the values of the label you will filter on**:
   `GET /api/v1/label/<label>/values?match[]=<metric_name>`
   — e.g. confirm the service is called `checkout` and not `checkout-api`.
4. **Check the metric type** (`GET /api/v1/metadata?metric=<name>` when supported):
   counter, gauge, histogram or summary decides which functions are valid.
5. **Only then** write the real query, using the exact names and label values found.

Keep discovery bounded: always pass a `match[]` and a time window; listing every series in
a large backend is expensive.

## Naming traps to check for explicitly

| Trap | What happens | How to handle |
|------|--------------|---------------|
| OpenTelemetry → Prometheus translation | `http.server.request.duration` becomes `http_server_request_duration_seconds` (dots → underscores, unit suffix added) | Search for the stem with a regex, not the dotted name |
| Counter `_total` suffix | Some exporters add it, some do not, some double it | Confirm the exact name in step 1 |
| Histogram vs summary | Histograms have `_bucket`/`_sum`/`_count`; summaries have `quantile` label | `histogram_quantile()` only works on `_bucket` |
| Unit suffixes | `_seconds` vs `_milliseconds`, `_bytes` | Read the suffix before writing thresholds |
| Service label key | Could be `service`, `service_name`, `job`, `app`, `app_kubernetes_io_name` | Use the key that actually exists on the series (step 2) |
| Environment / cluster labels | Added by the collector or remote-write (`cluster`, `env`) — not always present | Do not filter on a label you have not seen |
| Renamed metrics after upgrades | Exporter versions rename series | Prefer what discovery returns over documentation memory |

## Interpreting results honestly

| Result | Correct reading |
|--------|-----------------|
| Name not found | The metric is not collected here — say so; do not report "0" |
| Name found, filtered query empty | The label value is wrong or the series stopped — check the label values and the last sample time |
| Series exists but stale (no recent samples) | The target stopped being scraped — check `up{job="..."}` |
| Non-zero but tiny counter rate | Real low traffic, or a counter that resets — use `rate()`/`increase()` over a sensible window |

`up == 0` or a missing `up` series for the job means the problem is collection, not the
application.

## Query hygiene once names are known

- Use `rate()` / `increase()` on counters; never graph a raw counter as a rate.
- Aggregate before `histogram_quantile`: `histogram_quantile(0.99, sum by (le) (rate(x_bucket[5m])))`.
- Pick a range at least 4× the scrape interval.
- Ratios (error rate) need numerator and denominator from the same metric family and labels.
- Never add high-cardinality labels (user IDs, request IDs, raw URLs) to `by (...)`.

## Answer format

Say which names and labels you confirmed (and how), show the final query, and give the
value with its time window. If discovery found nothing, report "metric not present in this
backend" as the finding — that is a valid, useful answer.
