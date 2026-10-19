// BUGGY BUILD CHECKS. Each test asserts the CORRECT behaviour. ShopLite currently gets these wrong,
// so they are EXPECTED TO FAIL. Verixa should label them real_bug. When a fix lands, they turn green.
import { test, expect } from '@playwright/test';
import { adminToken, bearer, createProduct, newCustomer, uniqueEmail } from './helpers';

test('BUG: checkout should reduce product stock', async ({ request }) => {
  const user = await newCustomer(request);
  const product = await createProduct(request, { stock: 5 });
  await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: product.id, quantity: 2 } });
  expect((await request.post('/api/orders', { headers: bearer(user.token), data: {} })).status()).toBe(201);
  const after = await (await request.get(`/api/products/${product.id}`)).json();
  expect(after.stock).toBe(3); // actual: still 5, stock is never decremented
});

test('BUG: the last unit cannot be sold twice (overselling)', async ({ request }) => {
  const product = await createProduct(request, { stock: 1 });
  const a = await newCustomer(request);
  const b = await newCustomer(request);
  await request.post('/api/cart/items', { headers: bearer(a.token), data: { productId: product.id, quantity: 1 } });
  await request.post('/api/cart/items', { headers: bearer(b.token), data: { productId: product.id, quantity: 1 } });
  const first = await request.post('/api/orders', { headers: bearer(a.token), data: {} });
  const second = await request.post('/api/orders', { headers: bearer(b.token), data: {} });
  expect(first.status()).toBe(201);
  expect(second.status()).toBe(409); // actual: 201, both customers got the single item
});

test('BUG: emails are case insensitive, so a different case is a duplicate', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.post('/api/auth/register', {
    data: { email: user.email.toUpperCase(), password: 'Passw0rd!x' },
  });
  expect(res.status()).toBe(409); // actual: 201, a second account for the same address
});

test('BUG: login works whatever the email casing', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.post('/api/auth/login', {
    data: { email: user.email.toUpperCase(), password: user.password },
  });
  expect(res.status()).toBe(200); // actual: 401
});

test('BUG: a non string email returns 400, not a server error', async ({ request }) => {
  const res = await request.post('/api/auth/register', { data: { email: 12345, password: 'Passw0rd!x' } });
  expect(res.status()).toBe(400); // actual: 500 (email.includes is not a function)
});

test('BUG: a JSON null body returns a client error, not a server error', async ({ request }) => {
  const res = await request.post('/api/auth/login', {
    headers: { 'Content-Type': 'application/json' },
    data: Buffer.from('null'),
  });
  expect(res.status()).toBeLessThan(500); // actual: 500 (destructuring null)
});

test('BUG: a product cannot be created with negative stock', async ({ request }) => {
  const token = await adminToken(request);
  const res = await request.post('/api/products', {
    headers: bearer(token),
    data: { name: `Negative ${Date.now()}`, price: 500, stock: -10 },
  });
  expect(res.status()).toBe(400); // actual: 201
});

test('BUG: a product name of only spaces is rejected', async ({ request }) => {
  const token = await adminToken(request);
  const res = await request.post('/api/products', { headers: bearer(token), data: { name: '   ', price: 500 } });
  expect(res.status()).toBe(400); // actual: 201
});

test('BUG: a product id written as 1e0 is not a valid id', async ({ request }) => {
  const res = await request.get('/api/products/1e0');
  expect(res.status()).toBe(404); // actual: 200, Number("1e0") is 1
});

test('BUG: a registered password of only spaces is rejected', async ({ request }) => {
  const res = await request.post('/api/auth/register', { data: { email: uniqueEmail('blank'), password: '        ' } });
  expect(res.status()).toBe(400); // actual: 201
});
