// FEATURE NOT DEPLOYED YET. These describe coupon behaviour that the demo PRs add (see PR_CATALOG.md).
// On the base build they FAIL because the feature does not exist. Verixa should label them
// deployment_pending, and they go green once the coupon PR is merged and deployed to staging.
import { test, expect } from '@playwright/test';
import { bearer, newCustomer } from './helpers';

test('PENDING: coupon endpoint lists active coupons', async ({ request }) => {
  const res = await request.get('/api/coupons');
  expect(res.status()).toBe(200); // actual: 404
});

test('PENDING: coupon SAVE10 takes 10 percent off the order', async ({ request }) => {
  const user = await newCustomer(request);
  await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: 2, quantity: 1 } }); // 7999
  const res = await request.post('/api/orders', { headers: bearer(user.token), data: { coupon: 'SAVE10' } });
  expect(res.status()).toBe(201);
  const order = await res.json();
  expect(order.totals.discount).toBe(800); // actual: 0, the coupon field is ignored
  expect(order.totals.total).toBe(7199);
});

test('PENDING: an unknown coupon code is rejected', async ({ request }) => {
  const user = await newCustomer(request);
  await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: 3, quantity: 1 } });
  const res = await request.post('/api/orders', { headers: bearer(user.token), data: { coupon: 'NOT_A_CODE' } });
  expect(res.status()).toBe(400); // actual: 201, the code is silently ignored
});
