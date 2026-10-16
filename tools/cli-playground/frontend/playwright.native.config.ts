import { defineConfig, devices } from "@playwright/test";

if (!process.env.BOXIE_NATIVE_ORIGIN) throw new Error("native lifecycle origin required");
export default defineConfig({
  testDir: "./e2e-native",
  workers: 1,
  retries: 0,
  timeout: 90_000,
  forbidOnly: true,
  use: { baseURL: process.env.BOXIE_NATIVE_ORIGIN, trace: "retain-on-failure" },
  projects: [{ name: "native-chromium", use: { ...devices["Desktop Chrome"] } }],
});
