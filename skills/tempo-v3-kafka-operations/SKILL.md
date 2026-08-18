---
name: tempo-v3-kafka-operations
description: "Use when migrating Grafana Tempo v2\u2192v3, operating the v3 Kafka-based ingest path, or debugging partition-ring errors, orphan partitions, OOM on replay, or missing PDBs. Covers the ingester\u2192block-builder/live-store architecture change, partition ownership (1:1 live-store\u2194partition), Kafka topic partition-count gotchas, memberlist ring reset, and the tempo-distributed chart's missing PDB templates."
keywords: [tempo-v3-kafka-operations, tempo, v3, kafka, operations, "tempo v3", "v3 kafka", "kafka operations", grafana, oom, pdb]
---
# Tempo v3 Kafka Operations

Grafana Tempo 3.0 replaces the ingester-based write path with a **Kafka-based** pipeline. This skill covers the architecture change, the non-obvious operational gotchas, and the remediation procedures — all validated empirically during a real v2→v3 migration.

Applies to any Tempo 3.x + Kafka deployment (microservices mode). Replace `<org>` placeholders and `monitoring` namespace with your environment.

## Architecture: v2 → v3 component mapping

| v2 component | v3 replacement | Role in v3 |
|--------------|----------------|------------|
| `ingester` (StatefulSet, local WAL, RF3) | `blockBuilder` + `liveStore` | Split: block-builder writes blocks to object storage; live-store serves recent-data queries (~30 min) |
| `compactor` | `backendScheduler` + `backendWorker` | Scheduler plans compaction jobs; workers execute them |
| — (new) | `ingest.kafka` | Kafka is the durable WAL between distributor and consumers |

Write path in v3:
```
App → OTel Collector → OTLP gRPC → distributor
                                      ↓ (shards by trace_id, writes to Kafka)
                                   Kafka topic (tempo_traces)
                        ┌────────────┼────────────┐
                 block-builder   live-store   metrics-generator
                   ↓ blocks→S3    ↓ recent      ↓ service-graphs
```

Because Kafka provides durability, v3 runs **replication factor 1 (RF1)** — no ingester replication on the write path.

## CRITICAL: live-store is 1:1 with partitions; block-builder is not

This is the single most important operational fact and the root of most partition-ring errors.

| Component | Partition mapping | Config knob |
|-----------|-------------------|-------------|
| **block-builder** | ordinal-based; default `partitions_per_instance: 0` = 1:1 by ordinal. Set `> 0` (opt-in) to let one instance own multiple partitions | `blockBuilder.config.partitions_per_instance` |
| **live-store** | effectively **1:1 by ordinal** — no `partitions_per_instance` equivalent exists, so no config lets one live-store own multiple partitions | none exists |

> Upstream docs phrase live-store as consuming "one or more" partitions, but the invariant that matters operationally is: **each partition has exactly one owner (per zone), and there is no knob to assign extra partitions to a live-store**. So in practice `liveStore.replicas` must equal the partition count. block-builder has the same 1:1 *default* (`partitions_per_instance: 0`), but unlike live-store it can be told to own multiple partitions per instance.

Consequences:
- **`liveStore.replicas` MUST equal the Kafka topic partition count.** With 5 live-stores and a 10-partition topic, partitions 5–9 have **no owner** → queriers fail with `error finding partition ring replicas: partition N: too many unhealthy instances in the ring`.
- block-builder can cover 10 partitions with 5 replicas **only if** you opt into `partitions_per_instance: 2`; with the default `0` it is also 1:1 and needs 10 replicas.

Diagnostic: the error names the orphaned partitions. Range `5..9` on a 10-partition topic with 5 live-stores = exactly `(partitions − live_stores)` unowned partitions.

Verify partition ownership via the ring JSON:
```bash
kubectl port-forward -n monitoring <querier-pod> 13200:3200 &
curl -s "http://localhost:13200/partition-ring" -H "Accept: application/json" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(p['id'], p['state'], p.get('owner_ids') or 'ORPHAN') for p in d['partitions']]"
```
`state`: 2 = ACTIVE, 3 = INACTIVE. Owners `None`/`ORPHAN` on an ACTIVE partition = broken.

## CRITICAL: Kafka topic partition count vs broker `num.partitions`

Tempo's `ingest.kafka.auto_create_topic_default_partitions` attempts to set the broker-wide `num.partitions` default via the Kafka AdminClient. On managed or declaratively-configured Kafka (e.g. MSK, or a broker whose `num.partitions` is pinned by an operator), that change **fails silently** — the broker default wins and the topic is auto-created with the broker's count, ignoring Tempo's value. It also won't help if the topic is pre-created by another service.

Symptom: you set `auto_create_topic_default_partitions: 5`, but the topic keeps coming back with the broker's count (e.g. 10), even after deleting it — because the distributor auto-recreates it at the broker default immediately.

In practice, the safest approach regardless of Kafka model is **manual topic creation + `auto_create_topic_enabled: false`**:
```yaml
# values.yaml
ingest:
  kafka:
    topic: tempo_traces
    auto_create_topic_enabled: false   # ESSENTIAL — else distributor recreates at broker default
```
```bash
# 1. Apply values (auto_create=false) FIRST so the distributor stops recreating
# 2. Delete the wrong-sized topic, WAIT for async delete to complete, THEN create:
kubectl exec -n monitoring <kafka-broker-0> -- bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --delete --topic tempo_traces
sleep 10   # delete is async — verify it's gone before creating
kubectl exec -n monitoring <kafka-broker-0> -- bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --create --topic tempo_traces \
  --partitions 5 --replication-factor 3
```

Gotchas:
- **Kafka never reduces partitions.** Going from 10→5 requires delete + recreate (not `--alter`).
- **Delete is asynchronous.** Running delete + create back-to-back fails with `TopicExistsException`. Wait and verify `GONE` before creating.
- After recreating, consumers cache the **old TopicId** → `UNKNOWN_TOPIC_ID: This server does not host this topic ID`. Restart consumers **after** the new topic is stable (see below).
- Avoid mixing `.` and `_` in topic names (Kafka metric-name collision warning).

## CRITICAL: stale partition-ring entries require a FULL memberlist reset

The partition ring lives in the **memberlist gossip KV**, shared by ALL Tempo components (distributor, live-store, block-builder, querier, query-frontend, metrics-generator, backend-scheduler, backend-worker). Orphan partitions (e.g. 5–9 after downscaling from 10 to 5) persist as ACTIVE-with-no-owner and are **re-gossiped by any surviving member**.

Things that do NOT clear stale partitions:
- Rolling restart of live-stores (survivors re-gossip the old state).
- Setting partitions INACTIVE via the `/partition-ring` page — **queriers still read INACTIVE partitions**, so the error persists (INACTIVE ≠ removed).
- Scaling down only the read-path components (metrics-generator/backend-* keep the KV alive).

What DOES clear them — scale **every memberlist member** to zero simultaneously, wait for full termination, then scale back up:
```bash
kubectl scale -n monitoring statefulset/tempo-live-store statefulset/tempo-block-builder \
  statefulset/tempo-backend-scheduler statefulset/tempo-backend-worker --replicas=0
kubectl scale -n monitoring deployment/tempo-distributor deployment/tempo-querier \
  deployment/tempo-query-frontend deployment/tempo-metrics-generator --replicas=0
# WAIT until 0 pods remain (crucial — the gossip KV must fully disappear)
# then scale all back to target counts
```
The ring rebuilds from scratch with only the current live-stores → partition count = live-store count. Gateway (nginx) and memcached are not memberlist members and don't need restarting.

Impact: brief total Tempo outage (~1–2 min). No data loss — Kafka retains (default retention), live-store replays from last committed offset on restart.

**Non-disruptive alternative:** stale INACTIVE partitions are auto-expired by `delete_inactive_partition_after` (default **13h**). If you can wait, marking the orphans INACTIVE and letting them expire avoids any outage. The scale-to-zero is the emergency option for when partitions are stuck **ACTIVE-with-no-owner** (which never auto-expire), or when the 13h wait is unacceptable.

## CRITICAL: metrics-generator memory in v3

In v2 the metrics-generator received spans **pushed** from the distributor over gRPC (streaming, low memory). In v3 it **consumes from Kafka** and rebuilds state → memory needs are much higher. A v2-sized limit (e.g. 512Mi) will **OOMKill** on v3.

Same applies to **live-store on startup**: it replays the Kafka lookback window (~40 min) into memory to rebuild recent-data state. After an outage with accumulated backlog, the replay spike can OOM a tight limit. Size limits with replay headroom (e.g. live-store 6Gi limit, metrics-generator ≥3Gi).

Symptom: `exitCode=137`, `lastState.terminated.reason: OOMKilled`, CrashLoopBackOff shortly after a mass restart or migration.

## CRITICAL: tempo-distributed chart lacks PDB templates for block-builder and live-store

The `tempo-distributed` v3 chart ships PDB templates for every component **except** the two new StatefulSets (`block-builder`, `live-store`) — they only get service/servicemonitor/statefulset. Setting `blockBuilder.podDisruptionBudget.enabled: true` is **inert** (no template consumes it).

Add PDBs via `extraObjects` (the chart renders `.Values.extraObjects` through `tpl`):
```yaml
extraObjects:
  - apiVersion: policy/v1
    kind: PodDisruptionBudget
    metadata:
      name: tempo-live-store
    spec:
      maxUnavailable: 1
      selector:
        matchLabels:
          app.kubernetes.io/name: tempo
          app.kubernetes.io/instance: tempo
          app.kubernetes.io/component: live-store
  # repeat with component: block-builder
```

Related chart bug: the default values omit `backendWorker.podDisruptionBudget`, so its PDB template hits `nil pointer evaluating interface {}.enabled`. Set `backendWorker.podDisruptionBudget.enabled` explicitly.

## AZ spread and the DoNotSchedule ↔ spot trade-off

The chart's default `topologySpreadConstraints` use `whenUnsatisfiable: ScheduleAnyway` (soft) — best-effort, so under scheduling pressure pods can land 2-in-one-AZ (skew 2). To guarantee even spread, override with `whenUnsatisfiable: DoNotSchedule`.

**But for the 1:1-partition components (block-builder, live-store) this is dangerous when combined with spot-only node affinity**: `DoNotSchedule` + a zone with no spot capacity = a Pending pod = an **orphan partition** = the exact ring error above. Make it safe by allowing on-demand fallback (`karpenter.sh/capacity-type In [spot, on-demand]`) so every AZ always has schedulable capacity. A Pending querier only reduces query capacity (no orphan), so DoNotSchedule there is low-risk.

## Migration runbook (v2 → v3, in-place)

1. Prereq: v2 block format is **vParquet4 or later** (v3 drops vParquet3). Confirm `storage.trace.block.version`.
2. Have a Kafka broker reachable; decide partition count = intended live-store replica count.
3. Rewrite values: remove `ingester`/`compactor`; add `ingest.kafka`, `blockBuilder`, `liveStore` (replicas = partitions), `backendScheduler`, `backendWorker`. Remove `local_blocks` from metrics-generator. Bump metrics-generator + live-store memory.
4. Create the Kafka topic manually with the right partition count; set `auto_create_topic_enabled: false`.
5. Apply. Watch for: OOM (memory), partition-ring errors (replica/partition mismatch), `UNKNOWN_TOPIC_ID` (restart consumers), legacy-overrides startup error (migrate to scoped overrides format).
6. Validate end-to-end: distributor `received` logs, block-builder `Flushed block` + `committed offset to kafka`, ring shows all partitions owned, queries return recent + historical traces.

## Anti-patterns

- ❌ Setting `liveStore.replicas` ≠ Kafka partition count (orphan partitions)
- ❌ Relying on `auto_create_topic_default_partitions` when the broker sets `num.partitions`
- ❌ Rolling-restarting to clear stale ring entries (only a full memberlist scale-to-zero works)
- ❌ Treating INACTIVE partitions as removed (queriers still read them)
- ❌ Carrying v2 metrics-generator memory limits into v3 (OOM — it now consumes Kafka)
- ❌ `DoNotSchedule` + spot-only affinity on block-builder/live-store without on-demand fallback (Pending → orphan)
- ❌ Assuming the chart creates PDBs for block-builder/live-store (it doesn't — use extraObjects)
- ❌ delete + create a Kafka topic back-to-back (async delete → TopicExistsException)

## Key diagnostic endpoints

| Path | Component | Shows |
|------|-----------|-------|
| `GET /partition-ring` (+ `Accept: application/json`) | distributor, querier, live-store | Partition states + owners |
| `GET /live-store/ring` | live-store | Live-store hash ring health |
| `GET /backend-worker/ring` | backend-worker | Compaction sharding |
| `tempo_ingest_group_partition_lag{group=...}` | metric | Kafka consumer lag per group |
