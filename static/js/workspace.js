import { $, icon } from "./core.js?v=__APP_VERSION__";

export function workspaceAction(action) {
  document.dispatchEvent(new CustomEvent("workspace:action", { detail: action }));
}

export function initWorkspace() {
  const themeToggle = $("themeToggle");
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const label = theme === "dark" ? "切换浅色外观" : "切换深色外观";
    themeToggle.setAttribute("aria-label", label);
    themeToggle.title = label;
    themeToggle.replaceChildren(icon(theme === "dark" ? "sun" : "moon"));
    document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#101014" : "#fcfcff";
  }
  let savedTheme;
  try { savedTheme = localStorage.getItem("personal-agent-appearance"); } catch { /* Appearance remains usable when storage is unavailable. */ }
  applyTheme(savedTheme === "light" ? "light" : "dark");
  themeToggle.addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(theme);
    try { localStorage.setItem("personal-agent-appearance", theme); } catch { /* Keep the current appearance for this session. */ }
  });
  const sidebar = $("workspaceSidebar");
  const content = $("workspaceContent");
  const toggle = $("sidebarToggle");
  const scrim = $("sidebarScrim");
  const narrow = window.matchMedia("(max-width: 820px)");
  let expanded = !narrow.matches;

  function setExpanded(value, restoreFocus = false) {
    expanded = value;
    document.body.classList.toggle("sidebar-collapsed", !value);
    document.body.classList.toggle("sidebar-open", narrow.matches && value);
    sidebar.inert = !value;
    content.inert = narrow.matches && value;
    scrim.hidden = !narrow.matches || !value;
    toggle.setAttribute("aria-expanded", String(value));
    toggle.setAttribute("aria-label", value ? "收起导航" : "展开导航");
    if (restoreFocus) toggle.focus({ preventScroll: true });
    else if (narrow.matches && value) $("workspaceSearch").focus();
  }

  toggle.addEventListener("click", () => setExpanded(!expanded));
  scrim.addEventListener("click", () => setExpanded(false, true));
  sidebar.addEventListener("click", (event) => {
    if (narrow.matches && expanded && event.target.closest("button")) setExpanded(false, true);
  }, true);
  narrow.addEventListener("change", () => setExpanded(!narrow.matches, sidebar.contains(document.activeElement)));
  $("workspaceSearch").addEventListener("click", () => workspaceAction("search"));
  if (!/Mac|iPhone|iPad/.test(navigator.platform)) $("searchShortcut").textContent = "Ctrl K";
  $("workspaceDate").textContent = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "long", day: "numeric", weekday: "short" }).format(new Date());

  document.querySelectorAll("[data-workspace-action]").forEach((button) => {
    button.addEventListener("click", () => workspaceAction(button.dataset.workspaceAction));
  });
  document.addEventListener("workspace:action", () => {
    $("composerTools").open = false;
    if (narrow.matches && expanded) setExpanded(false, true);
  });
  document.addEventListener("workspace:view", (event) => {
    $("workspaceTitle").textContent = event.detail.view === "chat" ? "Personal Agent" : event.detail.title;
    if (narrow.matches && expanded) setExpanded(false, true);
  });
  document.addEventListener("pointerdown", (event) => {
    if (!$("composerTools").contains(event.target)) $("composerTools").open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("craftMobileCreate").open) $("craftMobileCreate").open = false;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k" && !document.querySelector("dialog[open]")) {
      event.preventDefault();
      workspaceAction("search");
    }
    if (event.key === "Escape") {
      if (narrow.matches && expanded) { event.preventDefault(); setExpanded(false, true); }
      if ($("composerTools").open) { $("composerTools").open = false; $("composerTools").querySelector("summary").focus(); }
    }
    if (event.key === "Tab" && narrow.matches && expanded) {
      const controls = [...sidebar.querySelectorAll("button:not(:disabled), a[href]")];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  setExpanded(expanded);
}
