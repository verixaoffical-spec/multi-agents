import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: 'tests/**/*.spec.ts',   // drafts/ is never run until a human approves it
  timeout: 30000,
  retries: 0,
  use: {
    baseURL: process.env.BASE_URL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
