---
name: istio-ambient-debugging
description: "Istio Ambient mode debugging and gotchas. Use when troubleshooting unexpected 400 errors on gRPC, source IP issues, missing pod metadata, ServiceEntry loops, or routing failures in cross-cluster setups. Covers waypoint SNAT, ServiceEntry protocol behavior, hostname mismatch errors, ephemeral pod limitations."
keywords: [istio-ambient-debugging, istio, ambient, debugging, "istio ambient", "ambient debugging", ip, snat]
---
# Istio Ambient Mode — Debugging & Gotchas

Common pitfalls and troubleshooting patterns specific to Istio Ambient mode.

## Gotcha 1: Waypoint SNAT breaks source-IP-based identification

### Symptom
- `k8sattributes` processor in OTel Collector can't enrich telemetry with pod metadata
- Logs show all telemetry coming from "unknown pod"
- Source IP in agent logs is consistently the same (waypoint pod IP)

### Root cause
In ambient mode, traffic through the waypoint loses the original source IP (waypoint does SNAT). Any system relying on connection source IP for identification breaks.

### Solution
Add `istio.io/use-waypoint: "none"` to the resource (ServiceEntry, Pod, Service):

```yaml
metadata:
  labels:
    istio.io/use-waypoint: "none"
```

This makes ztunnel deliver directly to the destination, preserving source IP.

### When to apply
- ServiceEntries that route to k8sattributes-aware backends (OTel Collector)
- Services that use IP-based authentication or audit logging
- Backends that need original client IP for rate limiting

## Gotcha 2: ServiceEntry protocol determines waypoint behavior

### Symptom
VirtualService rules with `http:` block don't apply when ServiceEntry uses `protocol: TLS` or `protocol: HTTPS`.

### Root cause
Waypoint behavior is determined by the ServiceEntry port protocol:

| Protocol | Waypoint behavior | VirtualService support |
|----------|-------------------|------------------------|
| `HTTP` | L7 routing | `http:` rules apply |
| `HTTPS` | TCP passthrough | `tls:` matches only (no termination) |
| `TLS` | TCP passthrough | `tls:` matches only (no termination) |
| `GRPC` | L7 routing (HTTP/2) | `http:` rules apply |

### Solution
- Need L7 features (header manipulation, retries, etc) → use `protocol: HTTP` (or `GRPC`)
- Need TLS termination at waypoint → currently NOT supported (only mesh mTLS)
- Need TLS passthrough → use `protocol: HTTPS` and `tls:` matches in VirtualService

## Gotcha 3: ServiceEntry can cause routing loops

### Symptom
- Pipeline goes into infinite loop
- Agent receives its own forwarded telemetry
- Memory/CPU climbs continuously

### Root cause
If the agent's exporter points to the SAME hostname as a ServiceEntry that routes to the agent itself:

```
exporter: otelcollector-prd.<old-internal-domain>:443
ServiceEntry: otelcollector-prd.<old-internal-domain> → otel-agent-collector  # the agent itself!
```

→ agent → ServiceEntry → agent → ServiceEntry → agent...

### Solution
Either:
- Remove the exporter that points to the same hostname (use a different one for outbound)
- Use a different hostname for the MDT pipeline (e.g., `otel-mdt.<old-internal-domain>` instead of `otelcollector-prd.<old-internal-domain>`)
- Add `excludeOutboundIPRanges` to bypass mesh for this specific destination

## Gotcha 4: gRPC hostname mismatch returns `Unimplemented`

### Symptom
Cross-cluster gRPC client (e.g., loadbalancing exporter) gets:
```
rpc error: code = Unimplemented desc = unknown service
```

### Root cause
The destination Gateway has GRPCRoutes configured for SPECIFIC hostnames. If the client hostname doesn't match exactly, the gateway has no route and returns Unimplemented.

```yaml
# GRPCRoute (server side)
spec:
  hostnames:
    - otel-gateway-0.<org-domain>

# Client side
exporters:
  loadbalancing/traces:
    resolver:
      static:
        hostnames:
          - otel-gateway-0.<org-domain>:4317   # ✅ matches
          - otel-gateway-collector-0.<org-domain>:4317  # ❌ no route
```

### Solution
Make hostnames match EXACTLY between client config and GRPCRoute.

### Debug command
From a debug pod, test connectivity with `grpcurl`:
```bash
kubectl run debug --rm -i --restart=Never \
  --image=fullstorydev/grpcurl:latest -- \
  -insecure otel-gateway-0.<org-domain>:4317 list
```

If this returns service list, route works. If `Unimplemented`, hostname mismatch.

## Gotcha 5: Ephemeral pods may not be enriched by k8sattributes

### Symptom
Test pods (`kubectl run ... --rm`) sometimes have no metadata in OTel telemetry.

### Root cause
The `k8sattributesprocessor` uses informers to cache pod metadata. Very short-lived pods (< 2s) may complete before the informer has cached them.

### Solution
For testing:
- Use longer-lived pods (`sleep 30` before exiting)
- Use existing pods via `kubectl exec`
- Add a wait period: `sleep 5 && telemetrygen ...`

For production:
- Not an issue (pods live longer)

## Gotcha 6: k8sattributes filter by `CostCenter` label

### Symptom
Production pods missing telemetry enrichment despite running for hours.

### Root cause
<org>'s k8sattributes is configured with a filter:
```yaml
processors:
  k8sattributes:
    filter:
      labels:
        - key: CostCenter
          op: exists
```

Pods without `CostCenter` label are NOT enriched.

### Solution
Add `CostCenter` label to deployment spec:
```yaml
spec:
  template:
    metadata:
      labels:
        CostCenter: <your-cost-center>
```

## Gotcha 7: VirtualService HTTP rules don't work for HTTPS ServiceEntry

### Symptom
VirtualService applies to internal traffic but not to traffic with HTTPS ServiceEntry.

### Root cause
See Gotcha 2 — protocol determines waypoint behavior.

### Solution
- For TLS routing decisions: use `tls:` match in VirtualService
- For full L7 features: terminate TLS upstream (e.g., at agent's `otlp/tls` receiver) and let internal traffic be HTTP

## Gotcha 8: Pod can't reach external hostname after ServiceEntry added

### Symptom
Adding ServiceEntry for `external-api.com` breaks connectivity to that host.

### Root cause
ServiceEntry intercepts DNS resolution → traffic routed through mesh → may need additional config (e.g., DestinationRule, retry policies, TLS).

### Solution
Test before adding ServiceEntry:
```bash
# Without ServiceEntry: real DNS, direct connection
nslookup external-api.com
curl -v https://external-api.com

# With ServiceEntry: Istio VIP, intercepted by ztunnel
# Verify config:
istioctl proxy-config endpoints <pod>
```

If ServiceEntry breaks something, validate:
- `resolution: DNS` (not `NONE` or `STATIC`)
- Correct `endpoints:` (if using static)
- Proper TLS settings if `protocol: HTTPS`

## Gotcha 9: Gateway listener port collisions

### Symptom
Two listeners with same port fail to bind.

### Root cause
Gateway listeners on the same port must use SNI-based routing or different protocols.

### Solution
Use `tls.mode: PASSTHROUGH` + SNI matching, OR pick different ports.

## Standard test commands

### Trace test from DEV cluster

```bash
kubectl run trace-test --rm -i --restart=Never \
  --labels="Environment=dev,CostCenter=test" \
  --image=ghcr.io/open-telemetry/opentelemetry-collector-contrib/telemetrygen:latest \
  -n monitoring -- traces \
  --otlp-endpoint otelcollector-prd.<old-internal-domain>:443 \
  --otlp-http --otlp-http-url-path /v1/traces \
  --otlp-insecure-skip-verify \
  --service trace-test-external \
  --telemetry-attributes 'route="test"' \
  --status-code Error \
  --traces 3
```

Required labels:
- `Environment=dev` — for k8sattributes to set `deployment.environment`
- `CostCenter=test` — for k8sattributes filter

### Tempo query from core-devops

```bash
kubectl --context core-devops run tempo-q -n monitoring --rm -i --restart=Never \
  --image=curlimages/curl:latest -- -s \
  "http://tempo-gateway.monitoring:80/api/search?q={resource.service.name=\"trace-test-external\"}&limit=5&start=<epoch>&end=<epoch>"
```

## Tail sampling debugging

For traces to pass tail sampling, MUST match at least one policy:

| Policy | Condition |
|--------|-----------|
| `errors` | span status = ERROR |
| `high-latency` | duration > 1000ms |
| `debug-forced` | tracestate `debug=true` |
| `prd-baseline` | `deployment.environment` ∈ {prd, prod, PRD, PROD} → 10% probabilistic |
| `btc-baseline` | `deployment.environment` ∈ {btc, batch, PRD-BATCH} → 5% probabilistic |
| `dev-baseline` | `deployment.environment` ∈ {dev, DEV, hml, HML, local, LOCAL} → 100% |

If a trace has no `deployment.environment` and is NOT error/high-latency → DROPPED.

For test traces: always use `--status-code Error` OR ensure pod has `Environment=dev` label.

## Useful debug tools

```bash
# Check ambient mode is active for namespace
kubectl get ns my-app -o jsonpath='{.metadata.labels}'

# Check waypoint deployment
kubectl get deploy -n my-app waypoint

# Inspect ztunnel config
kubectl exec -n istio-system <ztunnel-pod> -- /bin/sh -c "cat /etc/ztunnel/config.yaml"

# Check service mesh status
istioctl analyze -n my-app

# Trace debug with istioctl proxy-config
istioctl proxy-config endpoints <pod>
istioctl proxy-config listeners <pod>
```

## Reference

- Istio Ambient docs: https://istio.io/latest/docs/ambient/
- istioctl reference: https://istio.io/latest/docs/reference/commands/istioctl/
- Related skills: `istio-ambient-otel`, `otel-collector-multi-cluster`, `grpc-distributed-tracing`
