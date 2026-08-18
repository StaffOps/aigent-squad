import http from 'k6/http';
import { check, sleep } from 'k6';

/**
 * Stress/burst test for aigent-squad gateway.
 * Ramps 0→200 VUs in 30s, sustains 2 min, ramps down in 30s.
 * Validates graceful degradation under extreme load.
 */

export const options = {
  stages: [
    { duration: '30s', target: 200 },  // ramp up to 200 VUs
    { duration: '2m', target: 200 },   // sustain peak
    { duration: '30s', target: 0 },    // ramp down
  ],
  thresholds: {
    http_req_duration: ['p(99)<30000'], // p99 < 30s (lenient — stress test)
    http_req_failed: ['rate<0.3'],      // <30% error rate (expect some under stress)
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-load-key';

export function setup() {
  const res = http.get(`${BASE_URL}/health`);
  check(res, {
    'health check returns 200': (r) => r.status === 200,
  });
  if (res.status !== 200) {
    throw new Error(`Gateway not healthy: status=${res.status}`);
  }
  return { baseUrl: BASE_URL };
}

export default function (data) {
  const payload = JSON.stringify({
    query: 'What pods are running in the monitoring namespace?',
    user_id: 'loadtest-user',
    session_id: `load-${__VU}-${__ITER}`,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${API_KEY}`,
    },
  };

  const res = http.post(`${data.baseUrl}/query`, payload, params);

  check(res, {
    'status is 2xx or 429': (r) => r.status === 200 || r.status === 429,
  });

  sleep(0.5);
}
