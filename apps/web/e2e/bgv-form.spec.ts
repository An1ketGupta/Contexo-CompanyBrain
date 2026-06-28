/**
 * E2E coverage for the public BGV reference form.
 *
 * This file is a Playwright spec — to run:
 *
 *     cd apps/web
 *     pnpm add -D @playwright/test
 *     npx playwright install chromium
 *     npx playwright test e2e/bgv-form.spec.ts
 *
 * The spec exercises both happy and unhappy paths against a running dev
 * stack (web + api). It uses a TEST_TOKEN env var that points to a real
 * BGV token created via:
 *
 *     -- run in supabase SQL editor
 *     INSERT INTO onboarding_bgv_references (org_id, run_id, reference_name,
 *       reference_email, status, token_expires_at)
 *     VALUES ('<org>', '<run>', 'Test Reference', 'test@example.com',
 *             'sent', now() + interval '7 days')
 *     RETURNING token;
 *
 * We deliberately keep these tests against the dev server rather than mocking
 * fetches — the value of an E2E test is exercising the wire format end-to-end.
 */
import { expect, test } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";
const TEST_TOKEN = process.env.E2E_BGV_TOKEN ?? "";
const EXPIRED_TOKEN = process.env.E2E_BGV_EXPIRED_TOKEN ?? "";

test.describe("BGV public form", () => {
  test.skip(!TEST_TOKEN, "E2E_BGV_TOKEN not set — skipping E2E");

  test("loads and shows candidate context", async ({ page }) => {
    await page.goto(`${BASE_URL}/bgv/${TEST_TOKEN}`);

    // Heading mentions the candidate name from the prefill.
    await expect(page.getByRole("heading", { name: /Reference check for/i })).toBeVisible();
    // The 5 required form controls.
    await expect(page.getByLabel(/How long did you work with/i)).toBeVisible();
    await expect(page.getByLabel(/What was their role/i)).toBeVisible();
    await expect(page.getByText(/Would you recommend hiring them\?/i)).toBeVisible();
    await expect(page.getByLabel(/Strengths/i)).toBeVisible();
    await expect(page.getByLabel(/Any concerns/i)).toBeVisible();
  });

  test("rejects submit when required fields missing", async ({ page }) => {
    await page.goto(`${BASE_URL}/bgv/${TEST_TOKEN}`);
    await page.getByRole("button", { name: /Submit response/i }).click();
    // Inline validation surfaces "Required" / "Pick one".
    await expect(page.getByText(/Required/i).first()).toBeVisible();
  });

  test("submits a happy-path response and shows confirmation", async ({ page }) => {
    await page.goto(`${BASE_URL}/bgv/${TEST_TOKEN}`);
    await page.getByLabel(/How long did you work with/i).fill("18");
    await page.getByLabel(/What was their role/i).fill("They led the API platform team.");
    await page.getByRole("button", { name: "yes", exact: false }).click();
    await page.getByLabel(/Strengths/i).fill("Sharp on systems design; excellent mentor.");
    await page.getByLabel(/Any concerns/i).fill("Tends to over-engineer early.");

    await page.getByRole("button", { name: /Submit response/i }).click();
    await expect(page.getByText(/Thanks,/i)).toBeVisible({ timeout: 10_000 });
  });

  test("re-opening after submit shows the already-submitted screen", async ({ page }) => {
    // Reload — the GET response should mark already_submitted=true since
    // the previous test already filled it.
    await page.goto(`${BASE_URL}/bgv/${TEST_TOKEN}`);
    await expect(page.getByText(/Your response has been recorded/i)).toBeVisible();
  });

  test("malformed token shows a friendly error", async ({ page }) => {
    await page.goto(`${BASE_URL}/bgv/not-a-uuid`);
    await expect(
      page.getByRole("heading", { name: /couldn.t open this reference check/i })
    ).toBeVisible();
  });

  test("unknown token shows a friendly error", async ({ page }) => {
    await page.goto(`${BASE_URL}/bgv/00000000-0000-0000-0000-000000000000`);
    await expect(
      page.getByRole("heading", { name: /couldn.t open this reference check/i })
    ).toBeVisible();
  });
});

test.describe("BGV expired token", () => {
  test.skip(!EXPIRED_TOKEN, "E2E_BGV_EXPIRED_TOKEN not set — skipping");

  test("expired token shows the closed-out screen", async ({ page }) => {
    await page.goto(`${BASE_URL}/bgv/${EXPIRED_TOKEN}`);
    await expect(page.getByText(/Link expired|cancelled/i)).toBeVisible();
  });
});
