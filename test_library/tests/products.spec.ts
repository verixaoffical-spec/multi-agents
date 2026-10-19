// ShopLite products module. Every test here should pass on a healthy build.
import { test, expect } from '@playwright/test';
import { adminToken, bearer, newCustomer } from './helpers';

test('product list returns the seeded catalogue with the expected fields', async ({ request }) => {
  const res = await request.get('/api/products');
  expect(res.status()).toBe(200);
  const all = await res.json();
  const list = all.filter((p: any) => p.id <= 6); // seeded items only, other tests add products too
  expect(list).toHaveLength(6);
  for (const p of list) {
    expect(typeof p.id).toBe('number');
    expect(p.name).toBeTruthy();
    expect(Number.isInteger(p.price)).toBe(true);
    expect(p.price).toBeGreaterThan(0);
    expect(p.stock).toBeGreaterThanOrEqual(0);
  }
});

test('category filter only returns that category', async ({ request }) => {
  const list = await (await request.get('/api/products?category=electronics')).json();
  expect(list.length).toBeGreaterThan(0);
  expect(list.every((p: any) => p.category === 'electronics')).toBe(true);
});

test('unknown category returns an empty list', async ({ request }) => {
  const res = await request.get('/api/products?category=does_not_exist');
  expect(res.status()).toBe(200);
  expect(await res.json()).toEqual([]);
});

test('get product by id returns the wireless mouse', async ({ request }) => {
  const res = await request.get('/api/products/1');
  expect(res.status()).toBe(200);
  const p = await res.json();
  expect(p.name).toBe('Wireless Mouse');
  expect(p.price).toBe(2499);
});

test('unknown product id returns 404', async ({ request }) => {
  expect((await request.get('/api/products/99999')).status()).toBe(404);
});

test('non numeric product id returns 404', async ({ request }) => {
  expect((await request.get('/api/products/abc')).status()).toBe(404);
});

test('creating a product without a token is rejected', async ({ request }) => {
  const res = await request.post('/api/products', { data: { name: 'X', price: 100 } });
  expect(res.status()).toBe(401);
});

test('a customer cannot create a product', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.post('/api/products', { headers: bearer(user.token), data: { name: 'X', price: 100 } });
  expect(res.status()).toBe(403);
});

test('an admin can create a product and it can be fetched', async ({ request }) => {
  const token = await adminToken(request);
  const res = await request.post('/api/products', {
    headers: bearer(token),
    data: { name: 'Standing Desk', price: 29900, stock: 4, category: 'home' },
  });
  expect(res.status()).toBe(201);
  const created = await res.json();
  const fetched = await (await request.get(`/api/products/${created.id}`)).json();
  expect(fetched).toMatchObject({ name: 'Standing Desk', price: 29900, stock: 4, category: 'home' });
});

test('new product defaults stock to 0 and category to general', async ({ request }) => {
  const token = await adminToken(request);
  const res = await request.post('/api/products', { headers: bearer(token), data: { name: 'Plain', price: 100 } });
  expect(res.status()).toBe(201);
  expect(await res.json()).toMatchObject({ stock: 0, category: 'general' });
});

for (const bad of [0, -5, 10.5, '10']) {
  test(`creating a product with price ${JSON.stringify(bad)} is rejected`, async ({ request }) => {
    const token = await adminToken(request);
    const res = await request.post('/api/products', { headers: bearer(token), data: { name: 'Bad', price: bad } });
    expect(res.status()).toBe(400);
  });
}

test('creating a product without a name is rejected', async ({ request }) => {
  const token = await adminToken(request);
  const res = await request.post('/api/products', { headers: bearer(token), data: { price: 100 } });
  expect(res.status()).toBe(400);
});
