import http from 'k6/http';
import { check, sleep } from 'k6';

/**
 * Basic load test for aigent-squad gateway.
 * 50 VUs, 2 min duration — validates steady-state latency under moderate load.
 */

export const options = {
  vus: 50,
  duration: '2m',
  thresholds: {
    http_req_duration: ['p(95)<10000'], // p95 < 10s (LLM responses are slow)
    http_req_failed: ['rate<0.1'],      // <10% error rate
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-load-key';

export function setup() {
  // Health check — abort run if gateway is not ready
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
    'status is 200': (r) => r.status === 200,
    'response has body': (r) => r.body && r.body.length > 0,
  });

  // Throttle slightly to avoid pure burst behavior in the basic scenario
  sleep(1);
}
