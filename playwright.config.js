const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL: "http://127.0.0.1:8651",
    launchOptions: { executablePath: "/usr/bin/google-chrome-stable" },
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "WEB_SELFTEST=1 WEB_NO_BROWSER=1 ./.venv/bin/python web/wizard.py --port 8651",
    url: "http://127.0.0.1:8651/login.html",
    timeout: 15_000,
    reuseExistingServer: false,
  },
});
