import { mkdirSync, writeFileSync, chmodSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const credentialFile = fileURLToPath(
  new URL("../../../target/cli-playground-browser.secret", import.meta.url),
);
mkdirSync(fileURLToPath(new URL("../../../target", import.meta.url)), { recursive: true });
writeFileSync(credentialFile, "unused-browser-boxie-secret", { mode: 0o600 });
chmodSync(credentialFile, 0o600);

const origin = "http://127.0.0.1:8765";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  use: {
    baseURL: origin,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: "uv run --package cli-playground python -m tests.browser_server",
    cwd: fileURLToPath(new URL("../", import.meta.url)),
    env: {
      CLI_PLAYGROUND_DATA_DIR: fileURLToPath(
        new URL("../../../target/cli-playground-browser-data/", import.meta.url),
      ),
      CLI_PLAYGROUND_PUBLIC_URL: origin,
      CLI_PLAYGROUND_DEPLOY_TOKEN: "local-browser-test",
      CLI_PLAYGROUND_BOXIE_URL: "http://127.0.0.1:1",
      CLI_PLAYGROUND_BOXIE_CREDENTIAL_FILE: credentialFile,
    },
    url: `${origin}/healthz`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
