// BUGS IN THE TESTS THEMSELVES, not in ShopLite. These exist to exercise Verixa's failure classifier.
import { test, expect } from '@playwright/test';
import { bearer, newCustomer } from './helpers';

test('SLOW: waits longer than its own timeout', async ({ request }) => {
  test.setTimeout(3000);                                   // Verixa should label this: timeout
  await request.get('/health');
  await new Promise((resolve) => setTimeout(resolve, 6000));
  expect(true).toBe(true);
});

test('FLAKY: passes about half of the time', async ({ request }) => {
  const res = await request.get('/api/products');          // Verixa should label this: needs_review
  expect(res.status()).toBe(200);
  expect(Math.random()).toBeLessThan(0.5);
});

test('WRONG EXPECTATION: price is compared in dollars, the API uses cents', async ({ request }) => {
  const p = await (await request.get('/api/products/1')).json();
  expect(p.price).toBe(24.99);                             // wrong: the API returns 2499 (integer cents)
});

test('WRONG EXPECTATION: checkout is expected to return 200, it returns 201', async ({ request }) => {
  const user = await newCustomer(request);
  await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: 4, quantity: 1 } });
  const res = await request.post('/api/orders', { headers: bearer(user.token), data: {} });
  expect(res.status()).toBe(200);                          // wrong: ShopLite returns 201 Created
});

test('ORDER DEPENDENT: assumes a previous test already created order #1', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.get('/api/orders/1', { headers: bearer(user.token) });
  expect(res.status()).toBe(200);                          // wrong: orders are private, a new user gets 404
});
