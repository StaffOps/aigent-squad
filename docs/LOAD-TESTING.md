# Load Testing

Performance validation for aigent-squad using [k6](https://k6.io/).

## Prerequisites

**Option A** — k6 installed locally:
```bash
# macOS
brew install k6

# Linux (Debian/Ubuntu)
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
  --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D68
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | \
  sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6
```

**Option B** — via Docker (no install):
```bash
docker run --rm -i --network=host grafana/k6:latest run - < tests/load/scenario_basic.js
```

## Running Tests

### Basic scenario (steady state)

50 VUs for 2 minutes — validates normal operating conditions:

```bash
# Via make (recommended)
make load-test

# Direct
k6 run tests/load/scenario_basic.js

# With custom env
k6 run -e BASE_URL=http://gateway.staging:8000 -e API_KEY=my-key tests/load/scenario_basic.js
```

### Burst scenario (stress test)

Ramps to 200 VUs — validates graceful degradation:

```bash
k6 run tests/load/scenario_burst.js
```

## Interpreting Results

k6 prints a summary after each run:

```
http_req_duration..............: avg=2345ms  min=180ms  med=1890ms  max=9800ms  p(90)=4500ms  p(95)=6200ms  p(99)=8900ms
http_req_failed................: 3.2%  ✓ 48 ✗ 1452
```

### Thresholds

| Scenario | Metric | Pass | Fail |
|----------|--------|------|------|
| Basic | `http_req_duration p(95)` | < 10s | ≥ 10s |
| Basic | `http_req_failed rate` | < 10% | ≥ 10% |
| Burst | `http_req_duration p(99)` | < 30s | ≥ 30s |
| Burst | `http_req_failed rate` | < 30% | ≥ 30% |

### What "pass" means

- **Basic pass**: The system handles 50 concurrent users comfortably within SLA
- **Burst pass**: The system degrades gracefully under 4× expected load (rate limiter returns 429 rather than crashing)

### Common failure patterns

| Symptom | Likely cause |
|---------|--------------|
| p95 > 10s (basic) | Bedrock throttling or semaphore contention |
| Error rate > 10% (basic) | Rate limiter too aggressive or gateway crash |
| All requests timeout (burst) | Circuit breaker tripped, no recovery |
| 429s dominate (burst) | Expected — rate limiter protecting backend |

## CI Workflow

The load test runs via **manual trigger** only (not on every PR):

```bash
# Trigger from GitHub CLI
gh workflow run load.yml -f scenario=basic
gh workflow run load.yml -f scenario=burst
```

The workflow spins up docker-compose, waits for health, runs k6, and tears down.

## Baseline Results

> **Placeholder** — actual numbers will be recorded after the first production-like run.

| Scenario | Date | p50 | p95 | p99 | Error % | Notes |
|----------|------|-----|-----|-----|---------|-------|
| basic | _TBD_ | — | — | — | — | First run pending |
| burst | _TBD_ | — | — | — | — | First run pending |

Update this table after each significant infrastructure change.
