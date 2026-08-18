/* wechat-claw web demo · 主题切换
   主题: light / dark / green / system
   控件: .theme-btn（文字胶囊，向导用）与 .theme-dot（色块，后台用）
   持久化: localStorage "wc-theme"；system 跟随 prefers-color-scheme */
(function () {
  var KEY = "wc-theme";

  function controls() {
    return document.querySelectorAll(".theme-btn, .theme-dot");
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    controls().forEach(function (el) {
      el.classList.toggle("active", el.dataset.theme === theme);
    });
  }

  function current() {
    return localStorage.getItem(KEY) || "light";
  }

  document.addEventListener("DOMContentLoaded", function () {
    apply(current());
    controls().forEach(function (el) {
      el.addEventListener("click", function () {
        var t = el.dataset.theme;
        localStorage.setItem(KEY, t);
        apply(t);
      });
    });
  });

  /* 系统主题变化时跟随（仅 system 模式） */
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
    if (current() === "system") apply("system");
  });
})();
