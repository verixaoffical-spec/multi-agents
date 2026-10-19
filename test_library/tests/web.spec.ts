// ShopLite demo page (browser tests). Needs: npx playwright install chromium
import { test, expect } from '@playwright/test';
import { newCustomer } from './helpers';

test('home page lists the products', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'ShopLite' })).toBeVisible();
  await expect(page.getByTestId('product_list').locator('li')).toHaveCount(6, { timeout: 10000 });
  await expect(page.getByTestId('product_1')).toContainText('Wireless Mouse');
});

test('guest is asked to log in before adding to cart', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('add_to_cart_1').click();
  await expect(page.getByTestId('message')).toHaveText('Please login first');
});

test('a wrong password shows an error', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('email_input').fill('customer@shoplite.test');
  await page.getByTestId('password_input').fill('wrong-password');
  await page.getByTestId('login_button').click();
  await expect(page.getByTestId('message')).toHaveText('Invalid credentials');
});

test('customer logs in, fills the cart and checks out', async ({ page, request }) => {
  const user = await newCustomer(request); // fresh account, so the cart always starts empty
  await page.goto('/');
  await page.getByTestId('email_input').fill(user.email);
  await page.getByTestId('password_input').fill(user.password);
  await page.getByTestId('login_button').click();
  await expect(page.getByTestId('user_label')).toHaveText(user.email);

  await page.getByTestId('add_to_cart_1').click();
  await expect(page.getByTestId('cart_total')).toHaveText('24.99');
  await page.getByTestId('add_to_cart_1').click();
  await expect(page.getByTestId('cart_total')).toHaveText('49.98');

  await page.getByTestId('checkout_button').click();
  await expect(page.getByTestId('message')).toContainText('Order placed');
  await expect(page.getByTestId('cart_total')).toHaveText('0.00');
});

test('checkout with an empty cart shows an error', async ({ page, request }) => {
  const user = await newCustomer(request);
  await page.goto('/');
  await page.getByTestId('email_input').fill(user.email);
  await page.getByTestId('password_input').fill(user.password);
  await page.getByTestId('login_button').click();
  await expect(page.getByTestId('user_label')).toHaveText(user.email);
  await page.getByTestId('checkout_button').click();
  await expect(page.getByTestId('message')).toHaveText('Cart is empty');
});

test('static files cannot escape the public folder', async ({ request }) => {
  for (const p of ['/..%2fpackage.json', '/%2e%2e/package.json', '/../src/server.js']) {
    const res = await request.get(p);
    expect(res.status()).not.toBe(200);
  }
});
