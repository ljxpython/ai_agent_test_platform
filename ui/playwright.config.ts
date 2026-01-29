import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL ?? "http://localhost";

// 说明：SQL HITL 流程依赖外部系统（docker compose + LLM），
// 为了稳定性这里提高超时并强制串行执行。
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  timeout: Number(process.env.E2E_TEST_TIMEOUT_MS ?? 6 * 60_000),
  expect: {
    timeout: Number(process.env.E2E_EXPECT_TIMEOUT_MS ?? 90_000),
  },
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["list"], ["html", { open: "never" }]]
    : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
      },
    },
  ],
});
