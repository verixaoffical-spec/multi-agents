// ShopLite auth module. Every test here should pass on a healthy build.
import { test, expect } from '@playwright/test';
import { CUSTOMER, bearer, newCustomer, uniqueEmail } from './helpers';

test('health endpoint reports ok', async ({ request }) => {
  const res = await request.get('/health');
  expect(res.status()).toBe(200);
  expect(await res.json()).toEqual({ status: 'ok' });
});

test('register creates a customer and never leaks the password hash', async ({ request }) => {
  const email = uniqueEmail('reg');
  const res = await request.post('/api/auth/register', {
    data: { email, password: 'Passw0rd!x', name: 'New User' },
  });
  expect(res.status()).toBe(201);
  const user = await res.json();
  expect(user.email).toBe(email);
  expect(user.role).toBe('customer');
  expect(user.passwordHash).toBeUndefined();
  expect(user.password).toBeUndefined();
});

test('register ignores an attempt to set the role to admin', async ({ request }) => {
  const res = await request.post('/api/auth/register', {
    data: { email: uniqueEmail('sneaky'), password: 'Passw0rd!x', role: 'admin' },
  });
  expect(res.status()).toBe(201);
  expect((await res.json()).role).toBe('customer');
});

test('register rejects an invalid email', async ({ request }) => {
  const res = await request.post('/api/auth/register', { data: { email: 'not-an-email', password: 'Passw0rd!x' } });
  expect(res.status()).toBe(400);
});

test('register rejects a password shorter than 8 characters', async ({ request }) => {
  const res = await request.post('/api/auth/register', { data: { email: uniqueEmail(), password: 'short' } });
  expect(res.status()).toBe(400);
});

test('register rejects a duplicate email', async ({ request }) => {
  const res = await request.post('/api/auth/register', { data: { email: CUSTOMER.email, password: 'Passw0rd!x' } });
  expect(res.status()).toBe(409);
});

test('login returns a token and the public user', async ({ request }) => {
  const res = await request.post('/api/auth/login', { data: CUSTOMER });
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.token).toContain('.');
  expect(body.user.email).toBe(CUSTOMER.email);
  expect(body.user.passwordHash).toBeUndefined();
});

test('login with a wrong password is rejected', async ({ request }) => {
  const res = await request.post('/api/auth/login', { data: { ...CUSTOMER, password: 'wrong-password' } });
  expect(res.status()).toBe(401);
});

test('login with an unknown email is rejected', async ({ request }) => {
  const res = await request.post('/api/auth/login', { data: { email: 'nobody@shoplite.test', password: 'Passw0rd!x' } });
  expect(res.status()).toBe(401);
});

test('me returns the logged in user', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.get('/api/auth/me', { headers: bearer(user.token) });
  expect(res.status()).toBe(200);
  expect((await res.json()).email).toBe(user.email);
});

test('me without a token is rejected', async ({ request }) => {
  expect((await request.get('/api/auth/me')).status()).toBe(401);
});

test('a tampered token is rejected', async ({ request }) => {
  const user = await newCustomer(request);
  const res = await request.get('/api/auth/me', { headers: bearer(user.token + 'x') });
  expect(res.status()).toBe(401);
});

test('a garbage token is rejected', async ({ request }) => {
  const res = await request.get('/api/auth/me', { headers: bearer('abc.def') });
  expect(res.status()).toBe(401);
});

test('an invalid JSON body returns 400', async ({ request }) => {
  const res = await request.post('/api/auth/login', {
    headers: { 'Content-Type': 'application/json' },
    data: Buffer.from('{ this is not json'),   // Buffer = sent as raw bytes, not re-encoded as JSON
  });
  expect(res.status()).toBe(400);
});
