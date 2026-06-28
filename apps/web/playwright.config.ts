import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the public BGV reference-form E2E.
 *
 * To run:
 *   pnpm add -D @playwright/test
 *   npx playwright install chromium
 *   E2E_BGV_TOKEN=<uuid> npx playwright test
 *
 * We keep the surface small on purpose — the BGV form is the only public
 * (unauthenticated) page that ships in this product. Authenticated flows
 * are covered by pytest against the API (apps/api/tests/).
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
