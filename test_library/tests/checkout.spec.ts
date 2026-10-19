// Sample spec. NOTE: paths and payloads are assumptions, edit them to match the real Shoplite API.
import { test, expect } from '@playwright/test';

test('checkout without a coupon still works', async ({ request }) => {
  const res = await request.post('/api/checkout', {
    data: { items: [{ sku: 'A1', qty: 2 }] },
  });
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.total).toBeGreaterThan(0);
});
