// ShopLite orders module. Every test here should pass on a healthy build.
import { test, expect } from '@playwright/test';
import { bearer, newCustomer } from './helpers';

async function addToCart(request: any, token: string, productId: number, quantity: number) {
  const res = await request.post('/api/cart/items', { headers: bearer(token), data: { productId, quantity } });
  expect(res.status()).toBe(201);
}

test('order endpoints require a login', async ({ request }) => {
  expect((await request.post('/api/orders', { data: {} })).status()).toBe(401);
  expect((await request.get('/api/orders')).status()).toBe(401);
  expect((await request.get('/api/orders/1')).status()).toBe(401);
});

test('checkout with an empty cart is rejected', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.post('/api/orders', { headers: bearer(user.token), data: {} });
  expect(res.status()).toBe(400);
});

test('checkout creates a PLACED order with correct totals', async ({ request }) => {
  const user = await newCustomer(request);
  await addToCart(request, user.token, 1, 2); // 2 x 2499
  await addToCart(request, user.token, 4, 1); // 349
  const res = await request.post('/api/orders', { headers: bearer(user.token), data: {} });
  expect(res.status()).toBe(201);
  const order = await res.json();
  expect(order.status).toBe('PLACED');
  expect(order.userId).toBe(user.id);
  expect(order.items).toHaveLength(2);
  expect(order.totals.subtotal).toBe(2499 * 2 + 349);
  expect(order.totals.total).toBe(order.totals.subtotal + order.totals.shipping - order.totals.discount);
});

test('the cart is empty after checkout', async ({ request }) => {
  const user = await newCustomer(request);
  await addToCart(request, user.token, 3, 1);
  await request.post('/api/orders', { headers: bearer(user.token), data: {} });
  const cart = await (await request.get('/api/cart', { headers: bearer(user.token) })).json();
  expect(cart).toEqual({ items: [], subtotal: 0 });
});

test('order history lists only my orders', async ({ request }) => {
  const a = await newCustomer(request);
  const b = await newCustomer(request);
  await addToCart(request, a.token, 3, 1);
  await request.post('/api/orders', { headers: bearer(a.token), data: {} });
  const listA = await (await request.get('/api/orders', { headers: bearer(a.token) })).json();
  const listB = await (await request.get('/api/orders', { headers: bearer(b.token) })).json();
  expect(listA).toHaveLength(1);
  expect(listB).toEqual([]);
});

test('an order can be fetched by id', async ({ request }) => {
  const user = await newCustomer(request);
  await addToCart(request, user.token, 5, 1);
  const order = await (await request.post('/api/orders', { headers: bearer(user.token), data: {} })).json();
  const res = await request.get(`/api/orders/${order.id}`, { headers: bearer(user.token) });
  expect(res.status()).toBe(200);
  expect((await res.json()).id).toBe(order.id);
});

test('another customer cannot read my order', async ({ request }) => {
  const a = await newCustomer(request);
  const b = await newCustomer(request);
  await addToCart(request, a.token, 6, 1);
  const order = await (await request.post('/api/orders', { headers: bearer(a.token), data: {} })).json();
  const res = await request.get(`/api/orders/${order.id}`, { headers: bearer(b.token) });
  expect(res.status()).toBe(404);
});

test('unknown order id returns 404', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.get('/api/orders/99999', { headers: bearer(user.token) });
  expect(res.status()).toBe(404);
});

test('order keeps the price from the time of purchase', async ({ request }) => {
  const user = await newCustomer(request);
  await addToCart(request, user.token, 1, 1);
  const order = await (await request.post('/api/orders', { headers: bearer(user.token), data: {} })).json();
  expect(order.items[0]).toMatchObject({ productId: 1, price: 2499, quantity: 1, lineTotal: 2499 });
});
