const { test, expect } = require("@playwright/test");

const profile = {
  ok: true,
  location: { city: "集宁", province: "乌兰察布", code: "150902" },
  habits: ["夜跑", "喜欢喝茶"],
  identity: {
    address: "鑫",
    assistant_name: "小助手",
    assistant_name_customized: false,
    role: "可靠、不打扰的个人数字助理",
    language: "先结论后细节",
  },
  rules: ["不确定时先问我"],
  lifestyle: "",
};

const modules = {
  ok: true,
  modules: [
    { name: "todo", purpose: "待办提醒", enabled: true, version: "1.4.2", auto_update: true, source: "官方模块源" },
    { name: "planner", purpose: "早晚报", enabled: false, version: "2.1.0", auto_update: true, source: "官方模块源" },
  ],
};

const weather = {
  ok: true,
  city: "集宁",
  current: { temperature: 18, wind_speed: 8, code: 0, emoji: "☀️", description: "晴" },
  hourly: [
    { time: "13:00", temperature: 19, description: "晴" },
    { time: "14:00", temperature: 19, description: "多云" },
    { time: "15:00", temperature: 18, description: "多云" },
    { time: "16:00", temperature: 18, description: "多云" },
  ],
};

async function mockApi(page) {
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body = { ok: true };
    if (path === "/api/auth") body = { ok: true, token: "playwright-test-token" };
    else if (path === "/api/profile") body = profile;
    else if (path === "/api/admin/modules") body = modules;
    else if (path === "/api/admin/weather") body = weather;
    else if (path === "/api/admin/sources") body = { ok: true, sources: [{ id: "builtin", name: "官方模块源", modules: modules.modules }], catalog: modules.modules.map(item => ({ ...item, source_id: "builtin", installed: true })) };
    else if (path === "/api/admin/schema") body = { ok: true, schema: [{ group: "acp", title: "对话 agent", fields: [{ key: "command", label: "命令路径", type: "text", default: "opencode", hint: "测试字段" }] }] };
    else if (path === "/api/admin/settings") body = { ok: true, settings: { acp: { command: "opencode" } } };
    else if (path === "/api/admin/logs") body = { ok: true, lines: ["INFO system web smoke test"] };
    else if (path === "/api/state") body = { ok: true, steps: {}, password_set: false, selftest: true };
    else if (path === "/api/env_check") body = { ok: true, passed: true, items: [{ name: "Python", value: "ok", ok: true }] };
    else if (path === "/api/opencode/detect") body = { ok: true, already: true, version: "test" };
    else if (path === "/api/login/setup") body = { ok: true, already: true };
    else if (path === "/api/login/status") body = { ok: true, done: true };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

test("login page renders and authenticates", async ({ page }) => {
  await mockApi(page);
  await page.goto("/login.html");
  await expect(page).toHaveTitle("进入 wechat-claw");
  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
  await page.getByLabel("管理密码").fill("test-password");
  await page.getByRole("button", { name: /进入工作台/ }).click();
  await expect(page).toHaveURL(/admin\.html$/);
});

test("admin workbench renders navigation and responsive cards", async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => localStorage.setItem("wc-auth", "playwright-test-token"));
  await page.goto("/admin.html");
  await expect(page.locator(".hero h1")).toBeVisible();
  await expect(page.locator(".nav-item")).toHaveCount(4);
  await page.screenshot({ path: "test-results/admin-desktop.png", fullPage: true });

  await page.getByRole("button", { name: "用户与助理" }).click();
  await expect(page.getByRole("heading", { name: "用户与助理" })).toBeVisible();
  await page.getByRole("button", { name: "模块管理" }).click();
  await expect(page.getByRole("heading", { name: "模块管理" })).toBeVisible();
  await page.locator('[data-toggle-key="module:todo"]').click();
  await expect(page.locator('[data-toggle-key="module:todo"]')).not.toHaveClass(/on/);

  await page.getByRole("button", { name: "基础设置" }).click();
  await page.locator('[data-accent="amber"]').first().click();
  await expect(page.locator("html")).toHaveAttribute("data-accent", "amber");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("button", { name: "wechat-claw" }).click();
  await expect(page.locator(".hero h1")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/admin-mobile.png", fullPage: true });
});

test("wizard renders six steps and can pass environment check", async ({ page }) => {
  await mockApi(page);
  await page.goto("/wizard.html");
  await expect(page.getByRole("heading", { name: "先把基础准备好。" })).toBeVisible();
  await expect(page.locator(".wizard-step")).toHaveCount(6);
  await page.getByRole("button", { name: /开始体检/ }).click();
  await expect(page.locator(".wizard-step").first()).toHaveClass(/done/);
  await page.getByRole("button", { name: "下一步 →" }).click();
  await expect(page.getByRole("heading", { name: "准备对话引擎。" })).toBeVisible();
});
