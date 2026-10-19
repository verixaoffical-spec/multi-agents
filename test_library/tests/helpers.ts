import { APIRequestContext, expect } from '@playwright/test';

export const ADMIN = { email: 'admin@shoplite.test', password: 'Admin123!' };
export const CUSTOMER = { email: 'customer@shoplite.test', password: 'Customer123!' };

export const bearer = (token: string) => ({ Authorization: `Bearer ${token}` });
export const uniqueEmail = (prefix = 'user') =>
  `${prefix}_${Date.now()}_${Math.floor(Math.random() * 1e6)}@shoplite.test`;

// A brand new customer per test keeps carts and orders isolated, so tests never depend on each other.
export async function newCustomer(request: APIRequestContext) {
  const email = uniqueEmail('cust');
  const password = 'Passw0rd!x';
  const reg = await request.post('/api/auth/register', { data: { email, password, name: 'Test User' } });
  expect(reg.status()).toBe(201);
  const login = await request.post('/api/auth/login', { data: { email, password } });
  expect(login.status()).toBe(200);
  const { token, user } = await login.json();
  return { email, password, token: token as string, id: user.id as number };
}

export async function adminToken(request: APIRequestContext) {
  const res = await request.post('/api/auth/login', { data: ADMIN });
  expect(res.status()).toBe(200);
  return (await res.json()).token as string;
}

// Fresh product with a known price and stock, so stock tests do not touch the seeded catalogue.
export async function createProduct(
  request: APIRequestContext,
  overrides: Record<string, unknown> = {},
) {
  const token = await adminToken(request);
  const res = await request.post('/api/products', {
    headers: bearer(token),
    data: { name: `Test Item ${Date.now()}`, price: 1000, stock: 5, category: 'test', ...overrides },
  });
  expect(res.status()).toBe(201);
  return (await res.json()) as { id: number; name: string; price: number; stock: number; category: string };
}
