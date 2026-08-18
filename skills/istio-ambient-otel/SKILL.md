---
name: istio-ambient-otel
description: "Istio Ambient mesh configuration for OTel and cross-cluster traffic. Use when configuring ServiceEntry for cross-cluster routing, setting up TLS receivers, or designing services that work with ambient mode. Covers ztunnel + waypoint, namespace labels, ServiceEntry patterns, gateway listeners."
keywords: [istio-ambient-otel, istio, ambient, otel, "istio ambient", "ambient otel", tls]
---
# Istio Ambient Mode — Configuration Patterns

How to configure services and routing in Istio Ambient mode at <org>.

## Architecture

Istio Ambient (NOT sidecar) mode:

```
[Pod] ←→ ztunnel (DaemonSet, L4 mTLS) ←→ waypoint (Deployment, L7 policies)
```

| Component | Layer | Role |
|-----------|-------|------|
| **ztunnel** | L4 | mTLS, basic routing (DaemonSet on every node) |
| **waypoint** | L7 | HTTP policies, authorization, observability (Deployment per namespace) |

## Namespace configuration

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: my-app
  labels:
    istio.io/dataplane-mode: ambient    # Enables ambient mode
    istio.io/use-waypoint: waypoint     # Routes through L7 waypoint
```

| Label | Effect |
|-------|--------|
| `istio.io/dataplane-mode: ambient` | Traffic intercepted by ztunnel |
| `istio.io/use-waypoint: waypoint` | Routes through waypoint for L7 processing |
| `istio.io/use-waypoint: "none"` | Bypass waypoint (ztunnel direct delivery) |

## Waypoint behavior by protocol

| ServiceEntry protocol | Waypoint behavior |
|-----------------------|-------------------|
| `HTTP` | L7 routing (VirtualService applies, headers manipulated) |
| `HTTPS` | TCP passthrough (VirtualService ignored) |
| `TLS` | TCP passthrough (VirtualService ignored) |
| `GRPC` | L7 routing (HTTP/2) — requires service port `name: grpc` |

**Key insight**: HTTPS/TLS = waypoint does TCP passthrough only. To do L7 on HTTPS, terminate TLS at the waypoint OR upstream. VirtualService with `http` routes only works with `protocol: HTTP`.

## Cross-cluster routing pattern

Goal: app in DEV cluster sends telemetry to core-devops cluster.

### ServiceEntry for internal redirect (DEV cluster)

```yaml
apiVersion: networking.istio.io/v1
kind: ServiceEntry
metadata:
  name: otel-internal
  namespace: monitoring
  labels:
    istio.io/use-waypoint: "none"  # CRITICAL: bypass waypoint
spec:
  hosts:
    - otelcollector-prd.<old-internal-domain>
  ports:
    - number: 443
      name: tls-otel
      protocol: TLS
      targetPort: 443
  resolution: DNS
  location: MESH_INTERNAL
  endpoints:
    - address: otel-agent-collector.monitoring.svc.cluster.local
```

### Why `use-waypoint: none` is critical

Without it:
1. ztunnel sends to waypoint via HBONE
2. Waypoint does TCP passthrough (protocol: TLS/HTTPS)
3. Agent sees waypoint IP as source
4. **`k8sattributes` cannot identify the original pod** (filters by pod IP)

With it:
1. ztunnel delivers DIRECTLY to agent (bypasses waypoint)
2. Agent sees real pod IP
3. k8sattributes works correctly

## DNS resolution behavior

| ServiceEntry present? | DNS resolves to |
|----------------------|-----------------|
| ✅ Yes | Istio VIP (240.240.0.x), intercepted by ztunnel |
| ❌ No | Real DNS (NLB IPs, external) — bypasses mesh entirely |

## Istio Gateway for cross-cluster ingress (core-devops)

The `istio-olly-internal` Gateway in core-devops cluster:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: istio-olly-internal
  namespace: monitoring
spec:
  gatewayClassName: istio
  listeners:
    - name: http
      port: 80
      protocol: HTTP
    - name: https
      port: 443
      protocol: HTTPS
      tls: { ... }
    - name: grpc-4317        # CRITICAL for OTLP
      port: 4317
      protocol: HTTPS
      tls: { ... }
    - name: grpc-4318
      port: 4318
      protocol: HTTPS
      tls: { ... }
  addresses:
    - type: Hostname
      value: lbi-olly-<org>-eks-prd-*.elb.us-east-1.amazonaws.com
```

NLB: provides Internet-facing endpoint with TLS termination on all HTTPS listeners.

## GRPCRoute pattern

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: GRPCRoute
metadata:
  name: otel-gateway-0
  namespace: monitoring
spec:
  parentRefs:
    - name: istio-olly-internal
  hostnames:
    - otel-gateway-0.<org-domain>   # MUST match exporter hostname EXACTLY
  rules:
    - backendRefs:
        - name: otel-gateway-collector-0  # Per-instance Service
          port: 4317
```

### CRITICAL: hostname must match

The client-side `loadbalancing` exporter hostnames MUST exactly match GRPCRoute hostnames:
- ✅ `otel-gateway-0.<org-domain>:4317`
- ❌ `otel-gateway-collector-0.<org-domain>:4317` (returns `Unimplemented`)

## TLS receiver configuration (OTel Collector agent)

```yaml
receivers:
  otlp/tls:
    protocols:
      http:
        endpoint: 0.0.0.0:443
        tls:
          cert_file: /certs/tls.crt
          key_file: /certs/tls.key
```

Certificate managed by cert-manager:

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: otel-internal-tls
  namespace: monitoring
spec:
  secretName: otel-internal-tls
  duration: 2160h          # 90 days
  renewBefore: 720h        # Renew 30 days before expiry
  dnsNames:
    - <old-internal-domain>
    - '*.<old-internal-domain>'
  issuerRef:
    group: awspca.cert-manager.io
    kind: AWSPCAClusterIssuer
    name: aws-privateca-issuer
```

## Service ports for OTel Collector agent

```yaml
apiVersion: v1
kind: Service
metadata:
  name: otel-agent-collector
spec:
  ports:
    - name: otlp-grpc
      port: 4317
    - name: otlp-http
      port: 4318
    - name: otlp-tls-http
      port: 443
```

## gRPC services in ambient mode

For services exposing gRPC, the port `name` MUST be `grpc`:

```yaml
apiVersion: v1
kind: Service
spec:
  ports:
    - name: grpc        # ✅ Waypoint handles HTTP/2 correctly
      port: 50051
      targetPort: 50051
```

With `name: http`, waypoint treats traffic as HTTP/1.1 → returns 400 for gRPC frames.

Alternative:
```yaml
ports:
  - name: my-port
    port: 50051
    appProtocol: grpc
```

## MDT (secondary export path)

Agent can have a parallel pipeline for external collectors:

```yaml
exporters:
  otlphttp/mdt:
    endpoint: https://otel-mdt.<old-internal-domain>:443

service:
  pipelines:
    traces/mdt:
      receivers: [otlp]
      processors: [attributes/mdt_origin, batch]
      exporters: [otlphttp/mdt]
    # Similar for metrics/mdt, logs/mdt
```

This pipeline does NOT run k8sattributes or tail_sampling — pure forwarding.

## Cluster contexts at <org>

| Context | Cluster | Region | Purpose |
|---------|---------|--------|---------|
| `dev` | <org>-eks-dev | us-east-1 | Development |
| `prd-nv` | <org>-eks-prd-nv | us-east-1 | Production (Northeast Virginia) |
| `core-devops` | <org>-eks-core | us-east-1 | Observability backends (Tempo, VM, Loki) |
| (full ARN) | <org>-eks-prd | us-east-1 | Production |

## When NOT to bypass waypoint

`istio.io/use-waypoint: "none"` is for SPECIFIC cases (preserving source IP). General app-to-app traffic should KEEP the waypoint for:
- Authorization policies
- Request-level observability
- HTTP-level traffic management

Only bypass when you have a concrete reason (e.g., `k8sattributes` IP-based pod association).

## Reference

- Istio Ambient docs: https://istio.io/latest/docs/ambient/
- Gateway API: https://gateway-api.sigs.k8s.io/
- Related skills: `istio-ambient-debugging`, `otel-collector-multi-cluster`, `grpc-distributed-tracing`
