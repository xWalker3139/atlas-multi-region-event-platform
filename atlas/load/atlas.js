import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';
import exec from 'k6/execution';

const rate = Number(__ENV.EVENT_RATE || 1000);
const batch = 100;
const accepted = new Counter('accepted_unique_events');
const duplicateChecks = new Counter('duplicate_request_checks');
const runId = __ENV.RUN_ID;
if (!runId || !__ENV.ATLAS_TOKEN || !__ENV.ATLAS_URL) {
  throw new Error('RUN_ID, ATLAS_TOKEN, ATLAS_URL are required');
}
export const options = {
  scenarios: { orders: {
    executor: 'constant-arrival-rate', rate: rate / batch, timeUnit: '1s',
    duration: __ENV.DURATION || '10m', preAllocatedVUs: 30, maxVUs: 150,
  } },
  thresholds: {
    http_req_failed: ['rate<0.01'], checks: ['rate>0.99'],
    dropped_iterations: ['count==0'], http_req_duration: ['p(95)<1500'],
  },
};
export default function () {
  const iteration = exec.scenario.iterationInTest;
  const commands = Array.from({ length: batch }, (_, n) => ({
    idempotency_key: `${runId}:${iteration}:${n}`,
    customer_id: 'synthetic-k6', amount_cents: 100 + n,
  }));
  const params = { headers: { 'content-type': 'application/json', 'x-atlas-token': __ENV.ATLAS_TOKEN } };
  const result = http.post(`${__ENV.ATLAS_URL}/orders/batch`, JSON.stringify(commands), params);
  const ok = check(result, { '202 and full receipts': r => r.status === 202 && r.json('receipts').length === batch });
  if (ok) accepted.add(batch);
  if (iteration % 10 === 0 && ok) {
    const repeated = http.post(`${__ENV.ATLAS_URL}/orders/batch`, JSON.stringify(commands), params);
    check(repeated, { 'duplicate request keeps identities': r => r.status === 202 &&
      r.json('receipts').every((x, i) => x.duplicate && x.event_id === result.json('receipts')[i].event_id) });
    duplicateChecks.add(1);
  }
}
// Performance profile only. tools/load.py persists receipts for zero-loss auditing.
