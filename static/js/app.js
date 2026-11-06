import { initCraft, resetCraft } from "./craft.js?v=__APP_VERSION__";
import { initWorkspace } from "./workspace.js?v=__APP_VERSION__";
import { initSchedules } from "./schedules.js?v=__APP_VERSION__";
import { initArrangements, resetArrangements } from "./arrangements.js?v=__APP_VERSION__";
import { $, h, DEBUG, VERSION, toast } from "./core.js?v=__APP_VERSION__";
import { api } from "./api.js?v=__APP_VERSION__";
import { currentUserId, ensureSession } from "./identity.js?v=__APP_VERSION__";
import { store } from "./store.js?v=__APP_VERSION__";
import { initThread } from "./thread.js?v=__APP_VERSION__";
import { initComposer, focusComposer, prefillComposer } from "./composer.js?v=__APP_VERSION__";
import { initDrawer, loadProfile, resetDrawer } from "./drawer.js?v=__APP_VERSION__";
import {
  sendComposer,
  askMemory,
  restoreTranscript,
  showHero,
  resetThread,
  loadProactive,
  noteProfileEdited,
} from "./flows.js?v=__APP_VERSION__";

let health = null;
let workspaceInitialized = false;
let lastUserId = currentUserId();

function badge(text, cls = "") {
  return h("span", { class: `badge${cls ? ` ${cls}` : ""}` }, [h("span", { class: "dot", "aria-hidden": "true" }), text]);
}

function renderDebugBadges() {
  if (!DEBUG) return;
  const box = $("debugBadges");
  box.replaceChildren();
  if (!health) {
    box.append(badge("服务离线", "is-off"), badge(`ui ${VERSION}`), badge(`user ${currentUserId()}`));
    return;
  }
  const fast = health.fast || (health.routing && health.routing.fast) || {};
  const strong = health.strong || (health.routing && health.routing.strong) || {};
  box.append(
    badge(
      `llm ${health.llm || "?"} · fast ${fast.provider || "?"}/${fast.model || "?"} · strong ${strong.provider || "?"}/${strong.model || "?"}`,
      health.llm === "live" ? "is-live" : "is-mock"
    ),
    badge(`api ${health.version || "?"} · ui ${VERSION}`),
    badge(`user ${currentUserId()}`)
  );
}

async function loadHealth() {
  try {
    health = await api.health();
  } catch {
    health = null;
  }
  renderDebugBadges();
}

function startConversation() {
  if (!restoreTranscript()) showHero();
  loadProactive();
}

function onUserChange() {
  const uid = currentUserId();
  if (uid === lastUserId) return;
  lastUserId = uid;
  store.reload();
  resetThread();
  resetDrawer();
  resetArrangements();
  resetCraft({ preserveNavigation: !workspaceInitialized });
  workspaceInitialized = true;
  startConversation();
  renderDebugBadges();
  toast("已切换账号，档案和对话记录随之切换");
}

function onClearLocal() {
  store.clear();
  resetThread();
  showHero();
  toast("已清除这台设备上的对话记录");
  focusComposer();
}

async function boot() {
  if (DEBUG) document.body.classList.add("is-debug");
  initThread();
  initSchedules();
  initWorkspace();
  initCraft();
  initArrangements();
  initComposer({ onSubmit: sendComposer });
  initDrawer({ onUserChange, onProfileEdited: noteProfileEdited, onClearLocal });
  $("btnMemory").addEventListener("click", () => askMemory());
  document.addEventListener("craft:quote", (event) => {
    document.dispatchEvent(new CustomEvent("craft:navigate", { detail: "chat" }));
    const excerpt = Array.from(event.detail.content || "").slice(0, 2000).join("");
    if (!prefillComposer(`请根据《${event.detail.title}》的以下原文片段讨论。\n\n${excerpt}\n\n我的问题是：`)) toast("输入框已有内容，可以发送或整理后再引用文档");
  });

  try {
    await ensureSession();
  } catch {
    /* requests retry the session themselves */
  }
  onUserChange();
  const healthReady = loadHealth();
  loadProfile();
  await Promise.race([healthReady, new Promise((resolve) => window.setTimeout(resolve, 1500))]);
  startConversation();
  if (!workspaceInitialized) { resetCraft({ preserveNavigation: true }); workspaceInitialized = true; }
  if (!document.getElementById("main").hidden && window.matchMedia("(pointer: fine)").matches) focusComposer();
}

boot();
