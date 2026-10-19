// ShopLite cart module. Every test here should pass on a healthy build.
import { test, expect } from '@playwright/test';
import { bearer, createProduct, newCustomer } from './helpers';

test('cart endpoints require a login', async ({ request }) => {
  expect((await request.get('/api/cart')).status()).toBe(401);
  expect((await request.post('/api/cart/items', { data: { productId: 1, quantity: 1 } })).status()).toBe(401);
  expect((await request.delete('/api/cart/items/1')).status()).toBe(401);
});

test('a new customer has an empty cart', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.get('/api/cart', { headers: bearer(user.token) });
  expect(res.status()).toBe(200);
  expect(await res.json()).toEqual({ items: [], subtotal: 0 });
});

test('adding an item returns the line total and subtotal', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: 1, quantity: 2 } });
  expect(res.status()).toBe(201);
  const cart = await res.json();
  expect(cart.items).toHaveLength(1);
  expect(cart.items[0]).toMatchObject({ productId: 1, name: 'Wireless Mouse', price: 2499, quantity: 2, lineTotal: 4998 });
  expect(cart.subtotal).toBe(4998);
});

test('adding the same product twice merges the quantity', async ({ request }) => {
  const user = await newCustomer(request);
  await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: 3, quantity: 1 } });
  const res = await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: 3, quantity: 2 } });
  const cart = await res.json();
  expect(cart.items).toHaveLength(1);
  expect(cart.items[0].quantity).toBe(3);
  expect(cart.subtotal).toBe(799 * 3);
});

test('subtotal adds up across different products', async ({ request }) => {
  const user = await newCustomer(request);
  await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: 1, quantity: 1 } }); // 2499
  const res = await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: 4, quantity: 2 } }); // 349 x 2
  expect((await res.json()).subtotal).toBe(2499 + 698);
});

for (const bad of [0, -1, 1.5, '2']) {
  test(`quantity ${JSON.stringify(bad)} is rejected`, async ({ request }) => {
    const user = await newCustomer(request);
    const res = await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: 1, quantity: bad } });
    expect(res.status()).toBe(400);
  });
}

test('adding an unknown product returns 404', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: 99999, quantity: 1 } });
  expect(res.status()).toBe(404);
});

test('adding more than the available stock returns 409', async ({ request }) => {
  const user = await newCustomer(request);
  const product = await createProduct(request, { stock: 3 });
  const ok = await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: product.id, quantity: 3 } });
  expect(ok.status()).toBe(201);
  const tooMany = await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: product.id, quantity: 1 } });
  expect(tooMany.status()).toBe(409);
});

test('removing an item empties the cart', async ({ request }) => {
  const user = await newCustomer(request);
  await request.post('/api/cart/items', { headers: bearer(user.token), data: { productId: 2, quantity: 1 } });
  const res = await request.delete('/api/cart/items/2', { headers: bearer(user.token) });
  expect(res.status()).toBe(200);
  expect(await res.json()).toEqual({ items: [], subtotal: 0 });
});

test('removing an item that is not in the cart returns 404', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.delete('/api/cart/items/2', { headers: bearer(user.token) });
  expect(res.status()).toBe(404);
});

test('carts are private to each customer', async ({ request }) => {
  const a = await newCustomer(request);
  const b = await newCustomer(request);
  await request.post('/api/cart/items', { headers: bearer(a.token), data: { productId: 1, quantity: 1 } });
  const cartB = await (await request.get('/api/cart', { headers: bearer(b.token) })).json();
  expect(cartB.items).toEqual([]);
});
