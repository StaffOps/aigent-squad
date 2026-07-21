---
name: strimzi-kafka-metrics
description: "Use when troubleshooting Strimzi-managed Kafka via Prometheus metrics \u2014 broker/controller JMX metrics (kafka_server_*, kafka_controller_*, kafka_network_*, kafka_log_*), KRaft quorum metrics, Kafka Exporter consumer lag (kafka_consumergroup_lag), Strimzi operator reconciliation metrics (strimzi_*), Cruise Control, Kafka Connect / MirrorMaker 2 / Bridge. Covers the Prometheus JMX Exporter naming rules from the Strimzi kafka-metrics.yaml ConfigMap."
keywords: [strimzi-kafka, strimzi, kafka, "strimzi kafka", prometheus, jmx]
---
# Strimzi Kafka Metrics — Environment-Anchored Reference

Metrics reference for a **Strimzi-managed Apache Kafka** deployment (KRaft mode). In this environment Strimzi lives under `k8s-setup/monitoring/kafka*` (`kafka-operator`, `kafka-cluster`).

> ⚠️ **Metric names below follow the Strimzi Prometheus JMX Exporter naming rules** (from `examples/metrics/kafka-metrics.yaml`). Exact availability of individual MBeans depends on the Kafka version and whether the component (broker, controller, Connect, MM2) is running. Confirm presence with a VictoriaMetrics query (`{__name__=~"kafka_server_.+"}`) before asserting a value — several MBeans are version-specific (ZooKeeper-era metrics are gone in KRaft mode).

---

## 1. Metrics architecture — how Strimzi exposes Kafka metrics

```
Kafka JVM (JMX MBeans) ──► Prometheus JMX Exporter (javaagent) ──► :9404/metrics ──► vmagent scrape ──► VictoriaMetrics
                            (config from kafka-metrics ConfigMap)
Kafka __consumer_offsets ──► Kafka Exporter (danielqsj) ──────────► :9308/metrics ──► vmagent scrape ──► VictoriaMetrics
Strimzi operators (Vert.x/Micrometer) ─────────────────────────────► :8080/metrics ──► vmagent scrape ──► VictoriaMetrics
```

| Source | Port | What it exposes | Enabled by |
|---|---|---|---|
| Prometheus JMX Exporter | `9404` | Broker/controller/Connect/MM2/Bridge/Cruise Control JMX metrics | `spec.*.metricsConfig.type: jmxPrometheusExporter` (ConfigMap) |
| Strimzi Metrics Reporter (alternative) | `9404` | Same Kafka metrics, native (no JMX bridge) | `spec.*.metricsConfig.type: strimziMetricsReporter` |
| Kafka Exporter | `9308` | Consumer group lag, topic/partition offsets | `spec.kafkaExporter: {}` in `Kafka` CR |
| Cluster/Topic/User Operator | `8080` | `strimzi_*` reconciliation + JVM metrics | on by default in the operators |

**Reserved ports (never used for client listeners):** `9090`/`9091` interbroker, `9404` Prometheus, `9999` JMX.

**Tracing is NOT supported for Kafka brokers** — only Connect, MirrorMaker 2, and the HTTP Bridge support OpenTelemetry tracing.

---

## 2. JMX Exporter naming rules (how MBean → Prometheus name)

From the Strimzi `kafka-metrics.yaml` ConfigMap (`lowercaseOutputName: true`):

```
kafka.server<type=T, name=N><>Value      → kafka_server_T_N            (GAUGE)
kafka.server<type=T, name=N><>Count      → kafka_server_T_N_total      (COUNTER)
kafka.<domain><type=T,name=N><>Value     → kafka_<domain>_T_N          (GAUGE)
kafka.<domain><type=T,name=N><>Count     → kafka_<domain>_T_N_total    (COUNTER)
kafka.<domain><...><>50thPercentile      → kafka_<domain>_T_N{quantile="0.50"}
```

So a JMX MBean like `kafka.server:type=ReplicaManager,name=UnderReplicatedPartitions` becomes `kafka_server_replicamanager_underreplicatedpartitions`. The domain segment (`server`, `network`, `controller`, `log`, `coordinator`) is preserved lowercase.

---

## 3. Broker health (KEY — the first metrics to check)

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `kafka_server_replicamanager_underreplicatedpartitions` | Gauge | Partitions with fewer in-sync replicas than configured | **> 0 = replication at risk.** Broker down, slow follower, or network partition. Alert on `> 0` sustained. |
| `kafka_server_replicamanager_underminisr_partitioncount` | Gauge | Partitions below `min.insync.replicas` | **> 0 = producers with acks=all are failing.** Data-availability incident. |
| `kafka_controller_kafkacontroller_offlinepartitionscount` | Gauge | Partitions with no active leader | **> 0 = data unavailable** for those partitions. SEV. |
| `kafka_controller_kafkacontroller_activecontrollercount` | Gauge | 1 on the active controller, 0 elsewhere | **Sum across brokers must = 1.** 0 = no controller (cluster frozen); >1 = split brain. |
| `kafka_controller_kafkacontroller_globalpartitioncount` | Gauge | Total partitions in cluster | Capacity/cardinality tracking |
| `kafka_controller_kafkacontroller_globaltopiccount` | Gauge | Total topics | Growth / runaway topic creation |
| `kafka_server_replicamanager_partitioncount` | Gauge | Partitions hosted on this broker | Load balance across brokers |
| `kafka_server_replicamanager_leadercount` | Gauge | Leader partitions on this broker | Leadership skew → hot broker |
| `kafka_server_replicamanager_isrshrinks_total` | Counter | ISR shrink events | Rate spike = followers falling behind |
| `kafka_server_replicamanager_isrexpands_total` | Counter | ISR expand events | Followers catching up after a shrink |

---

## 4. Throughput & request rates (RED)

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `kafka_server_brokertopicmetrics_bytesin_total` | Counter | Bytes produced into the broker | `rate()` = ingress throughput; per-`topic` label available |
| `kafka_server_brokertopicmetrics_bytesout_total` | Counter | Bytes consumed out of the broker | `rate()` = egress throughput |
| `kafka_server_brokertopicmetrics_messagesin_total` | Counter | Messages produced | Message rate (independent of size) |
| `kafka_server_brokertopicmetrics_totalproducerequests_total` | Counter | Produce requests | Produce QPS |
| `kafka_server_brokertopicmetrics_totalfetchrequests_total` | Counter | Fetch requests | Consumer/replication fetch QPS |
| `kafka_server_brokertopicmetrics_failedproducerequests_total` | Counter | Failed produce requests | **> 0 rate = producers erroring** (auth, quota, ISR) |
| `kafka_server_brokertopicmetrics_failedfetchrequests_total` | Counter | Failed fetch requests | Consumer read failures |
| `kafka_network_requestmetrics_requests_total` | Counter | Requests by `request` type (Produce/Fetch/...) | Break down load by API |
| `kafka_server_brokertopicmetrics_reassignmentbytesin_total` | Counter | Bytes in from partition reassignment | Rebalance/Cruise Control traffic |

---

## 5. Request latency & queue saturation

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `kafka_network_requestmetrics_totaltimems{request,quantile}` | Gauge (percentiles) | End-to-end request time by API | p99 spike = latency incident; break down by phase below |
| `kafka_network_requestmetrics_requestqueuetimems{request}` | Gauge | Time waiting in request queue | High = request handler saturation |
| `kafka_network_requestmetrics_localtimems{request}` | Gauge | Time processing on the leader | High = disk/CPU pressure on leader |
| `kafka_network_requestmetrics_remotetimems{request}` | Gauge | Time waiting on other brokers (replication) | High = slow followers (acks=all) |
| `kafka_network_requestmetrics_responsequeuetimems{request}` | Gauge | Time in response queue | Network processor saturation |
| `kafka_network_requestmetrics_responsesendtimems{request}` | Gauge | Time sending response | Slow/backpressured clients |
| `kafka_server_kafkarequesthandlerpool_requesthandleravgidlepercent` | Gauge (MeanRate) | Fraction of time I/O (request handler) threads are idle | **< 0.2 = saturated** → raise `num.io.threads`. 0 idle = all resources in use. |
| `kafka_network_socketserver_networkprocessoravgidlepercent` | Gauge | Idle fraction of network threads | Low = network-thread bound → raise `num.network.threads` |

---

## 6. Log / storage

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `kafka_log_log_size{topic,partition}` | Gauge | On-disk size of a partition log | Disk growth, retention not kicking in |
| `kafka_log_logmanager_offlinelogdirectorycount` | Gauge | Log dirs marked offline (JBOD disk failure) | **> 0 = a disk failed**; partitions on it are offline |
| `kafka_log_logflushstats_logflushrateandtimems_total` | Counter | Log flush count | Flush pressure |
| `kafka_server_kafkaserver_linux_disk_read_bytes_total` | Counter | Disk read bytes (if OS metrics enabled) | Cold-read pressure (consumers lagging off page cache) |
| `kafka_server_kafkaserver_linux_disk_write_bytes_total` | Counter | Disk write bytes | Write pressure |

---

## 7. KRaft metrics (ZooKeeper-free metadata quorum)

Mapped by dedicated rules in `kafka-metrics.yaml` — three families:

| Metric family / example | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `kafka_server_raftmetrics_current_state{current-state}` | Untyped (value 1) | Raft role of the node (`leader`/`follower`/`candidate`/`observer`) | Confirm exactly one leader in the controller quorum |
| `kafka_server_raftmetrics_current_leader` | Gauge | Node ID of the current quorum leader | Leadership stability; flapping = quorum instability |
| `kafka_server_raftmetrics_current_epoch` | Gauge | Current Raft epoch | Rising fast = repeated elections |
| `kafka_server_raftmetrics_high_watermark` | Gauge | Committed offset high-watermark of the metadata log | Metadata replication progress |
| `kafka_server_raftmetrics_log_end_offset` | Gauge | End offset of the metadata log | Compare to high-watermark for lag |
| `kafka_server_raftmetrics_commit_latency_avg` / `_max` | Gauge / Counter | Metadata commit latency | High = slow metadata propagation |
| `kafka_server_raftchannelmetrics_*` | Gauge/Counter | Low-level Raft network channel metrics | Quorum network health |
| `kafka_server_brokermetadatametrics_*` | Gauge | Broker fetching/applying metadata records | Metadata lag on brokers (KRaft) |

> Rule detail: metrics ending in `-total` or `-max` are typed as COUNTER; `current-state` is emitted as an UNTYPED metric with a `current-state` label and value `1`.

---

## 8. Kafka Exporter — consumer lag (port 9308)

Emitted by the bundled `danielqsj/kafka_exporter` (deployed when `spec.kafkaExporter` is set). These are the **only** consumer-lag metrics — brokers do NOT expose per-group lag via JMX.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kafka_consumergroup_lag` | Gauge | Approx lag of a group on a topic/partition | **Primary consumer-health signal.** Rising monotonically = consumer can't keep up | `consumergroup`, `topic`, `partition` |
| `kafka_consumergroup_lag_sum` | Gauge | Lag summed across all partitions of a topic | Per-group/topic lag at a glance | `consumergroup`, `topic` |
| `kafka_consumergroup_current_offset` | Gauge | Committed offset of the group | Consumer progress | `consumergroup`, `topic`, `partition` |
| `kafka_consumergroup_current_offset_sum` | Gauge | Committed offset summed over partitions | — | `consumergroup`, `topic` |
| `kafka_consumergroup_members` | Gauge | Number of members in the group | Drop to 0 = consumers disconnected | `consumergroup` |
| `kafka_topic_partition_current_offset` | Gauge | Log-end (latest) offset per partition | Write position; `latest - committed = lag` | `topic`, `partition` |
| `kafka_topic_partition_oldest_offset` | Gauge | Earliest available offset | Retention window | `topic`, `partition` |
| `kafka_topic_partitions` | Gauge | Partition count of a topic | — | `topic` |
| `kafka_topic_partition_in_sync_replica` | Gauge | ISR count per partition | `< replicas` = under-replicated | `topic`, `partition` |
| `kafka_topic_partition_under_replicated_partition` | Gauge | 1 if partition under-replicated | Per-partition under-replication | `topic`, `partition` |
| `kafka_topic_partition_leader` | Gauge | Leader broker ID | Leadership map | `topic`, `partition` |
| `kafka_topic_partition_leader_is_preferred` | Gauge | 1 if leader is the preferred replica | 0 = leadership imbalance | `topic`, `partition` |
| `kafka_topic_partition_replicas` | Gauge | Replica count | — | `topic`, `partition` |
| `kafka_brokers` | Gauge | Number of brokers in the cluster | Cluster size | — |
| `kafka_broker_info` | Gauge (=1) | Broker address/id metadata | Join to `id` in other queries | `address`, `id` |

> Kafka Exporter shows `N/A`/no data until consumer groups actually commit offsets (it reads `__consumer_offsets`). No traffic = no lag data.

---

## 9. Strimzi operator metrics (Cluster/Topic/User Operator, port 8080)

Confirmed present in the `strimzi-operators` Grafana dashboard. All carry a `kind` label (`Kafka`, `KafkaTopic`, `KafkaUser`, `KafkaConnect`, `KafkaMirrorMaker2`, `KafkaBridge`, `KafkaNodePool`, `KafkaConnector`, `KafkaRebalance`).

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `strimzi_resources{kind}` | Gauge | Count of custom resources of each kind being managed | Inventory; sudden drop = CRs deleted |
| `strimzi_reconciliations_total{kind}` | Counter | Reconciliations started | Reconcile rate |
| `strimzi_reconciliations_successful_total{kind}` | Counter | Successful reconciliations | Healthy operator baseline |
| `strimzi_reconciliations_failed_total{kind}` | Counter | Failed reconciliations | **> 0 rate = operator can't converge** a resource. Check operator logs. |
| `strimzi_reconciliations_locked_total{kind}` | Counter | Reconciliations skipped due to lock held | High = long-running ops / contention |
| `strimzi_reconciliations_periodical_total{kind}` | Counter | Periodic (timer-triggered) reconciliations | Baseline every `STRIMZI_FULL_RECONCILIATION_INTERVAL_MS` (default 120s) |
| `strimzi_reconciliations_duration_seconds_count` / `_sum` / `_max` | Histogram | Reconciliation duration | Rising `_max` = slow reconciles (API latency, big clusters) |
| `strimzi_certificate_expiration_timestamp_ms{cluster,type,resource_namespace}` | Gauge | Expiry epoch (ms) of Strimzi-managed CAs/certs | **Alert well before expiry** — expired CA = cluster comms break |

### Operator JVM (Micrometer, `container=user-operator|topic-operator|strimzi-cluster-operator`)

| Metric Name | Type | Use |
|---|---|---|
| `jvm_memory_used_bytes{container}` | Gauge | Operator heap/non-heap usage → OOM risk |
| `jvm_gc_pause_seconds_sum{container}` | Counter | GC time; `rate()` high = GC pressure |
| `jvm_gc_pause_seconds_count{container}` | Counter | GC frequency |

---

## 10. Cruise Control (port 9404 via JMX exporter)

Cruise Control metrics are called **sensors**. Enabled when `spec.cruiseControl` is set with `metricsConfig`.

| Metric / sensor | What It Measures | Troubleshooting Use |
|---|---|---|
| `kafka_cruisecontrol_..._balancedness_score` (`balancedness-score`) | How evenly workload is distributed across brokers | Low/declining = cluster imbalance building |
| Anomaly detector sensors | Broker failures, goal violations, disk/metric anomalies | Feed alerts on cluster health blocking optimization |

> Cruise Control computes `balancedness-score` from `anomaly.detection.goals` (in `spec.cruiseControl.config`), which may differ from the `default.goals` used by a `KafkaRebalance`. Full sensor list: Cruise Control wiki (Sensors). Grafana dashboard: `strimzi-cruise-control.json`.

---

## 11. Kafka Connect / MirrorMaker 2 / Bridge

Connect and MM2 expose the standard Kafka Connect JMX metrics via the JMX exporter (naming rule: `kafka_connect_*`). MM2 runs on the Connect framework.

| Metric (Connect / MM2) | What It Measures | Troubleshooting Use |
|---|---|---|
| `kafka_connect_connect_worker_metrics_connector_count` | Connectors on the worker | Deployment inventory |
| `kafka_connect_connect_worker_metrics_task_count` | Running tasks | Compare to configured `tasksMax` |
| `kafka_connect_connect_worker_metrics_connector_failed_task_count` | Failed tasks | **> 0 = connector broken** (also see `KafkaConnector` autoRestart) |
| `kafka_connect_connector_task_metrics_*` (offset commit, batch size) | Per-task throughput/latency | Slow sink/source diagnosis |
| MM2 replication latency / offset-sync latency | Lag between source and target clusters | Cross-cluster replication health (dashboard `strimzi-kafka-mirror-maker-2.json`) |

**HTTP Bridge** exposes HTTP + Kafka client metrics on `9404` (dashboard `strimzi-kafka-bridge.json`); `http.*`/`bridge.*` config is managed by Strimzi.

---

## 12. Troubleshooting quick reference

| Symptom | First metrics to check |
|---|---|
| Producers failing (acks=all) | `underminisr_partitioncount`, `underreplicatedpartitions`, `failedproducerequests_total` |
| Data unavailable / partition offline | `offlinepartitionscount`, `logmanager_offlinelogdirectorycount` |
| No controller / cluster frozen | `sum(activecontrollercount)` (must=1), KRaft `current_state`/`current_leader` |
| High produce/fetch latency | `totaltimems` p99 → split into `requestqueuetimems`/`localtimems`/`remotetimems` |
| Broker "busy" | `requesthandleravgidlepercent` (<0.2), `networkprocessoravgidlepercent` |
| Consumer falling behind | `kafka_consumergroup_lag[_sum]`, `kafka_consumergroup_members` |
| Disk filling | `kafka_log_log_size`, retention config |
| Operator not applying CR changes | `strimzi_reconciliations_failed_total`, `strimzi_reconciliations_duration_seconds_max`, operator logs |
| Cert about to expire | `strimzi_certificate_expiration_timestamp_ms` |

---

## Complements

- `observability/application-metrics-first` — check application/broker metrics (lag, under-replication) before CPU/memory
- `apm-metrics/collector-internal-metrics` — the OTel Collector that receives Connect/MM2/Bridge traces
- `observability/vmalert-configuration` + `sre/alerting-strategy` — turning these into alerts
- Example Grafana dashboards shipped by Strimzi: `strimzi-kafka.json`, `strimzi-kraft.json`, `strimzi-kafka-exporter.json`, `strimzi-cruise-control.json`, `strimzi-operators.json`

## Sources

- Strimzi `examples/metrics/kafka-metrics.yaml` (JMX exporter naming rules, KRaft mapping)
- Strimzi `examples/metrics/grafana-dashboards/strimzi-operators.json` (operator metric names)
- danielqsj/kafka_exporter README (consumer-lag metric names)
- Strimzi "Introducing metrics" deploying guide; Apache Kafka monitoring reference (JMX MBeans)
