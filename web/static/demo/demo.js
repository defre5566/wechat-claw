(function () {
  "use strict";
  var toast = document.querySelector(".toast");
  var timer;

  function show(message) {
    if (!toast) return;
    toast.textContent = message || "这是静态 demo";
    toast.classList.add("show");
    clearTimeout(timer);
    timer = setTimeout(function () { toast.classList.remove("show"); }, 1800);
  }

  document.querySelectorAll("[data-toast]").forEach(function (button) {
    button.addEventListener("click", function (event) {
      event.preventDefault();
      show(button.dataset.toast);
    });
  });

  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      document.body.classList.toggle("demo-dim");
      show(document.body.classList.contains("demo-dim") ? "已切换为低亮度预览" : "已恢复默认亮度");
    });
  });

  document.querySelectorAll(".mini-switch input").forEach(function (input) {
    input.addEventListener("change", function () {
      show(input.checked ? "模块已启用（静态预览）" : "模块已停用（静态预览）");
    });
  });

  document.querySelectorAll(".filter-bar button:not(.filter-search)").forEach(function (button) {
    button.addEventListener("click", function () {
      document.querySelectorAll(".filter-bar button").forEach(function (item) { item.classList.remove("filter-active"); });
      button.classList.add("filter-active");
      show("已切换筛选视图");
    });
  });
})();
