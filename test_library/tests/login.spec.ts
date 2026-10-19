// Sample spec. NOTE: paths and payloads are assumptions, edit them to match the real Shoplite API.
import { test, expect } from '@playwright/test';

test('login returns a token', async ({ request }) => {
  const res = await request.post('/api/login', {
    data: { username: 'demo', password: 'demo123' },
  });
  expect(res.status()).toBe(200);
  expect((await res.json()).token).toBeTruthy();
});

test('tampered token is rejected', async ({ request }) => {
  const login = await request.post('/api/login', { data: { username: 'demo', password: 'demo123' } });
  const { token } = await login.json();
  const res = await request.get('/api/orders', { headers: { Authorization: `Bearer ${token}x` } });
  expect(res.status()).toBe(401);
});
