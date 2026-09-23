import { expect, test } from "@playwright/test";
import { TEST_EMAIL, TEST_PASSWORD } from "./global-setup";

test.describe.configure({ mode: "serial" }); // one shared backend session, not independent fixtures

test("sign in, launch a scenario, watch it finish, and investigate the incident it raises", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(TEST_EMAIL);
  await page.getByLabel("Password").fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL("/");
  await expect(page.getByText(TEST_EMAIL)).toBeVisible();

  await page.goto("/scenarios");
  const launch = page.getByTestId("launch-auth_bruteforce");
  await expect(launch).toBeEnabled();
  await launch.click();

  await expect(page).toHaveURL(/\/sessions\/[0-9a-f-]{36}/, { timeout: 15_000 });
  await expect(page.getByTestId("session-status")).toHaveText(/running/i);

  // speed=0 (unpaced): the 1800-step auth_bruteforce scenario finishes in well under a minute.
  await expect(page.getByTestId("session-status")).toHaveText(/completed/i, { timeout: 60_000 });
  await expect(page.getByText(/1 incident\(s\)/)).toBeVisible();

  const incidentLink = page.getByTestId("incident-link").first();
  await expect(incidentLink).toBeVisible();
  await incidentLink.click();

  await expect(page).toHaveURL(/\/incidents\/[0-9a-f-]{36}/);
  await expect(page.getByText("Cyberattack", { exact: true })).toBeVisible();
  await expect(page.getByText(/auth_anomaly/).first()).toBeVisible();

  const investigate = page.getByTestId("investigate-button");
  await expect(investigate).toBeEnabled();
  await investigate.click();

  // No ANTHROPIC_API_KEY is configured for the e2e backend, so this is always the offline path —
  // that's the one path guaranteed reproducible without a paid key (docs/LIMITATIONS.md).
  await expect(page.getByTestId("report-mode")).toHaveText(/offline fallback/i, { timeout: 30_000 });
  await expect(page.getByText("Recommended action")).toBeVisible();
  await expect(page.getByText(/Escalate to the security team/)).toBeVisible();

  // Ground truth stays hidden on the session page until asked for, even after the run finishes.
  await page.goBack();
  await expect(page.getByText("What actually happened is hidden until you ask")).toBeVisible();
  await page.getByRole("button", { name: "reveal" }).click();
  await expect(page.getByText("abnormal_authentication")).toBeVisible();
});

test("signed out, incidents are readable but launching a scenario is blocked", async ({ page }) => {
  await page.goto("/incidents");
  await expect(page.getByText("Cyberattack", { exact: true }).first()).toBeVisible();

  await page.goto("/scenarios");
  await expect(page.getByText("Sign in with an analyst or admin account")).toBeVisible();
  await expect(page.getByTestId("launch-auth_bruteforce")).toBeDisabled();
});
