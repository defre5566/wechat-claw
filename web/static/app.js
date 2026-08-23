/* wechat-claw formal frontend: preview visual language + real backend API. */
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = (v = "") => String(v).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const AUTH_KEY = "wc-auth";
const CARDS_KEY = "wc-home-cards";
const app = $("#app");

const state = {
  page: "home",
  drawer: false,
  autostart: false,
  autostartMode: "none",
  autostartPoll: null,
  serviceStatus: null,
  statusTimer: null,
  version: null,
  mode: localStorage.getItem("wc-mode") || "light",
  accent: localStorage.getItem("wc-accent") || "amber",
  homeCards: [],
  habits: [],
  toastTimer: null,
  profile: null,
  modules: [],
  sources: [],
  weather: null,
};

const greetings = [
  ["今天也替你", "留意着。"], ["我会帮你记着", "那些容易忘记的事。"], ["今天不用一次", "处理完所有事情。"],
  ["我会在合适的时候", "提醒你。"], ["微信连接正常", "小助手正在安静工作。"], ["有需要时叫我", "我会继续跟进。"],
  ["今天也慢慢来", "重要的事情我会帮你放在心上。"],
];

const api = {
  get token() { return localStorage.getItem(AUTH_KEY) || ""; },
  setToken(v) { localStorage.setItem(AUTH_KEY, v); },
  clearToken() { localStorage.removeItem(AUTH_KEY); },
  async request(method, path, body) {
    const headers = body === undefined ? {} : { "Content-Type": "application/json" };
    if (this.token) headers["X-Auth"] = this.token;
    let response;
    try { response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }); }
    catch { throw new Error("无法连接本地服务，请确认 Web 服务正在运行"); }
    let data = {}; try { data = await response.json(); } catch { /* non-json */ }
    if (response.status === 401 && path !== "/api/auth") { this.clearToken(); location.href = "/login.html"; throw new Error("登录已失效"); }
    if (!response.ok || data.ok === false) throw new Error(data.error || `请求失败（${response.status}）`);
    return data;
  },
  get(path) { return this.request("GET", path); },
  post(path, body = {}) { return this.request("POST", path, body); },
};

function toast(message, kind = "") { let node = $(".toast"); if (!node) { node = document.createElement("div"); node.className = "toast"; document.body.append(node); } node.className = `toast show ${kind}`; node.textContent = message; clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => node.classList.remove("show"), 2200); }
function applyTheme() {
  let mode = state.mode;
  if (mode === "system") mode = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.mode = mode;
  document.documentElement.dataset.accent = state.accent;
  localStorage.setItem("wc-mode", state.mode);
  localStorage.setItem("wc-accent", state.accent);
}
function themeCard() {
  const colors = [["green", "绿"], ["blue", "蓝"], ["amber", "琥"], ["violet", "紫"]];
  const modes = [["light", "浅色"], ["dark", "深色"], ["system", "跟随系统"]];
  return `<div class="theme-control">
    <div class="theme-row"><span>明暗</span><div class="segments">${modes.map(([id, text]) => `<button class="${state.mode === id ? "active" : ""}" data-mode="${id}">${text}</button>`).join("")}</div></div>
    <div class="theme-row"><span>主色</span><div class="swatches">${colors.map(([id, text]) => `<button class="swatch swatch-${id} ${state.accent === id ? "active" : ""}" aria-label="${text}色" data-accent="${id}"></button>`).join("")}</div></div>
  </div>`;
}
function bindThemeControls(root = document) {
  $$('[data-mode]', root).forEach(b => b.onclick = () => { state.mode = b.dataset.mode; applyTheme(); $$('.segments button[data-mode]', root).forEach(x => x.classList.toggle('active', x.dataset.mode === state.mode)); });
  $$('[data-accent]', root).forEach(b => b.onclick = () => { state.accent = b.dataset.accent; applyTheme(); $$('.swatch[data-accent]', root).forEach(x => x.classList.toggle('active', x.dataset.accent === state.accent)); });
}
function userName() { return state.profile?.identity?.address || "朋友"; }
function assistantName() { const identity = state.profile?.identity || {}; return identity.assistant_name_customized && identity.assistant_name ? identity.assistant_name : "wechat-claw"; }
function initials(name = "小助手") { return [...name].slice(0, 2).join(""); }
function dateLine() { return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "long" }).format(new Date()); }
function greeting() { const last = sessionStorage.getItem("wc-greeting"); const options = greetings.filter(x => x.join("") !== last); const value = options[Math.floor(Math.random() * options.length)] || greetings[0]; sessionStorage.setItem("wc-greeting", value.join("")); return value.map(x => `<span>${esc(x)}</span>`).join(""); }
function cards() { try { return JSON.parse(localStorage.getItem(CARDS_KEY) || "[]"); } catch { return []; } }
function toggle(enabled, key) { return `<button class="toggle ${enabled ? "on" : ""}" data-toggle-key="${key}" aria-label="切换开关"><i></i></button>`; }
function weatherClass() { const code = state.weather?.current?.code; return code >= 51 ? "weather-rain" : code === 2 || code === 3 ? "weather-cloud" : "weather-clear"; }
function sourceFor(module) { if (module.source) return module.source; for (const source of state.sources || []) if ((source.modules || []).some(x => x.name === module.name)) return source.name || source.id; return "本地模块"; }
function pluginCard(name) {
  const module = state.modules.find(x => x.name === name) || { name, purpose: "模块提供的首页卡片" };
  return `<article class="card dashboard-card span-6" data-plugin-card="${esc(name)}"><div class="card-head"><div><span class="card-label">模块卡片</span><h2>${esc(name)}</h2></div><button class="btn btn-secondary btn-sm" data-remove-card="${esc(name)}">移除</button></div><div class="activity-empty plugin-placeholder"><i>✦</i><p>${esc(module.purpose || "模块内容")}<small>正式内容由 ${esc(name)} 模块提供。</small></p></div></article>`;
}
function heading(title, description) { return `<header class="page-heading"><div><h1>${title}</h1><p>${description}</p></div></header>`; }

const ICONS = {
  home: '<svg viewBox="0 0 24 24"><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6A8.4 8.4 0 0 1 12.5 3h.5a8.5 8.5 0 0 1 8 8v.5Z"/></svg>',
  settings: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></svg>',
  user: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/></svg>',
  modules: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></svg>',
};
function nav() {
  const items = [["home", assistantName(), ICONS.home], ["settings", "基础设置", ICONS.settings], ["user", "用户与助理", ICONS.user], ["modules", "模块管理", ICONS.modules]];
  return `<aside class="sidebar ${state.drawer ? "open" : ""}"><nav>${items.map(([id, label, icon]) => `<button class="nav-item ${state.page === id ? "active" : ""}" data-nav="${id}"><span class="nav-icon">${icon}</span><span>${esc(label)}</span></button>`).join("")}</nav></aside>`;
}
function shell(content) {
  return `<div class="app-shell">${nav()}<main class="workspace"><header class="global-bar"><div class="global-left"><button class="mobile-menu" data-menu aria-label="打开导航">☰</button></div><div class="global-actions">${state.page === "home" ? '<button class="btn btn-primary" data-card-manager><b>＋</b> 添加卡片</button>' : ""}<span class="avatar"><img src="/api/profile/avatar?ts=${Date.now()}" alt="" onerror="this.remove()">${esc(initials(userName()))}</span></div></header>${content}</main></div><div class="toast"></div>`;
}
function modal(title, body) {
  const node = document.createElement("div"); node.className = "modal-backdrop";
  node.innerHTML = `<section class="modal"><header><h2>${title}</h2><button class="btn btn-quiet" data-close>✕</button></header><div class="modal-body">${body}</div></section>`;
  document.body.append(node);
  node.addEventListener("click", e => { if (e.target === node || e.target.closest("[data-close]")) node.remove(); });
  return node;
}

function home() {
  const modules = state.modules, enabled = modules.filter(x => x.enabled), profile = state.profile || {}, loc = profile.location || {};
  const current = state.weather?.current, hourly = state.weather?.hourly || [];
  const city = loc.city || "还未设置";
  const habits = profile.habits || [];
  const st = state.serviceStatus || {};
  const bridgeCls = st.bridge_running ? "ok" : "fail";
  const suggestion = habits.includes("夜跑") ? "一天的工作结束，要不，晚上出去跑跑？" : "今天也慢慢来，留一点时间给自己。";
  const pref = habits.length ? `我记得你喜欢${esc(habits.slice(0, 3).join("、"))}。` : "在用户与助理中完善兴趣和生活习惯，我会更了解你。";
  const pluginCards = cards().map(pluginCard).join("");
  return `<section class="page"><section class="hero"><div class="hero-copy"><span class="kicker">${esc(dateLine())}</span><h1>${greeting()}</h1><p>${esc(userName())}，${esc(assistantName())}会在这里安静地陪你处理日常。</p><div class="hero-status"><i class="status-dot ${bridgeCls}"></i>${st.bridge_running ? "服务在线" : "服务离线"} · 模块 ${st.module_count ?? state.modules.length} 个已加载</div></div><div class="hero-art"><div class="assistant-orb">✦</div></div></section><section class="dashboard-grid" id="homeGrid">
    <article class="card dashboard-card span-7"><div class="card-head"><div><span class="card-label">最近</span><h2>最近活动</h2></div><button class="btn btn-secondary btn-sm" data-open="logs">查看日志</button></div><div class="activity-meta"><span>${enabled.length} 个模块运行中</span><span>${modules.length} 个已安装</span><span>今日发送暂无数据</span></div><div class="activity-empty"><i>◷</i><p>最近还没有新的活动。<small>完整记录可以在运行日志中查看。</small></p></div></article>
    <article class="card dashboard-card weather ${weatherClass()} span-5"><div class="card-head"><div><span class="card-label">天气</span></div><button class="btn btn-secondary btn-sm" data-open="city">设置城市</button></div>${!loc.city ? `<div class="weather-main"><div class="weather-city-temp"><span class="weather-city">待设置城市</span><strong class="weather-temp">—</strong></div><span class="weather-desc">设置城市后可查看天气</span></div>` : current ? `<div class="weather-main"><div class="weather-city-temp"><span class="weather-city">${esc(city)}</span><strong class="weather-temp">${current.temperature}°</strong></div><span class="weather-desc">${esc(current.emoji)} ${esc(current.description)} · 风速 ${current.wind_speed} km/h</span></div><div class="forecast-title">未来 3 小时</div><div class="forecast">${hourly.slice(1, 4).map(x => `<div><time>${esc(x.time)}</time><strong>${x.temperature}°</strong><small>${esc(x.description)}</small></div>`).join("")}</div>` : `<div class="weather-main"><div class="weather-city-temp"><span class="weather-city">${esc(city)}</span><strong class="weather-temp">—</strong></div><span class="weather-desc">天气暂时无法获取</span></div>`}</article>
    <article class="card suggestion span-12"><div class="suggestion-icon">✦</div><div class="suggestion-copy"><span class="card-label">给你的建议</span><h2>${suggestion}</h2><p>${pref}</p></div><button class="btn btn-secondary btn-sm" data-nav="user">编辑偏好</button></article>${pluginCards}
  </section></section>`;
}

function settings() {
  const enabled = state.modules.filter(x => x.enabled).length;
  const st = state.serviceStatus || {};
  const bridgeOk = st.bridge_running;
  const bridgeCls = bridgeOk ? "ok" : "fail";
  const modeTxt = st.autostart_mode === "system" ? "系统服务·开机自启" : st.autostart_mode === "user" ? "用户级·登录自启" : "未开启自启";
  return `<section class="page secondary-page">${heading("基础设置", "管理服务、外观、自启动和安全选项。")}<div class="settings-rows">
    <div class="settings-row-1">
      <article class="card panel service-log-final"><div class="panel-head"><div><span class="card-label">SERVICE</span><h2>服务与自启动</h2><p>由 wechat-claw 管理本地服务。</p></div></div>
        <div class="service-hero-row"><div class="service-orb">✦</div><div><strong>开机自动启动服务</strong><small>${modeTxt}${state.autostartMode === "user" ? " · 开启后升级为开机自启（需 UAC）" : " · 登录系统后自动启动微信消息桥接"}</small></div>${toggle(state.autostart, "autostart")}</div>
        <div class="service-status-line"><span class="status-dot ${bridgeCls}"></span><strong>服务状态</strong>${bridgeOk ? " · 运行中" : " · 未运行"}<span class="service-module-count">${st.module_count ?? state.modules.length} 个模块已加载</span>${bridgeOk ? `<button class="btn btn-secondary btn-sm" data-open="logs">查看完整日志</button>` : `<button class="btn btn-primary btn-sm" data-start>启动</button>`}</div>
      </article>
      <article class="card panel theme-final"><div class="panel-head"><div><span class="card-label">APPEARANCE</span><h2>主题外观</h2><p>明暗模式和主色独立设置。</p></div><span class="panel-symbol">◐</span></div>${themeCard()}</article>
    </div>
    <div class="settings-row-2">
      <article class="card panel security-final"><div class="panel-head"><div><span class="card-label">SECURITY</span><h2>管理密码</h2><p>修改进入后台所需的管理密码。</p></div><span class="panel-symbol">⌁</span></div><button class="btn btn-secondary btn-sm" data-open="password">修改密码</button></article>
      <article class="card panel advanced-final"><div class="panel-head"><div><span class="card-label">ADVANCED</span><h2>高级运行配置</h2><p>${state.version ? `当前版本 v${state.version.current}${state.version.is_latest ? " · 当前为最新版本" : " · 最新版本 v" + state.version.latest + " · " + (state.version.has_git ? '<button class="btn btn-secondary btn-sm" data-update="gitpull">git pull 更新</button>' : '<button class="btn btn-primary btn-sm" data-update="download">下载最新版</button>')}` : "运行参数、文件发送规则和其他高级选项。"}</p></div><span class="panel-symbol">⚙</span></div><button class="btn btn-secondary btn-sm" data-open="advanced">打开高级设置</button></article>
    </div>
  </div></section>`;
}

function user() {
  const p = state.profile || {}, i = p.identity || {}, habits = p.habits || [];
  return `<section class="page secondary-page"><header class="page-heading user-heading"><div><h1>用户与助理</h1><p>设置助理的人设、表达方式和对你的了解。</p></div><div class="user-save-actions"><button class="btn btn-secondary" data-undo-all>撤销修改</button><button class="btn btn-primary" data-save-profile>保存用户与助理</button></div></header><form id="profileForm" class="user-rows">
    <div class="user-row-1">
      <article class="card panel persona-final"><div class="panel-head"><div><span class="card-label">YOUR ASSISTANT</span><h2>助理人设</h2><p>保存后自动生成 AGENTS.md。</p></div><label class="persona-avatar" data-open="avatar"><img src="/api/profile/avatar?ts=${Date.now()}" alt="" onerror="this.remove()">${esc(initials(userName()))}<input id="avatarInput" type="file" accept="image/*" hidden></label></div><div class="persona-avatar-action"><button type="button" class="btn btn-primary btn-sm" data-open="avatar">选择头像</button><button type="button" class="btn btn-secondary btn-sm" data-avatar-undo>撤销头像</button><span>默认使用抽象小助手形象。</span></div><div class="identity-fields"><label class="field">怎么称呼你<input name="address" value="${esc(i.address || "")}"></label><label class="field">助理名称<input name="assistant_name" value="${esc(i.assistant_name || "小助手")}"></label></div><label class="field full">角色设定<textarea name="role" rows="3">${esc(i.role || "")}</textarea></label><label class="field full">语言习惯<textarea name="language" rows="2">${esc(i.language || "")}</textarea></label><div class="inline-actions"><button type="button" class="btn btn-secondary btn-sm" data-optimize>用 opencode 优化</button><button type="button" class="btn btn-quiet" data-undo="identity">撤销</button></div></article>
      <div class="user-side-stack">
        <article class="card panel memory-final"><div class="panel-head"><div><span class="card-label">I REMEMBER</span><h2>我记得这些</h2><p>用于首页建议和模块上下文。</p></div><button type="button" class="btn btn-quiet" data-undo="habits">撤销</button></div><div class="tags" id="habitTags">${habits.map(h => `<span class="tag">${esc(h)}<button type="button" data-remove-habit="${esc(h)}">×</button></span>`).join("")}</div><button type="button" class="btn btn-primary btn-sm" data-open="habit">＋ 添加偏好</button></article>
        <article class="card panel city-final"><div class="panel-head"><div><span class="card-label">YOUR PLACE</span><h2>所在城市</h2><p>天气和本地化模块会使用这里。</p></div><button type="button" class="btn btn-quiet" data-undo="city">撤销</button></div><div class="city-layout"><strong class="city-name">${esc(p.location?.city || "未设置城市")}</strong><span class="city-sub">${esc(p.location?.province || "城市用于天气和本地化信息")}</span><div class="city-footer"><button type="button" class="btn btn-secondary btn-sm" data-locate>定位</button><button type="button" class="btn btn-primary btn-sm" data-open="city">选择城市</button></div></div></article>
      </div>
    </div>
    <div class="user-row-2">
      <article class="card panel rules-final"><div class="panel-head"><div><span class="card-label">BOUNDARIES</span><h2>行为守则</h2><p>一行一条，帮助助理理解边界。</p></div><button type="button" class="btn btn-quiet" data-undo="rules">撤销</button></div><textarea name="rules" rows="8">${esc((p.rules || []).join("\n"))}</textarea></article>
      <article class="card panel lifestyle-final"><div class="panel-head"><div><span class="card-label">LIFE</span><h2>生活习惯</h2><p>让模块理解你的生活节奏。</p></div></div><textarea name="lifestyle" rows="8">${esc(p.lifestyle || "")}</textarea></article>
    </div>
  </form></section>`;
}

function moduleCard(m) {
  return `<article class="card module-card"><div class="module-title"><h2>${esc(m.name)}<span class="version">v${esc(m.version || "?")}</span></h2>${toggle(m.enabled, `module:${m.name}`)}</div><p>${esc(m.purpose || "暂无描述")}</p><div class="module-meta"><span>来源：${esc(sourceFor(m))}</span><button class="meta-action" data-auto="${esc(m.name)}">${m.auto_update === false ? "自动更新关闭" : "自动更新开启"}</button></div><footer class="module-footer"><span class="service-status">${m.enabled ? "运行中" : "已停用"}</span><div class="module-buttons"><button class="btn btn-secondary btn-sm" data-module-settings="${esc(m.name)}">设置</button><button class="btn btn-danger btn-sm" data-module-remove="${esc(m.name)}">卸载</button></div></footer></article>`;
}
function modules() {
  const sources = state.sources || [];
  return `<section class="page secondary-page">${heading("模块管理", "管理模块源、已安装模块、版本和运行状态。")}<div class="module-stagger">
    <article class="card panel module-sources"><div class="panel-head"><div><h2>模块源</h2><p>管理模块目录的仓库和本地路径。</p></div><button class="btn btn-primary" data-open="add-source">＋ 添加模块源</button></div><div class="source-list">${sources.length ? sources.map(s => `<div class="source-row"><div class="source-name"><strong>${esc(s.name || s.id)}</strong><small>${esc(s.url || "本地模块源")} · ${(s.modules || []).length} 个模块</small></div><span class="source-url">${esc(s.url || "本地")}</span><button class="btn btn-secondary btn-sm" data-source-refresh="${esc(s.id || "")}">刷新</button>${s.builtin ? "" : `<button class="btn btn-danger btn-sm" data-source-remove="${esc(s.id || "")}">删除</button>`}</div>`).join("") : '<p class="modal-note">暂无模块源</p>'}</div></article>
    <article class="card panel module-control-panel"><div class="module-top-actions"><div><h2>已安装模块</h2><p>检查更新、刷新列表，或添加新的模块能力。</p></div><div class="module-buttons"><button class="btn btn-secondary" data-check>检查更新</button><button class="btn btn-secondary" data-refresh>刷新列表</button><button class="btn btn-primary" data-open="install">＋ 添加模块</button></div></div><div class="module-grid">${state.modules.map(moduleCard).join("") || '<p class="modal-note">还没有安装模块</p>'}</div></article>
  </div></section>`;
}

async function loadData() {
  const [profile, modules, sources, weather, autostart, status, version] = await Promise.all([
    api.get("/api/profile"),
    api.get("/api/admin/modules"),
    api.get("/api/admin/sources"),
    api.get("/api/admin/weather").catch(() => null),
    api.get("/api/admin/autostart").catch(() => null),
    api.get("/api/admin/status").catch(() => null),
    api.get("/api/admin/version").catch(() => null),
  ]);
  state.profile = profile;
  state.modules = modules.modules || [];
  state.sources = sources.sources || [];
  state.weather = weather?.ok ? weather : null;
  if (autostart?.ok) {
    state.autostartMode = autostart.mode || "none";
    state.autostart = autostart.mode !== "none";
  }
  if (status?.ok) state.serviceStatus = status;
  if (version?.ok) state.version = version;
  clearTimeout(state.statusTimer);
  state.statusTimer = setTimeout(async () => { try { const s = await api.get("/api/admin/status"); if (s?.ok) { state.serviceStatus = s; if (state.page === "settings") render(); } } catch (e) { /* 静默 */ } }, 15000);
}

async function pollAutostart(attempts = 10) {
  for (let i = 0; i < attempts; i++) {
    await new Promise(r => setTimeout(r, 1500));
    try {
      const d = await api.get("/api/admin/autostart");
      if (!d.ok) continue;
      if (d.mode === "system" || d.mode === "user") {
        state.autostartMode = d.mode;
        state.autostart = true;
        render();
        toast(`已生效（${d.mode === "system" ? "系统服务·开机自启" : "用户级·登录自启"}）`, "success");
        return;
      }
      if (d.mode === "none") { render(); toast("未检测到自启（可能未完成）", "error"); return; }
    } catch (e) { /* 继续轮询 */ }
  }
  render();
  toast("状态刷新超时，请查看 web.log 或手动确认", "error");
}

async function toggleAutostart(b) {
  b.disabled = true;
  const on = !state.autostart;
  try {
    const d = await api.post("/api/admin/autostart", { on });
    if (d.uac_required) {
      toast("请在 UAC 弹窗中点击『允许』，稍后自动确认…", "info");
      pollAutostart();
    } else {
      if (!on) { state.autostart = false; state.autostartMode = "none"; render(); toast("已关闭开机自动启动", "success"); }
      else if (d.mode === "system" || d.mode === "user") { state.autostart = true; state.autostartMode = d.mode; render(); toast("已开启开机自动启动", "success"); }
    }
  } catch (e) {
    toast(e.message, "error");
  } finally {
    b.disabled = false;
  }
}
function render() { applyTheme(); const content = state.page === "home" ? home() : state.page === "settings" ? settings() : state.page === "user" ? user() : modules(); app.innerHTML = shell(content); bind(); }
function bind() {
  $$('[data-nav]').forEach(b => b.onclick = () => { state.page = b.dataset.nav; state.drawer = false; render(); });
  $("[data-menu]")?.addEventListener("click", () => { state.drawer = !state.drawer; render(); });
  bindThemeControls();
  $$('[data-toggle-key]').forEach(b => b.onclick = () => {
    if (b.dataset.toggleKey === "autostart") { toggleAutostart(b); }
    else if (b.dataset.toggleKey === "autostart-on") { b.classList.toggle("on"); svcUp(); }
    else {
      const m = state.modules.find(x => `module:${x.name}` === b.dataset.toggleKey); if (!m) return;
      b.disabled = true;
      api.post("/api/admin/modules/toggle", { name: m.name, enabled: !m.enabled }).then(() => { m.enabled = !m.enabled; b.classList.toggle("on", m.enabled); b.closest(".module-card").querySelector(".service-status").textContent = m.enabled ? "运行中" : "已停用"; }).catch(e => toast(e.message, "error")).finally(() => { b.disabled = false; });
    }
  });
  $$('[data-open]').forEach(b => b.onclick = () => openModal(b.dataset.open));
  $$('[data-start]').forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = "启动中…";
    try { const d = await api.post("/api/admin/start"); if (!d.ok) throw new Error("启动失败"); toast("bridge 已启动", "success"); setTimeout(loadData, 2000); } catch (e) { toast(e.message, "error"); b.disabled = false; b.textContent = "启动"; }
  });
  $$('[data-update="gitpull"]').forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = "更新中…";
    try { const d = await api.post("/api/admin/update/gitpull"); if (!d.ok) throw new Error(d.error || "更新失败"); toast("更新成功，请重启 web 服务", "success"); setTimeout(loadData, 3000); } catch (e) { toast(e.message, "error"); b.disabled = false; b.textContent = "git pull 更新"; }
  });
  $$('[data-update="download"]').forEach(b => b.onclick = () => { window.open("https://github.com/defre5566/wechat-claw/releases/latest", "_blank"); });
  $$('[data-toast]').forEach(b => b.onclick = () => toast(b.dataset.toast));
  $("[data-card-manager]")?.addEventListener("click", openCardManager);
  $$('[data-remove-card]').forEach(b => b.onclick = () => { const node = b.closest("[data-plugin-card]"); if (node) node.remove(); const next = cards().filter(x => x !== b.dataset.removeCard); localStorage.setItem(CARDS_KEY, JSON.stringify(next)); toast("卡片已移除"); });
  $$('[data-locate]').forEach(b => b.onclick = () => locateCity());
  $$('[data-source-refresh]').forEach(b => b.onclick = async () => { b.disabled = true; try { const d = await api.post("/api/admin/sources/refresh", { id: b.dataset.sourceRefresh }); toast(d.ok ? "模块源已刷新" : (d.error || "刷新失败"), "success"); } catch (e) { toast(e.message, "error"); } finally { b.disabled = false; } });
  $$('[data-source-remove]').forEach(b => b.onclick = async () => { if (!confirm("确定删除这个模块源？")) return; try { await api.post("/api/admin/sources/remove", { id: b.dataset.sourceRemove }); state.sources = (await api.get("/api/admin/sources")).sources || []; render(); toast("模块源已删除", "success"); } catch (e) { toast(e.message, "error"); } });
  $$('[data-module-settings]').forEach(b => b.onclick = () => openModuleSettings(b.dataset.moduleSettings));
  $$('[data-module-remove]').forEach(b => b.onclick = async () => { if (!confirm(`确定卸载 ${b.dataset.moduleRemove}？`)) return; try { await api.post("/api/admin/modules/remove", { name: b.dataset.moduleRemove }); state.modules = state.modules.filter(x => x.name !== b.dataset.moduleRemove); render(); toast("模块已卸载", "success"); } catch (e) { toast(e.message, "error"); } });
  $$('[data-auto]').forEach(b => b.onclick = async () => { const m = state.modules.find(x => x.name === b.dataset.auto); if (!m) return; try { await api.post("/api/admin/module/auto_update", { name: m.name, on: m.auto_update === false }); m.auto_update = m.auto_update === false; render(); } catch (e) { toast(e.message, "error"); } });
  $$('[data-check]').forEach(b => b.onclick = async () => { b.disabled = true; try { const r = await api.post("/api/admin/modules/check_updates"); toast(r.updated?.length ? `已更新：${r.updated.join("、")}` : "检查完成，没有需要更新的模块", "success"); } catch (e) { toast(e.message, "error"); } finally { b.disabled = false; } });
  $$('[data-refresh]').forEach(b => b.onclick = async () => { try { const d = await api.get("/api/admin/modules"); state.modules = d.modules || []; render(); toast("模块列表已刷新", "success"); } catch (e) { toast(e.message, "error"); } });
  $$('[data-undo]').forEach(b => b.onclick = () => undoProfile(b.dataset.undo));
  $$('[data-avatar-undo]').forEach(b => b.onclick = async () => { try { await api.post("/api/profile/avatar/undo"); state.profile = await api.get("/api/profile"); render(); toast("头像已撤销", "success"); } catch (e) { toast(e.message, "error"); } });
  $$('[data-optimize]').forEach(b => b.onclick = () => optimizePersona(b));
  $$('[data-remove-habit]').forEach(b => b.onclick = () => b.parentElement.remove());
  const form = $("#profileForm"); if (form) bindProfile(form);
  $("[data-save-profile]")?.addEventListener("click", () => form?.requestSubmit());
  $("[data-undo-all]")?.addEventListener("click", () => toast("已撤销未保存修改"));
  $("#avatarInput")?.addEventListener("change", uploadAvatar);
}
function bindProfile(form) {
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const d = new FormData(form);
    const habits = $$("#habitTags .tag").map(x => x.textContent.replace("×", "").trim()).filter(Boolean);
    const name = String(d.get("assistant_name") || "").trim();
    const old = state.profile.identity?.assistant_name || "小助手";
    const customized = Boolean(name && (state.profile.identity?.assistant_name_customized || name !== old || name !== "小助手"));
    const button = $("[data-save-profile]"); if (button) button.disabled = true;
    try {
      await api.post("/api/profile", {
        identity: { address: d.get("address"), assistant_name: name || "小助手", assistant_name_customized: customized, role: d.get("role"), language: d.get("language") },
        rules: String(d.get("rules") || "").split("\n").map(x => x.trim()).filter(Boolean),
        habits, lifestyle: d.get("lifestyle") || "",
      });
      state.profile = await api.get("/api/profile");
      toast("资料已保存，AGENTS.md 已自动生成", "success");
      api.post("/api/agents/render").catch(() => {});
      render();
    } catch (error) { toast(error.message, "error"); }
    finally { if (button) button.disabled = false; }
  });
}
function addHabitToDom() {}
async function uploadAvatar(e) { const file = e.target.files?.[0]; if (!file) return; const reader = new FileReader(); reader.onload = async () => { try { await api.post("/api/profile/avatar", { data: reader.result }); state.profile = await api.get("/api/profile"); render(); toast("头像已更新", "success"); } catch (error) { toast(error.message, "error"); } }; reader.readAsDataURL(file); }
async function undoProfile(field) { try { await api.post("/api/profile/undo", { field }); state.profile = await api.get("/api/profile"); render(); toast("已撤销上次修改", "success"); } catch (e) { toast(e.message, "error"); } }
async function optimizePersona(btn) {
  const form = $("#profileForm");
  const role = form?.querySelector('[name="role"]'); const lang = form?.querySelector('[name="language"]');
  if (!role || !lang) return;
  if (!role.value.trim() && !lang.value.trim()) { toast("角色设定和语言习惯都为空，无法优化", "error"); return; }
  btn.disabled = true; btn.textContent = "优化中…";
  try {
    const d = await api.post("/api/agents/optimize_persona", { role: role.value, language: lang.value });
    if (d.role) role.value = d.role;
    if (d.language) lang.value = d.language;
    toast(d.fallback ? "opencode 输出未按格式截取，原始结果已填入角色设定" : "人设优化完成，请确认后保存", "success");
  } catch (e) { toast(e.message, "error"); }
  finally { btn.disabled = false; btn.textContent = "用 opencode 优化"; }
}

function openModal(name) {
  if (name === "logs") {
    const node = modal("运行日志", `<div class="field-group"><div class="modal-log" id="logLines">读取中…</div><div class="modal-actions"><button class="btn btn-secondary btn-sm" data-log-refresh>刷新</button></div></div>`);
    const load = async () => { try { const d = await api.post("/api/admin/logs", { tail: 200 }); $("#logLines", node).innerHTML = (d.lines || []).map(l => `<div class="log-line">${esc(l)}</div>`).join("") || "暂无日志"; } catch (e) { $("#logLines", node).textContent = e.message; } };
    $("[data-log-refresh]", node).onclick = load; load();
  } else if (name === "password") {
    const node = modal("修改管理密码", `<div class="field-group"><label class="field">当前密码<input id="oldPwd" type="password"></label><label class="field">新密码<input id="newPwd" type="password"></label><label class="field">确认新密码<input id="confirmPwd" type="password"></label><div class="modal-actions"><button class="btn btn-primary btn-sm" data-pwd-save>保存密码</button></div></div>`);
    $("[data-pwd-save]", node).onclick = async () => { const next = $("#newPwd", node).value; if (next.length < 6 || next !== $("#confirmPwd", node).value) return toast("新密码至少 6 位且两次一致", "error"); try { await api.post("/api/admin/password", { old: $("#oldPwd", node).value, new: next }); api.clearToken(); location.href = "/login.html"; } catch (e) { toast(e.message, "error"); } };
  } else if (name === "advanced") {
    Promise.all([api.get("/api/admin/schema"), api.get("/api/admin/settings")]).then(([schema, settings]) => {
      const node = modal("高级运行配置", `<div class="field-group">${(schema.schema || []).map(g => `<label class="field">${esc(g.title)}${(g.fields || []).map(f => `<input data-key="${esc(f.key)}" value="${esc(settings.settings?.[g.group]?.[f.key] ?? f.default ?? "")}">`).join("")}</label>`).join("")}<div class="modal-actions"><button class="btn btn-primary btn-sm" data-adv-save>保存设置</button></div></div>`);
      $("[data-adv-save]", node).onclick = async () => { const output = {}; $$("[data-key]", node).forEach(f => { output[f.dataset.key] = f.value; }); try { await api.post("/api/admin/settings", { settings: { advanced: output } }); node.remove(); toast("高级设置已保存", "success"); } catch (e) { toast(e.message, "error"); } };
    }).catch(e => toast(e.message, "error"));
  } else if (name === "city") {
    fetch("/cities.json").then(r => r.json()).then(cities => {
      // 数据结构 [code, name, parent, pinyin, lat, lng]；初始显示市级（parent 为省级），
      // 搜索过滤全量（省/市/区县都可选，天气按区级坐标查询）
      const cityList = q => {
        const kw = q.trim();
        const all = kw ? cities.filter(c => c[1].includes(kw))
                       : cities.filter(c => c[2] && c[2].slice(-4) === "0000");
        return all.slice(0, 80).map(([code, cn]) =>
          `<button class="city-item" data-code="${esc(code)}" data-city="${esc(cn)}">${esc(cn)}</button>`).join("");
      };
      const node = modal("选择城市", `<input class="city-search" placeholder="搜索城市（省/市/区县）"><div class="city-list">${cityList("")}</div><div class="modal-actions"><button class="btn btn-secondary btn-sm" data-locate>使用当前位置</button><button class="btn btn-primary btn-sm" data-close>完成</button></div>`);
      const listEl = $(".city-list", node);
      $(".city-search", node).addEventListener("input", e => { listEl.innerHTML = cityList(e.target.value); });
      listEl.addEventListener("click", async e => {
        const item = e.target.closest(".city-item"); if (!item) return;
        try { await api.post("/api/profile/city", { code: item.dataset.code }); state.profile = await api.get("/api/profile"); node.remove(); render(); toast(`已设为所在城市：${item.dataset.city}`, "success"); } catch (err) { toast(err.message, "error"); }
      });
      $("[data-locate]", node).onclick = () => locateCity(node);
    }).catch(e => toast("城市库加载失败：" + e.message, "error"));
  } else if (name === "habit") {
    const node = modal("添加偏好", `<div class="field-group"><label class="field">新的偏好或习惯<input id="habitInput" placeholder="例如：夜跑、喜欢喝茶"></label><div class="modal-actions"><button class="btn btn-primary btn-sm" data-habit-save>添加</button></div></div>`);
    $("[data-habit-save]", node).onclick = () => { const value = $("#habitInput", node).value.trim(); if (!value) return; $("#habitTags").insertAdjacentHTML("beforeend", `<span class="tag">${esc(value)}<button type="button" data-remove-habit="${esc(value)}">×</button></span>`); node.remove(); };
  } else if (name === "avatar") {
    const node = modal("选择头像", `<p class="modal-note">支持 png / jpg / gif / webp，建议 512×512 以内。</p><label class="avatar-drop">点击选择图片<input id="avatarModalInput" type="file" accept="image/*"></label><div class="modal-actions"><button class="btn btn-primary btn-sm" data-close>确定</button></div>`);
    $("#avatarModalInput", node).addEventListener("change", async e => { const file = e.target.files?.[0]; if (!file) return; const reader = new FileReader(); reader.onload = async () => { try { await api.post("/api/profile/avatar", { data: reader.result }); state.profile = await api.get("/api/profile"); node.remove(); render(); toast("头像已更新", "success"); } catch (err) { toast(err.message, "error"); } }; reader.readAsDataURL(file); });
  } else if (name === "add-source") {
    const node = modal("添加模块源", `<div class="field-group"><label class="field">类型<select id="sourceType"><option value="github">GitHub 仓库</option><option value="local">本地目录</option></select></label><label class="field">地址或路径<input id="sourceUrl" placeholder="github.com/user/repo 或 ~/dev/modules"></label><div class="modal-actions"><button class="btn btn-primary btn-sm" data-source-add>添加</button></div></div>`);
    $("[data-source-add]", node).onclick = async () => { try { await api.post("/api/admin/sources/add", { type: $("#sourceType", node).value, url: $("#sourceUrl", node).value.trim() }); node.remove(); toast("模块源已添加", "success"); } catch (e) { toast(e.message, "error"); } };
  } else if (name === "install") {
    api.get("/api/admin/sources").then(d => {
      const catalog = d.catalog || [];
      const node = modal("添加模块", `<div class="install-list">${catalog.length ? catalog.map(m => `<div class="install-item"><div><strong>${esc(m.name)}</strong><small>${esc(m.purpose || "模块")}</small></div>${m.installed ? '<span class="soft-tag">已安装</span>' : `<button class="btn btn-primary btn-sm" data-install="${esc(m.source_id || m.source || "")}" data-install-name="${esc(m.name)}">安装</button>`}</div>`).join("") : '<p class="modal-note">暂无可安装模块</p>'}</div>`);
      $$("[data-install]", node).forEach(b => b.onclick = async () => { try { await api.post("/api/admin/modules/install", { source_id: b.dataset.install, name: b.dataset.installName }); node.remove(); state.modules = (await api.get("/api/admin/modules")).modules || []; render(); toast("模块已安装", "success"); } catch (e) { toast(e.message, "error"); } });
    }).catch(e => toast(e.message, "error"));
  }
}
function openModuleSettings(name) {
  api.post("/api/admin/module/get", { name }).then(d => {
    const module = d.module || {};
    const fieldHtml = f => {
      const val = module.settings?.[f.key] ?? f.default ?? "";
      let input;
      if (f.type === "select") {
        const opts = (f.options || []).map(o => `<option value="${esc(o.value)}" ${String(val) === String(o.value) ? "selected" : ""}>${esc(o.label || o.value)}</option>`).join("");
        input = `<select data-key="${esc(f.key)}" data-type="select">${opts}</select>`;
      } else if (f.type === "boolean") {
        input = `<select data-key="${esc(f.key)}" data-type="boolean"><option value="true" ${val === true || val === "true" ? "selected" : ""}>开启</option><option value="false" ${val === false || val === "" || val === "false" ? "selected" : ""}>关闭</option></select>`;
      } else if (f.type === "tags") {
        input = `<textarea data-key="${esc(f.key)}" data-type="tags" rows="4">${esc(Array.isArray(val) ? val.join("\n") : String(val))}</textarea>`;
      } else { // string / path 等文本输入
        input = `<input data-key="${esc(f.key)}" data-type="string" value="${esc(val)}">`;
      }
      const cond = f.show_when ? JSON.stringify(f.show_when) : "";
      return `<label class="field" data-show-when='${esc(cond)}'>${esc(f.label)}${f.desc ? `<small>${esc(f.desc)}</small>` : ""}${input}</label>`;
    };
    const groups = (module.settings_schema || []);
    const body = groups.length ? groups.map(g =>
      `<div class="setting-group"><h3>${esc(g.section || "")}</h3>${g.desc ? `<p class="modal-note">${esc(g.desc)}</p>` : ""}${(g.fields || []).map(fieldHtml).join("")}</div>`).join("")
      : '<p class="modal-note">这个模块没有可配置项。</p>';
    const node = modal(`${esc(name)} 设置`, `<p class="modal-note">${esc(module.purpose || "暂无描述")}</p>${body}<div class="modal-actions"><button class="btn btn-primary btn-sm" data-module-save>保存设置</button></div>`);
    // show_when 条件显示：字段值变化联动显隐（不满足条件的字段不提交，后端也会丢弃）
    const applyShowWhen = () => {
      $$("[data-show-when]", node).forEach(el => {
        const cond = el.dataset.showWhen ? JSON.parse(el.dataset.showWhen) : null;
        if (!cond || Object.keys(cond).length === 0) { el.style.display = ""; return; }
        const ok = Object.entries(cond).every(([k, v]) => {
          const src = node.querySelector(`[data-key="${k}"]`);
          return src && String(src.value) === String(v);
        });
        el.style.display = ok ? "" : "none";
      });
    };
    $$("[data-type='select']", node).forEach(s => s.addEventListener("change", applyShowWhen));
    applyShowWhen();
    $("[data-module-save]", node).onclick = async () => {
      const settings = {};
      $$("[data-key]", node).forEach(x => {
        const holder = x.closest("[data-show-when]");
        if (holder && holder.style.display === "none") return;  // 隐藏字段不提交
        const t = x.dataset.type;
        if (t === "boolean") settings[x.dataset.key] = x.value === "true";
        else if (t === "tags") settings[x.dataset.key] = x.value.split("\n").map(s => s.trim()).filter(Boolean);
        else settings[x.dataset.key] = x.value;
      });
      try { await api.post("/api/admin/module/update", { name, settings }); node.remove(); toast("模块设置已保存", "success"); } catch (e) { toast(e.message, "error"); }
    };
  }).catch(e => toast(e.message, "error"));
}
function openCardManager() {
  const selected = cards();
  const available = state.modules.filter(m => !selected.includes(m.name));
  const node = modal("添加首页卡片", `<div class="install-list">${available.length ? available.map(m => `<button class="install-item card-add-item" data-add-card="${esc(m.name)}"><div><strong>${esc(m.name)}</strong><small>${esc(m.purpose || "模块卡片")}</small></div><b>＋</b></button>`).join("") : '<p class="modal-note">所有可用模块卡片都已添加。</p>'}</div>`);
  $$("[data-add-card]", node).forEach(item => item.onclick = () => {
    const name = item.dataset.addCard;
    localStorage.setItem(CARDS_KEY, JSON.stringify([...selected, name]));
    node.remove();
    const grid = $("#homeGrid");
    if (grid) grid.insertAdjacentHTML("beforeend", pluginCard(name));
    toast("卡片已添加", "success");
  });
}
function locateCity(node) {
  if (!navigator.geolocation) { toast("当前浏览器不支持定位", "error"); return; }
  toast("正在获取当前位置…");
  navigator.geolocation.getCurrentPosition(async pos => {
    try { await api.post("/api/profile/locate", { lat: pos.coords.latitude, lon: pos.coords.longitude }); state.profile = await api.get("/api/profile"); node?.remove(); render(); toast("已更新为当前位置", "success"); }
    catch (e) { toast(e.message, "error"); }
  }, () => toast("未获得定位权限，请检查浏览器设置", "error"), { timeout: 6000, maximumAge: 600000 });
}

function renderLogin() {
  applyTheme();
  app.innerHTML = `<main class="auth-layout"><div class="auth-atmosphere"></div><section class="auth-card"><div class="auth-mark">✦</div><span class="card-label">PERSONAL ASSISTANT</span><h1 class="auth-title">欢迎回来</h1><p class="auth-lead">进入你的 wechat-claw 工作台。</p><form id="loginForm"><label class="field">管理密码<input id="password" type="password" placeholder="输入管理密码" autofocus></label><button class="btn btn-primary auth-submit" type="submit">进入工作台 <span>→</span></button></form><p class="auth-help">忘记密码？删除 <code>.config/admin.password</code> 后重新运行初始化向导。</p></section></main><div class="toast"></div>`;
  $("#loginForm").addEventListener("submit", async e => {
    e.preventDefault();
    const button = $(".auth-submit"); button.disabled = true; button.textContent = "验证中…";
    try { const data = await api.post("/api/auth", { password: $("#password").value }); api.setToken(data.token); location.href = "/admin.html"; }
    catch (error) { toast(error.message, "error"); button.disabled = false; button.innerHTML = "进入工作台 <span>→</span>"; }
  });
}

const WIZARD_STEPS = ["环境体检", "opencode", "项目装配", "配置生成", "扫码登录", "启动服务"];
const WIZARD_TITLES = ["先把基础准备好。", "准备对话引擎。", "装配项目依赖。", "给后台设一道门。", "连接你的微信。", "你的助理准备好了。"];
const WIZARD_DESC = ["检查这台机器是否满足 wechat-claw 的运行条件。", "检测并准备 opencode 运行时。", "创建虚拟环境并安装依赖。", "设置进入后台所需的管理密码。", "扫描二维码连接微信。", "进入小助手工作台。"];
function renderWizard() {
  let current = 0; const done = new Set(); let ocPollTimer = null; let asmPollTimer = null;

  function markDone(i) {
    done.add(i);
    const stepEl = $(`[data-step="${i}"]`); if (stepEl) { stepEl.classList.add("done"); const b = stepEl.querySelector("b"); if (b) b.textContent = "✓"; }
    $("#wizardHint").textContent = ""; $("#wizardNext").disabled = false;
  }

  /* ---- opencode 自动安装（第二步） ---- */
  function ocPost(path) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(r => r.json().catch(() => ({})));
  }
  function ocArea(html) { const a = $("#ocArea"); if (a) a.innerHTML = html; }
  function ocInstalled(version) {
    ocArea(`<div class="check-row ok"><b>✓</b><span>opencode 已安装</span><small>${esc(version || "")}</small></div>`);
    const btn = $("#ocInstall"); if (btn) { btn.textContent = "✓ 已安装"; btn.disabled = true; }
    ocHideProgress();
    markDone(1);
  }
  function ocMissing() {
    ocArea(`<div class="check-row fail"><b>!</b><span>未检测到 opencode</span><small>点击「自动安装 opencode」由系统后台安装</small></div>`);
    const btn = $("#ocInstall"); if (btn) { btn.textContent = "自动安装 opencode"; btn.disabled = false; }
    ocHideProgress();
  }
  function ocHideProgress() {
    // 隐藏"准备安装…/进度条/日志"区，避免与检测结果区状态不一致
    const st = $("#ocStage"); if (st) st.closest(".check-row").style.display = "none";
    const wrap = $("#ocBarWrap"); if (wrap) wrap.style.display = "none";
    const log = $("#ocLog"); if (log) log.style.display = "none";
  }
  function ocDetect(cb) {
    ocArea(`<div class="check-row"><b>…</b><span>正在检测 opencode…</span></div>`);
    ocPost("/api/opencode/detect").then(d => {
      if (d.already) { ocInstalled(d.version); cb && cb(true); }
      else if (d.missing) { ocMissing(); cb && cb(false); }
      else { ocArea(`<div class="check-row fail"><b>!</b><span>${esc(d.error || "检测失败")}</span></div>`); cb && cb(false); }
    }).catch(e => { ocArea(`<div class="check-row fail"><b>!</b><span>${esc(e.message)}</span></div>`); cb && cb(false); });
  }
  function ocPollStart() {
    const btn = $("#ocInstall"); if (btn) { btn.disabled = true; btn.textContent = "安装中…"; }
    const progress = $("#ocProgress"); if (progress) progress.style.display = "";
    const barWrap = $("#ocBarWrap"); if (barWrap) barWrap.style.display = "";
    clearInterval(ocPollTimer);
    ocPollTimer = setInterval(() => {
      api.get("/api/opencode/status").then(d => {
        const stage = $("#ocStage"); if (stage && d.stage) stage.textContent = d.stage;
        const log = $("#ocLog");
        if (log && d.lines && d.lines.length) { const div = document.createElement("div"); div.className = "log-line"; div.textContent = d.lines[d.lines.length - 1]; log.appendChild(div); }
        const bar = $("#ocBar");
        if (bar && log) bar.style.width = Math.min(90, 10 + log.children.length * 5) + "%";
        if (d.done) {
          clearInterval(ocPollTimer);
          const b2 = $("#ocBar"); if (b2) b2.style.width = "100%";
          if (d.ok) ocDetectWithRetry(5);
          else { const b = $("#ocInstall"); if (b) { b.textContent = "自动安装 opencode"; b.disabled = false; } ocArea(`<div class="check-row fail"><b>!</b><span>安装失败，可重试</span></div>`); }
        }
      }).catch(() => {});
    }, 1500);
  }
  // 安装完成后的确认带自动重试（Windows 刚解压/Defender 扫描期 --version 瞬时失败，
  // 重试窗口 ~7.5s）；确认期间显示"正在确认安装结果"，不误导为"未检测到"
  function ocDetectWithRetry(attempts) {
    ocArea(`<div class="check-row"><b>…</b><span>正在确认 opencode 安装结果…</span></div>`);
    ocDetect(ok => {
      if (!ok && attempts > 0) setTimeout(() => ocDetectWithRetry(attempts - 1), 1500);
      else if (!ok) ocMissing();
    });
  }
  /* ---- 启动服务（第六步）：自启动开关 + 服务配置 ---- */
  async function svcUp() {
    const area = $("#svcArea");
    const toggleEl = document.querySelector('[data-toggle-key="autostart-on"]');
    const on = !!(toggleEl && toggleEl.classList.contains("on"));
    const next = $("#wizardNext"); if (next) next.disabled = true;
    if (area) area.innerHTML = `<div class="check-row"><b>…</b><span>正在配置服务…</span></div>`;
    try {
      const d = await api.post("/api/service/up", { autostart: on });
      const rows = (d.steps || []).map(s => `<div class="check-row ${s.ok ? "ok" : "fail"}"><b>${s.ok ? "✓" : "!"}</b><span>${esc(s.cmd)}</span>${s.out ? `<small>${esc(s.out)}</small>` : ""}</div>`).join("");
      if (area) area.innerHTML = rows || `<div class="check-row ok"><b>✓</b><span>完成</span></div>`;
      if (d.ok) { toast("服务配置完成", "success"); setTimeout(() => { location.href = "/admin.html"; }, 1200); }
      else toast("服务配置未完成，可重试", "error");
    } catch (e) {
      if (area) area.innerHTML = `<div class="check-row fail"><b>!</b><span>${esc(e.message)}</span></div>`;
    } finally {
      if (next) next.disabled = false;
    }
  }
  function ocInstall() {
    const btn = $("#ocInstall"); btn.disabled = true; btn.textContent = "准备安装…";
    ocPost("/api/opencode/install").then(d => {
      if (d.already) { ocInstalled(d.version); return; }
      ocPollStart();
    }).catch(e => { btn.disabled = false; btn.textContent = "自动安装 opencode"; ocArea(`<div class="check-row fail"><b>!</b><span>${esc(e.message)}</span></div>`); });
  }

  /* ---- 扫码登录（第五步） ---- */
  let loginPollTimer = null;
  function loginStart() {
    const banner = $("#loginBanner"); const box = $("#qrBox");
    clearInterval(loginPollTimer); loginPollTimer = null;
    if (banner) { banner.className = "login-banner waiting"; banner.textContent = "⏳ 获取二维码…"; }
    api.post("/api/login/setup").then(d => {
      if (d.already) {
        if (banner) { banner.className = "login-banner done"; banner.textContent = "✅ 已登录（复用已有会话）"; }
        markDone(4);
        return;
      }
      if (box && d.qr_url) box.innerHTML = `<img src="https://api.qrserver.com/v1/create-qr-code/?size=170x170&data=${encodeURIComponent(d.qr_url)}" alt="二维码">`;
      if (banner) { banner.className = "login-banner waiting"; banner.textContent = "⏳ 等待扫码…"; }
      loginPollTimer = setInterval(() => {
        api.get("/api/login/status").then(s => {
          if (s.done) {
            clearInterval(loginPollTimer);
            if (banner) { banner.className = "login-banner done"; banner.textContent = "✅ 登录成功"; }
            markDone(4);
          } else if (s.status === "expired") {
            clearInterval(loginPollTimer);
            if (banner) { banner.className = "login-banner error"; banner.textContent = "二维码已失效，请点击刷新"; }
          }
          // error/pending 等瞬时状态：不判失效，继续轮询等待
        }).catch(() => {});
      }, 1500);
    }).catch(e => {
      if (banner) { banner.className = "login-banner error"; banner.textContent = "二维码获取失败：" + e.message; }
    });
  }

  const draw = () => {
    applyTheme();
    const ocPanel = current === 1 ? `
      <div class="check-results" id="ocArea"><div class="check-row"><b>…</b><span>正在检测 opencode…</span></div></div>
      <div id="ocProgress" style="display:none">
        <div class="check-row"><b>…</b><span id="ocStage">准备安装…</span></div>
        <div class="progress" id="ocBarWrap" style="display:none"><div class="progress-bar" id="ocBar" style="width:0%"></div></div>
        <div class="modal-log" id="ocLog" style="margin-top:10px"></div>
      </div>
      <div class="wizard-actions">
        <button class="btn btn-primary" id="ocInstall">自动安装 opencode</button>
        <button class="btn btn-secondary" id="ocRedetect">重新检测</button>
      </div>` : "";
    const asmPanel = current === 2 ? `
      <div class="check-results" id="asmArea"><div class="check-row"><b>…</b><span>正在检测装配状态…</span></div></div>
      <div class="progress" id="asmBarWrap" style="display:none"><div class="progress-bar" id="asmBar" style="width:0%"></div></div>
      <div class="modal-log" id="asmLog" style="display:none;margin-top:10px"></div>
      <div class="wizard-actions"><button class="btn btn-primary" id="asmAction">开始装配</button></div>` : "";
    const actionBtn = current === 1 || current === 2 || current === 4 || current === 5 ? "" : `<div class="wizard-actions"><button class="btn btn-primary" id="wizardAction">${["开始体检", "检测 opencode", "开始装配", "生成配置", "获取二维码", "进入工作台"][current]} <span>→</span></button></div>`;
    const svcPanel = current === 5 ? `
      <div class="check-results" id="svcArea"><div class="check-row"><b>…</b><span>点击「启动服务并进入工作台」开始</span></div></div>
      <label class="field toggle-field">${toggle(false, "autostart-on")}<span>开机自动启动 bridge</span><small>登录后自动运行微信消息桥接；升级为系统服务时需 UAC 授权（不勾选则本次不注册，可在工作台随时开启）</small></label>` : "";
    const loginPanel = current === 4 ? `
      <div class="login-row">
        <div class="qr-box" id="qrBox">${done.has(4) ? '<div class="qr-done">✅</div>' : "二维码区域"}</div>
        <div class="login-side">
          <div class="login-banner ${done.has(4) ? "done" : "waiting"}" id="loginBanner">${done.has(4) ? "✅ 已登录（会话可复用）" : "⏳ 获取二维码…"}</div>
          <div class="login-actions">
            <button class="btn btn-primary" id="wizardAction">获取二维码 <span>→</span></button>
            <button class="btn btn-secondary" id="qrRefresh">↻ 刷新二维码</button>
          </div>
        </div>
      </div>` : "";
    app.innerHTML = `<main class="wizard-layout"><header class="wizard-top"><div class="brand-lockup"><span>✦</span><div><strong>wechat-claw</strong><small>初始化向导</small></div></div><div class="theme-slot">${themeCard()}</div></header>
      <section class="wizard-progress">${WIZARD_STEPS.map((name, i) => `<button class="wizard-step ${i === current ? "active" : ""} ${done.has(i) ? "done" : ""}" data-step="${i}"><b>${done.has(i) ? "✓" : i + 1}</b><span>${name}</span></button>`).join("")}</section>
      <section class="wizard-card"><div class="wizard-copy"><span class="card-label">STEP ${String(current + 1).padStart(2, "0")}</span><h1>${WIZARD_TITLES[current]}</h1><p>${WIZARD_DESC[current]}</p></div>${current === 3 ? `<div class="wizard-form"><label class="field">管理密码<input id="wizardPwd" type="password" placeholder="至少 6 位"></label></div>` : ""}${ocPanel}${asmPanel}${loginPanel}${svcPanel}${actionBtn}<div id="wizardResult" class="check-results"></div></section>
      <footer class="wizard-footer"><button class="btn btn-secondary" id="wizardPrev" ${current === 0 ? "disabled" : ""}>← 上一步</button><span id="wizardHint"></span><button class="btn btn-primary" id="wizardNext">${current === 5 ? "进入工作台 →" : "下一步 →"}</button></footer></main><div class="toast"></div>`;
    bindThemeControls();
    $$("[data-step]").forEach(b => b.onclick = () => { const i = Number(b.dataset.step); if (i <= current) { current = i; draw(); } });
    $("#wizardPrev").onclick = () => { if (current) { current--; draw(); } };
    $("#wizardNext").onclick = () => { if (current === 5) { svcUp(); return; } if (done.has(current)) { current++; draw(); } else { $("#wizardHint").textContent = "请先完成当前步骤"; } };
    if (current === 1) {
      $("#ocInstall").onclick = ocInstall;
      $("#ocRedetect").onclick = () => ocDetect();
      // 状态优先：安装中 → 轮询（done 后 ocDetect）；已装完 → 直接检测渲染；
      // 从未启动 → 检测。修复"装完必须刷新才显示已装"的竞态
      api.get("/api/opencode/status").then(d => {
        if (d.started) { if (!d.done) ocPollStart(); else ocDetect(); }
        else ocDetect();
      }).catch(() => ocDetect());
    }
    if (current === 2) {
      const asmArea = $("#asmArea");
      const asmAction = $("#asmAction");
      const asmReady = () => { asmArea.innerHTML = `<div class="check-row ok"><b>✓</b><span>依赖已就绪，无需装配</span></div>`; asmAction.style.display = "none"; markDone(2); };
      const asmStart = () => {
        asmAction.disabled = true; asmAction.textContent = "装配中…";
        const wrap = $("#asmBarWrap"), bar = $("#asmBar"), log = $("#asmLog");
        wrap.style.display = ""; log.style.display = "";
        asmPollTimer = setInterval(() => {
          api.get("/api/assemble/status").then(d => {
            const lines = d.lines || [];
            if (log && lines.length) { const div = document.createElement("div"); div.className = "log-line"; div.textContent = lines[lines.length - 1]; log.appendChild(div); }
            if (bar) bar.style.width = Math.min(90, 10 + lines.length * 5) + "%";
            if (d.done) {
              clearInterval(asmPollTimer);
              if (bar) bar.style.width = "100%";
              if (d.ok) { asmArea.innerHTML = `<div class="check-row ok"><b>✓</b><span>依赖装配完成</span></div>`; markDone(2); }
              else { asmAction.disabled = false; asmAction.textContent = "重试装配"; asmArea.innerHTML = `<div class="check-row fail"><b>!</b><span>装配失败，可重试</span></div>`; }
            }
          }).catch(() => {});
        }, 1500);
        api.post("/api/assemble").catch(e => { asmAction.disabled = false; asmAction.textContent = "开始装配"; asmArea.innerHTML = `<div class="check-row fail"><b>!</b><span>${esc(e.message)}</span></div>`; });
      };
      asmAction.onclick = asmStart;
      api.post("/api/assemble/detect").then(d => {
        if (d.ready) asmReady();
        else asmArea.innerHTML = `<div class="check-row fail"><b>!</b><span>${esc(d.reason || "依赖未就绪")}</span><small>点击「开始装配」自动安装</small></div>`;
      }).catch(e => asmArea.innerHTML = `<div class="check-row fail"><b>!</b><span>${esc(e.message)}</span></div>`);
    }
    if (current === 4) {
      $("#qrRefresh").onclick = loginStart;
      // 自动获取二维码（未登录且未在轮询时）；已登录（done）则保持已登录态不重置
      if (!done.has(4) && !loginPollTimer) loginStart();
    }
    if (current === 5) {
      // 进入第 6 步即自动启动 bridge（默认不勾选 = 仅本次运行）；开关点击立即生效
      svcUp();
    }
    const action = $("#wizardAction");
    if (action) action.onclick = async () => {
      const result = $("#wizardResult");
      try {
        if (current === 0) { const d = await api.post("/api/env_check"); result.innerHTML = (d.items || []).map(x => `<div class="check-row ${x.ok ? "ok" : "fail"}"><b>${x.ok ? "✓" : "!"}</b><span>${esc(x.name)}</span><small>${esc(x.value || "")}</small></div>`).join(""); }
        else if (current === 3) { const pwd = $("#wizardPwd").value; if (pwd.length < 6) throw new Error("密码至少 6 位"); await api.post("/api/config/gen", { password: pwd }); result.innerHTML = `<div class="check-row ok"><b>✓</b><span>配置已生成</span></div>`; }
        else if (current === 4) { loginStart(); return; }
        else { location.href = "/admin.html"; return; }
        markDone(current);
      } catch (error) { result.innerHTML = `<div class="check-row fail"><b>!</b><span>${esc(error.message)}</span></div>`; }
    };
  };
  draw();
}

const APP_TYPE = document.body.dataset.app;
if (APP_TYPE === "login") renderLogin();
else if (APP_TYPE === "wizard") renderWizard();
else {
  applyTheme();
  if (!api.token) { location.href = "/login.html"; }
  else {
    app.innerHTML = `<div class="loading-screen"><div class="loader-orb">✦</div><p>正在加载工作台…</p></div>`;
    loadData().then(render).catch(e => { app.innerHTML = `<div class="error-screen"><span>!</span><h1>页面加载失败</h1><p>${esc(e.message)}</p><button class="btn btn-primary" onclick="location.reload()">重新加载</button></div>`; });
  }
}
