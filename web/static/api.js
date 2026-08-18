/* wechat-claw web · API 封装
   - fetch 统一封装（JSON / X-Auth 会话 header / 401 跳登录）
   - 会话 token 存 localStorage("wc-auth") */
(function () {
  "use strict";
  var TOKEN_KEY = "wc-auth";

  var api = {
    get token() { return localStorage.getItem(TOKEN_KEY) || ""; },
    setToken: function (t) { localStorage.setItem(TOKEN_KEY, t); },
    clearToken: function () { localStorage.removeItem(TOKEN_KEY); },

    request: async function (method, path, body) {
      var headers = {};
      if (body !== undefined) headers["Content-Type"] = "application/json";
      var t = this.token;
      if (t) headers["X-Auth"] = t;
      var resp;
      try {
        resp = await fetch(path, {
          method: method,
          headers: headers,
          body: body !== undefined ? JSON.stringify(body) : undefined,
        });
      } catch (e) {
        throw new Error("无法连接本地服务，请确认已启动 web 服务");
      }
      if (resp.status === 401 && path !== "/api/auth") {
        this.clearToken();
        location.href = "login.html";
        throw new Error("登录已失效");
      }
      var data = {};
      try { data = await resp.json(); } catch (e) { /* 非 JSON */ }
      if (!resp.ok && !data.ok) throw new Error(data.error || ("HTTP " + resp.status));
      return data;
    },

    get: function (p) { return this.request("GET", p); },
    post: function (p, b) { return this.request("POST", p, b || {}); },

    /* 长任务轮询：递归 setTimeout，直至 done */
    poll: function (statusPath, onLines, onDone, onError, interval) {
      interval = interval || 1200;
      var self = this;
      function tick() {
        self.get(statusPath).then(function (data) {
          if (data.lines && data.lines.length && onLines) onLines(data.lines);
          if (data.done) { if (onDone) onDone(data.ok); }
          else setTimeout(tick, interval);
        }).catch(function (e) { if (onError) onError(e); });
      }
      tick();
    },
  };

  window.api = api;
})();
