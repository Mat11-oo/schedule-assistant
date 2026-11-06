import { $, h, icon, toast } from "./core.js?v=__APP_VERSION__";
import { api } from "./api.js?v=__APP_VERSION__";
import { ensureSession, currentUserId } from "./identity.js?v=__APP_VERSION__";

let workspace = null;
let workspaceOwner = "";
let request = 0;
let saving = false;
let tabs = [];
let active = "home";
let visits = [];
let visitIndex = -1;
let inspectorTab = "outline";
let documentContext = null;
let questionMarker = null;
let inspectorReturnFocus = null;
const expanded = new Set();
const emit = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail }));
const note = (value) => h("p", { class: "ar-note", text: value });
const button = (label, action, primary = false) => h("button", {
  type: "button", class: `btn btn-${primary ? "primary" : "ghost"} btn-sm`, text: label, onClick: action,
});
const date = (value) => value ? new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", dateStyle: "medium",
}).format(new Date(value)) : "未提供";

export function getWorkspace() {
  return workspaceOwner === currentUserId() ? workspace : null;
}

function sheet(title) {
  const dialog = $("arrangementDialog");
  dialog.replaceChildren(h("div", { class: "ar-dialog-head" }, [
    h("h2", { id: "arDialogTitle", text: title }), button("关闭", () => dialog.close()),
  ]));
  if (!dialog.open) dialog.showModal();
  return dialog;
}

export async function loadWorkspace() {
  const ticket = ++request;
  try {
    await ensureSession();
    const owner = currentUserId();
    const [result, starred, tags] = await Promise.all([
      api.libraryWorkspace(),
      api.librarySearch({ starred: "true", limit: "20" }).catch(() => null),
      api.libraryTags().catch(() => null),
    ]);
    if (ticket !== request || owner !== currentUserId()) return;
    workspace = result.data;
    workspaceOwner = owner;
    renderNavigation(starred?.data.items ?? null, tags?.data.tags ?? null);
    emit("craft:workspace-ready", workspace);
  } catch (error) {
    if (ticket !== request) return;
    $("craftStarred").replaceChildren(note("工作空间暂时无法读取"), button("重新读取", loadWorkspace));
    $("craftFolders").replaceChildren();
  }
}

export async function mutateWorkspace(action) {
  if (saving) throw new Error("上一项整理正在保存，请稍后再操作");
  const state = getWorkspace();
  if (!state) throw new Error("工作空间尚未读取，请先重新读取");
  saving = true;
  const owner = currentUserId();
  try {
    const response = await api.libraryWorkspaceAction({ ...action, version: action.version ?? state.version, confirmed: true });
    if (owner !== currentUserId()) return null;
    workspace = response.data;
    await loadWorkspace();
    if (owner !== currentUserId()) return null;
    emit("craft:workspace-changed", workspace);
    return workspace;
  } catch (error) {
    if (owner === currentUserId() && error.status === 409) await loadWorkspace();
    throw error;
  } finally { if (owner === currentUserId()) saving = false; }
}

export async function starSource(source, value) {
  try { return !!await mutateWorkspace({ action: "star_source", source_id: source.id, starred: value }); }
  catch (error) { toast(error.message || "收藏保存失败"); return false; }
}

function folderPath(folder, folders) {
  const parents = new Map(folders.map((item) => [item.id, item]));
  const names = [], visited = new Set();
  let current = folder;
  while (current && !visited.has(current.id)) {
    visited.add(current.id); names.unshift(current.name); current = parents.get(current.parent_id);
  }
  return names.join(" / ");
}

function isWithinFolder(folder, ancestor, folders) {
  const parents = new Map(folders.map((item) => [item.id, item.parent_id]));
  let current = folder.id;
  while (current) {
    if (current === ancestor) return true;
    current = parents.get(current);
  }
  return false;
}

function workspaceError(host, error, reload) {
  host.replaceChildren(h("span", { text: error.message || "保存失败，请重试" }));
  if (error.status === 409) host.append(button("重新读取后再编辑", reload));
}

export function moveSourceDialog(source) {
  const state = getWorkspace();
  if (!state) { toast("请先读取工作空间"); return; }
  const dialog = sheet("移动文档");
  const select = h("select", { class: "field-input", name: "folder", "aria-label": "目标文件夹" }, [
    h("option", { value: "", text: "未分类" }),
    ...state.folders.map((folder) => h("option", { value: folder.id, text: folderPath(folder, state.folders) })),
  ]);
  select.value = state.entries[source.root_id || source.id]?.folder_id || "";
  const error = h("div", { class: "ar-error", role: "alert" });
  const save = h("button", { type: "submit", class: "btn btn-primary", text: "移动到此处" });
  const form = h("form", { class: "ar-form" }, [note(source.title), select, error, save]);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); save.disabled = true;
    try {
      if (!await mutateWorkspace({ action: "move_source", source_id: source.id, folder_id: select.value, version: state.version })) return;
      if (form.isConnected && dialog.open) dialog.close();
      toast("文档位置已保存");
    } catch (err) { workspaceError(error, err, () => moveSourceDialog(source)); }
    finally { save.disabled = false; }
  });
  dialog.append(form);
}

export function folderDialog(folder = null, parent = "") {
  const state = getWorkspace();
  if (!state) { toast("工作空间尚未读取完成"); return; }
  const dialog = sheet(folder ? "整理文件夹" : "新建文件夹");
  const name = h("input", { class: "field-input", required: true, maxLength: 60, name: "name", "aria-label": "文件夹名称", value: folder?.name || "", placeholder: "文件夹名称" });
  const parentSelect = h("select", { class: "field-input", name: "parent", "aria-label": "上级文件夹" }, [
    h("option", { value: "", text: "工作空间根目录" }),
    ...state.folders.filter((item) => !folder || !isWithinFolder(item, folder.id, state.folders)).map((item) => h("option", { value: item.id, text: folderPath(item, state.folders) })),
  ]);
  parentSelect.value = folder?.parent_id || parent;
  const error = h("div", { class: "ar-error", role: "alert" });
  const save = h("button", { type: "submit", class: "btn btn-primary", text: "保存文件夹" });
  const form = h("form", { class: "ar-form" }, [name, parentSelect, error, save]);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); save.disabled = true; error.textContent = "";
    try {
      if (!await mutateWorkspace({ action: folder ? "rename_folder" : "create_folder", folder_id: folder?.id || "", parent_id: parentSelect.value, name: name.value, version: state.version })) return;
      if (form.isConnected && dialog.open) dialog.close();
      toast("文件夹已保存");
    } catch (err) { workspaceError(error, err, () => {
      const current = folder && getWorkspace()?.folders.find((item) => item.id === folder.id);
      if (folder && !current) { error.textContent = "这个文件夹已移除，请返回列表。"; return; }
      folderDialog(current || null, parent);
    }); }
    finally { save.disabled = false; }
  });
  if (folder) form.append(button("移除文件夹…", () => {
    const confirm = h("div", { class: "craft-folder-confirm" }, [
      note("移除此文件夹后，文档回到未分类，子文件夹回到根目录。"),
      button("确认移除文件夹", async () => {
        try { if (await mutateWorkspace({ action: "remove_folder", folder_id: folder.id, version: state.version }) && form.isConnected && dialog.open) dialog.close(); }
        catch (err) { error.textContent = err.message; }
      }),
    ]);
    form.querySelector(".craft-folder-confirm")?.remove(); form.append(confirm);
  }));
  dialog.append(form); name.focus();
}

export function documentDrop(target, folderId) {
  target.addEventListener("dragover", (event) => {
    if (![...event.dataTransfer.types].some((type) => type.startsWith("application/x-pa-"))) return;
    event.preventDefault(); event.stopPropagation(); target.classList.add("is-drop-target");
  });
  target.addEventListener("dragleave", () => target.classList.remove("is-drop-target"));
  target.addEventListener("drop", async (event) => {
    event.preventDefault(); event.stopPropagation(); target.classList.remove("is-drop-target");
    const sourceId = event.dataTransfer.getData("application/x-pa-source");
    const folder = event.dataTransfer.getData("application/x-pa-folder");
    if (!sourceId && !folder) return;
    try {
      await mutateWorkspace(sourceId ? { action: "move_source", source_id: sourceId, folder_id: folderId } :
        { action: "move_folder", folder_id: folder, parent_id: folderId });
      toast("位置已保存");
    } catch (err) { toast(err.message || "移动失败"); }
  });
}

function folderTree(host) {
  const state = getWorkspace();
  host.replaceChildren();
  if (!state) { host.append(note("文件夹暂未读取完成")); return; }
  const unsorted = h("button", { type: "button", class: "craft-folder-link", "data-folder-id": "unsorted", onClick: () => libraryNavigate({ folder_id: "unsorted" }) }, [icon("inbox"), h("span", { text: "未分类" })]);
  documentDrop(unsorted, ""); host.append(unsorted);
  function branch(parent, container, depth) {
    for (const folder of state.folders.filter((item) => item.parent_id === parent)) {
      const row = h("div", { class: "craft-folder-row", draggable: true });
      row.style.paddingLeft = `${depth * 12}px`;
      row.addEventListener("dragstart", (event) => { event.stopPropagation(); event.dataTransfer.setData("application/x-pa-folder", folder.id); });
      const children = h("div", { class: "craft-folder-children", hidden: !expanded.has(folder.id) });
      const toggle = h("button", { type: "button", class: "btn-icon craft-folder-expand", "aria-label": `展开或收起 ${folder.name}`, "aria-expanded": String(expanded.has(folder.id)), onClick: () => {
        children.hidden = !children.hidden;
        if (children.hidden) expanded.delete(folder.id); else expanded.add(folder.id);
        toggle.setAttribute("aria-expanded", String(!children.hidden));
      } }, [icon("chevron")]);
      row.append(toggle, h("button", { type: "button", class: "craft-folder-link", "data-folder-id": folder.id, onClick: () => libraryNavigate({ folder_id: folder.id }) }, [icon("folder"), h("span", { text: folder.name })]),
        h("button", { type: "button", class: "btn-icon craft-folder-more", "aria-label": `整理 ${folder.name}`, onClick: () => folderDialog(folder) }, [icon("more")]));
      documentDrop(row, folder.id); container.append(row, children); branch(folder.id, children, depth + 1);
    }
  }
  branch("", host, 0);
}

function renderNavigation(starred, tags) {
  const host = $("craftStarred");
  host.replaceChildren(...(starred || []).map((source) => h("button", { type: "button", class: "craft-folder-link", onClick: () => openDocument(source) }, [icon("doc"), h("span", { text: source.title })])));
  if (!starred?.length) host.append(h("p", { class: "craft-nav-empty", text: starred ? "收藏文档以便快速访问" : "收藏文档暂时无法读取" }));
  folderTree($("craftFolders"));
  $("craftTags").replaceChildren(...(tags || []).map((tag) => h("button", { type: "button", class: "craft-folder-link", onClick: () => libraryNavigate({ tag: tag.label }) }, [h("span", { text: "#", "aria-hidden": "true" }), h("span", { text: tag.label })])));
  if (!tags?.length) $("craftTags").append(h("p", { class: "craft-nav-empty", text: tags ? "文档标签会显示在这里" : "标签暂时无法读取" }));
}

export function libraryNavigate(filters = {}) {
  emit("craft:library", filters);
}

export function openDocument(source) {
  emit("craft:open-source", { id: source.id });
}

function persistTabs() {
  try { localStorage.setItem(`pa.document-tabs.${currentUserId()}`, JSON.stringify(tabs)); } catch { /* In-memory tabs remain available. */ }
}

function renderTabs() {
  const host = $("craftTabs"); host.replaceChildren();
  const all = [{ id: "home", title: "首页" }, ...tabs];
  for (const tab of all) {
    const selected = active === tab.id;
    const open = h("button", { type: "button", role: "tab", "aria-selected": String(selected), tabIndex: selected || tab.id === "home" && !all.some((item) => item.id === active) ? 0 : -1, text: tab.title, title: tab.title, onClick: () => {
      if (tab.id === "home") emit("craft:navigate", "home"); else openDocument(tab);
    } });
    const row = h("div", { class: `craft-tab${selected ? " is-active" : ""}` }, [open]);
    if (tab.id !== "home") row.append(h("button", { type: "button", class: "craft-tab-close", "aria-label": `关闭 ${tab.title}`, onClick: () => {
      tabs = tabs.filter((item) => item.id !== tab.id); persistTabs();
      if (selected) {
        if (tabs.length) openDocument(tabs[tabs.length - 1]); else emit("craft:navigate", "home");
      }
      renderTabs();
    } }, [icon("x")]));
    host.append(row);
  }
}

function visitKey(detail) {
  return JSON.stringify([detail.view, detail.sourceId || "", detail.library || null]);
}

function rememberVisit(detail) {
  active = detail.view === "sources" && detail.sourceId ? detail.sourceId : detail.view;
  const key = visitKey(detail);
  if (detail.navigation === "browser") {
    const previous = visits[visitIndex - 1]?.key === key ? visitIndex - 1 : -1;
    const next = visits[visitIndex + 1]?.key === key ? visitIndex + 1 : -1;
    const found = previous >= 0 ? previous : next >= 0 ? next : visits.findIndex((item) => item.key === key);
    if (found >= 0) visitIndex = found;
  }
  if (visits[visitIndex]?.key !== key) {
    visits = visits.slice(0, visitIndex + 1);
    visits.push({ ...detail, key }); visitIndex++;
  }
  $("craftBack").disabled = visitIndex <= 0;
  $("craftForward").disabled = visitIndex >= visits.length - 1;
  renderTabs();
}

function historyStep(delta) {
  if (visits[visitIndex + delta]) window.history.go(delta);
}

function restoreQuestion() {
  if (questionMarker?.isConnected && documentContext?.qa) questionMarker.replaceWith(documentContext.qa);
  questionMarker = null;
}

function renderInspector() {
  restoreQuestion();
  const host = $("craftInspectorBody"); host.replaceChildren();
  document.querySelectorAll("[data-inspector-tab]").forEach((control) => control.setAttribute("aria-selected", String(control.dataset.inspectorTab === inspectorTab)));
  if (!documentContext?.root?.isConnected) {
    host.append(note("打开一份文档，查看目录、来源信息，或基于原文提问。"), button("与小助对话", () => emit("craft:navigate", "chat")));
    return;
  }
  const { source, root, qa } = documentContext;
  if (inspectorTab === "assistant") {
    if (qa) {
      questionMarker = document.createComment("document-questions"); qa.before(questionMarker); host.append(qa);
    } else {
      host.append(note("可以引用这份文档的原文，与小助一起讨论。"), button("引用原文到对话", () => emit("craft:quote", source)));
    }
  } else if (inspectorTab === "info") {
    host.append(h("h3", { text: source.title }), note(`保存于 ${date(source.created_at)}`), note(`原始时间 ${date(source.original_at)}`),
      note(`正文 ${Array.from(source.content || "").length} 字 · 关联事项 ${source.candidates.length} 项`),
      button("移动文档", () => moveSourceDialog(source)));
    const rootId = source.root_id || source.material?.root_id || source.id;
    const starred = !!getWorkspace()?.entries[rootId]?.starred;
    const star = button(starred ? "取消收藏" : "收藏文档", async () => {
      star.disabled = true;
      if (await starSource(source, !starred) && documentContext?.source === source && inspectorTab === "info") renderInspector();
      else star.disabled = false;
    });
    host.append(star);
  } else {
    const headings = [...root.querySelectorAll("h2, h3, h4, h5, h6")].filter((item) => item.textContent.trim());
    for (const heading of headings) host.append(h("button", { type: "button", class: "craft-outline-item", text: heading.textContent, onClick: () => { if (window.matchMedia("(max-width: 820px)").matches) closeInspector(true); heading.scrollIntoView({ block: "start", behavior: "smooth" }); } }));
    if (!headings.length) host.append(note("这份文档暂时没有章节标题。"));
  }
}

export function inspectDocument(context) {
  restoreQuestion(); documentContext = context;
  const source = context.source;
  const existing = tabs.find((tab) => tab.id === source.id);
  if (existing) existing.title = source.title;
  else tabs.push({ id: source.id, title: source.title });
  if (tabs.length > 12) tabs.shift();
  active = source.id; persistTabs(); renderTabs();
  $("workspaceTitle").textContent = source.title;
  if (!$("craftInspector").hidden) renderInspector();
}

function syncInspectorMode() {
  const panel = $("craftInspector");
  const modal = !panel.hidden && window.matchMedia("(max-width: 820px)").matches;
  panel.setAttribute("role", modal ? "dialog" : "complementary");
  if (modal) panel.setAttribute("aria-modal", "true"); else panel.removeAttribute("aria-modal");
  $("arrangementPanel").inert = modal;
  $("main").inert = modal;
  document.querySelector(".ws-topbar").inert = modal;
  document.querySelector(".craft-mobile-dock").inert = modal;
}

export function showInspector(tab = inspectorTab) {
  inspectorTab = tab;
  if ($("craftInspector").hidden) inspectorReturnFocus = document.activeElement;
  $("craftInspector").hidden = false;
  document.body.classList.add("craft-inspector-open");
  $("craftPanelToggle").setAttribute("aria-expanded", "true");
  renderInspector(); syncInspectorMode();
  if (window.matchMedia("(max-width: 820px)").matches) $("craftInspector").querySelector('[aria-selected="true"]').focus();
}

function closeInspector(restoreFocus = false) {
  restoreQuestion(); $("craftInspector").hidden = true;
  document.body.classList.remove("craft-inspector-open");
  $("craftPanelToggle").setAttribute("aria-expanded", "false");
  syncInspectorMode();
  if (restoreFocus) {
    const fallback = window.matchMedia("(max-width: 820px)").matches ? $("craftMobileMore") : $("craftPanelToggle");
    (inspectorReturnFocus?.isConnected && inspectorReturnFocus.getClientRects().length && !inspectorReturnFocus.closest("[inert]") ? inspectorReturnFocus : fallback).focus();
  }
}

export function resetCraft({ preserveNavigation = false } = {}) {
  request++; workspace = null; workspaceOwner = ""; saving = false;
  closeInspector(); documentContext = null; tabs = [];
  const current = visits[visitIndex] || { view: document.body.dataset.craftView || "home", sourceId: "", library: null };
  if (!preserveNavigation) { visits = [{ ...current, key: visitKey(current) }]; visitIndex = 0; }
  active = current.sourceId || current.view;
  $("craftInspectorBody").replaceChildren();
  $("craftStarred").replaceChildren(note("正在读取…"));
  $("craftFolders").replaceChildren(); $("craftTags").replaceChildren();
  $("craftBack").disabled = visitIndex <= 0; $("craftForward").disabled = visitIndex >= visits.length - 1;
  $("craftQuickOpen").close(); $("craftQuickInput").value = ""; $("craftQuickResults").replaceChildren();
  try {
    const saved = JSON.parse(localStorage.getItem(`pa.document-tabs.${currentUserId()}`) || "[]");
    tabs = saved.filter((item) => /^[a-f0-9]{32}$/.test(item.id) && typeof item.title === "string").slice(-12);
  } catch { /* A malformed local preference does not block the workspace. */ }
  renderTabs(); loadWorkspace();
}

export function initCraft() {
  $("craftMobileSpace").addEventListener("click", () => {
    const back = ["setup", "chat"].includes(document.body.dataset.craftView) || document.body.classList.contains("craft-document-open");
    if (!back) emit("craft:navigate", "setup");
    else if (visitIndex > 0) historyStep(-1);
    else emit("craft:navigate", "home");
  });
  $("craftMobileMore").addEventListener("click", () => {
    const dialog = sheet("空间选项");
    dialog.append(button("空间设置", () => { dialog.close(); emit("craft:navigate", "setup"); }),
      button("切换深色／浅色", () => $("themeToggle").click()),
      button("查看文档工具", () => { dialog.close(); showInspector(); }),
      button("待核对事项", () => { dialog.close(); emit("craft:navigate", "inbox"); }));
  });
  $("craftMobileReminders").addEventListener("click", () => $("btnSchedules").click());
  const separator = $("craftInspectorResize");
  function resize(width) {
    const bounded = Math.round(Math.max(240, Math.min(440, width)));
    document.documentElement.style.setProperty("--inspector-width", `${bounded}px`);
    separator.setAttribute("aria-valuenow", String(bounded));
  }
  separator.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault(); separator.setPointerCapture(event.pointerId);
  });
  separator.addEventListener("pointermove", (event) => {
    if (separator.hasPointerCapture(event.pointerId)) resize($("craftInspector").getBoundingClientRect().right - event.clientX);
  });
  separator.addEventListener("pointerup", (event) => {
    if (separator.hasPointerCapture(event.pointerId)) separator.releasePointerCapture(event.pointerId);
  });
  separator.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault(); resize($("craftInspector").getBoundingClientRect().width + (event.key === "ArrowLeft" ? 16 : -16));
  });
  $("workspaceSwitcher").addEventListener("click", () => emit("craft:navigate", "setup"));
  $("craftAddFolder").addEventListener("click", () => folderDialog());
  $("craftBack").addEventListener("click", () => historyStep(-1));
  $("craftForward").addEventListener("click", () => historyStep(1));
  $("craftPanelToggle").addEventListener("click", () => {
    if ($("craftInspector").hidden) showInspector(); else closeInspector();
  });
  $("craftPanelClose").addEventListener("click", () => closeInspector(true));
  window.matchMedia("(max-width: 820px)").addEventListener("change", syncInspectorMode);
  $("craftMobileAssistant").addEventListener("click", () => emit("craft:navigate", "chat"));
  document.querySelectorAll("[data-inspector-tab]").forEach((control) => control.addEventListener("click", () => showInspector(control.dataset.inspectorTab)));
  document.addEventListener("craft:history-location", (event) => {
    const current = visits[visitIndex];
    if (current?.view === "sources" && !current.sourceId) {
      current.library = event.detail; current.key = visitKey(current);
    }
  });
  document.addEventListener("craft:location", (event) => {
    const { folder_id, starred } = event.detail;
    document.querySelectorAll("[data-folder-id]").forEach((control) => control.setAttribute("aria-current", control.dataset.folderId === folder_id ? "page" : "false"));
    if (folder_id !== "all" || starred === "true") document.querySelectorAll('[data-arrangement-view="sources"]').forEach((control) => control.setAttribute("aria-current", "false"));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("craftInspector").hidden && !document.querySelector("dialog[open]")) {
      event.preventDefault(); closeInspector(true);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Tab" || $("craftInspector").hidden || !window.matchMedia("(max-width: 820px)").matches || document.querySelector("dialog[open]")) return;
    const controls = [...$("craftInspector").querySelectorAll("button:not(:disabled), input, textarea, select")];
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.addEventListener("craft:document-loading", () => {
    restoreQuestion(); documentContext = null;
    if (!$("craftInspector").hidden) $("craftInspectorBody").replaceChildren(note("正在读取文档…"));
  });
  document.addEventListener("workspace:view", (event) => {
    if (window.matchMedia("(max-width: 820px)").matches && !$("craftInspector").hidden) closeInspector();
    restoreQuestion(); documentContext = null;
    document.body.dataset.craftView = event.detail.view;
    document.body.classList.toggle("craft-document-open", !!event.detail.sourceId);
    $("craftMobileSpace").setAttribute("aria-label", ["setup", "chat"].includes(event.detail.view) || event.detail.sourceId ? "返回上一页" : "打开空间设置");
    rememberVisit(event.detail);
    if (!$("craftInspector").hidden) renderInspector();
  });
  document.addEventListener("workspace:action", (event) => {
    $("craftMobileCreate").open = false;
    if (event.detail === "search") openQuickSearch();
  });
  $("craftTabs").addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const controls = [...$("craftTabs").querySelectorAll('[role="tab"]')];
    const index = controls.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? controls.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + controls.length) % controls.length;
    controls[next].focus(); controls[next].click();
  });
  initQuickSearch();
}

export function openQuickSearch() {
  const dialog = $("craftQuickOpen");
  if (!dialog.open) dialog.showModal();
  $("craftQuickInput").focus(); $("craftQuickInput").dispatchEvent(new Event("input"));
}

function initQuickSearch() {
  const dialog = $("craftQuickOpen"), input = $("craftQuickInput"), results = $("craftQuickResults");
  let generation = 0, timer;
  $("craftQuickClose").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener("close", () => { generation++; clearTimeout(timer); });
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const ticket = ++generation;
    results.replaceChildren(note("正在查找…"));
    timer = setTimeout(async () => {
      const owner = currentUserId();
      try {
        const response = await api.librarySearch({ q: input.value.trim(), limit: "20" });
        if (!dialog.open || ticket !== generation || owner !== currentUserId()) return;
        results.replaceChildren(...response.data.items.map((source) => h("button", { type: "button", class: "craft-quick-result", onClick: () => { dialog.close(); openDocument(source); } }, [
          icon("doc"), h("span", {}, [h("strong", { text: source.title }), h("small", { text: source.hits[0]?.quote || "正文尚未完成提取" })]),
        ])));
        if (!response.data.items.length) results.append(note("没有找到匹配的文档。"));
      } catch (err) {
        if (dialog.open && ticket === generation && owner === currentUserId()) results.replaceChildren(note(err.message || "搜索暂时不可用"));
      }
    }, 220);
  });
  dialog.addEventListener("keydown", (event) => {
    const items = [...results.querySelectorAll("button")];
    if (event.key === "Enter" && document.activeElement === input && items.length) { event.preventDefault(); items[0].click(); }
    if (!["ArrowDown", "ArrowUp"].includes(event.key) || !items.length) return;
    event.preventDefault();
    const index = items.indexOf(document.activeElement);
    items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
  });
}

export function renderCraftHome(host, ctx) {
  const root = h("section", { class: "craft-home" }); host.append(root);
  const search = h("input", { class: "craft-home-search-input", type: "search", maxLength: 200, placeholder: "在我的空间中搜索", "aria-label": "在我的空间中搜索" });
  const mode = h("select", { "aria-label": "搜索方式" }, [h("option", { value: "q", text: "关键词" }), h("option", { value: "tag", text: "标签" })]);
  mode.addEventListener("change", () => { search.maxLength = mode.value === "tag" ? 30 : 200; });
  const form = h("form", { class: "craft-home-search" }, [icon("search"), search, mode, h("button", { type: "submit", class: "sr-only", text: "搜索文档" })]);
  form.addEventListener("submit", (event) => { event.preventDefault(); libraryNavigate({ [mode.value]: search.value.trim() }); });
  const rail = h("div", { class: "craft-document-rail", "aria-label": "最近的文档" });
  const stars = h("div", { class: "craft-home-starred" });
  const folders = h("div", { class: "craft-home-folders" });
  const section = (title, action, content, moreAction = action) => h("section", { class: "craft-home-section" }, [h("div", { class: "craft-section-head" }, [h("button", { type: "button", class: "craft-section-title", onClick: action }, [title, icon("chevron")]), h("button", { type: "button", class: "btn-icon", "aria-label": `整理${title}`, onClick: moreAction }, [icon("more")])]), content]);
  root.append(form, rail, h("div", { class: "craft-home-links" }, [
    h("button", { type: "button", onClick: () => libraryNavigate() }, [icon("doc"), h("span", { text: "所有文档" }), icon("chevron")]),
    h("button", { type: "button", onClick: () => ctx.navigate("connections") }, [icon("external"), h("span", { text: "连接" }), icon("chevron")]),
    h("button", { type: "button", onClick: () => ctx.navigate("shared") }, [icon("doc"), h("span", { text: "与我共享" }), icon("chevron")]),
  ]), section("收藏", () => libraryNavigate({ starred: "true" }), stars), section("文件夹", () => ctx.navigate("folders"), folders, () => folderDialog()));
  refreshCraftHome(root);
  const owner = currentUserId();
  api.librarySearch({ limit: "6" }).then((response) => {
    if (!root.isConnected || !ctx.isCurrent() || owner !== currentUserId()) return;
    for (const source of response.data.items) {
      const preview = h("article", { class: "craft-paper-preview", "data-kind": source.kind }, [
        h("h3", { text: source.title }), h("div", { class: "craft-paper-line" }),
        h("p", { text: source.hits[0]?.quote || "正文正在等待提取，打开文档可查看处理状态。" }),
      ]);
      rail.append(h("a", { href: `#sources/${source.id}`, class: "craft-preview-button", onClick: (event) => { event.preventDefault(); openDocument(source); } }, [preview]));
    }
    if (!response.data.items.length) {
      for (const [title, description, action] of [
        ["读懂一份长文", "导入 PDF，提炼结论、风险和关键数据。", () => emit("workspace:action", "upload")],
        ["整理一场会议", "上传录音，或粘贴已有的会议文字稿。", () => emit("workspace:action", "upload")],
        ["开始使用", "查看服务配置与个人安排的开始指南。", () => ctx.navigate("setup")],
      ]) rail.append(h("a", { href: "#setup", class: "craft-preview-button", onClick: (event) => { event.preventDefault(); action(); } }, [h("article", { class: "craft-paper-preview" }, [h("h3", { text: title }), h("div", { class: "craft-paper-line" }), h("p", { text: description }), h("small", { text: "开始使用" })])]));
    }
  }).catch(() => { if (root.isConnected) rail.append(note("最近文档暂时无法读取，可以从「所有文档」重试。")); });
}

export function refreshCraftHome(host) {
  const root = host.matches?.(".craft-home") ? host : host.querySelector(".craft-home");
  if (!root) return;
  const state = getWorkspace();
  const count = Object.values(state?.entries || {}).filter((entry) => entry.starred).length;
  root.querySelector(".craft-home-starred").replaceChildren(count ? button(`查看 ${count} 份收藏文档`, () => libraryNavigate({ starred: "true" })) :
    note(state ? "收藏文档以便快速访问" : "收藏暂未读取完成"));
  folderTree(root.querySelector(".craft-home-folders"));
}

export function renderCraftSettings(host, details, ctx) {
  const root = h("section", { class: "craft-space-settings" });
  host.append(root);
  const row = (symbol, title, action) => h("button", { type: "button", class: "craft-settings-row", onClick: action }, [icon(symbol), h("span", { text: title }), icon("chevron")]);
  const templates = () => {
    const dialog = sheet("文档模板");
    for (const [title, kind, content] of [
      ["会议记录", "meeting_transcript", "会议主题\n参会人\n\n讨论内容\n\n决议\n\n行动项与负责人\n\n待确认的问题"],
      ["阅读笔记", "document_text", "资料名称\n\n主要结论\n\n关键数据\n\n风险与疑问\n\n我的笔记"],
      ["随手记录", "message", "时间\n\n记录内容\n\n下一步"],
    ]) dialog.append(button(title, () => { dialog.close(); ctx.collect({ title, kind, content }); }));
  };
  const appearance = () => {
    const dialog = sheet("外观与语言");
    dialog.append(note("外观偏好保存在本机。"), button("切换深色／浅色", () => $("themeToggle").click()),
      note("界面语言为简体中文，日期与个人安排使用上海时区。"), button("编辑个人档案", () => { dialog.close(); $("btnProfile").click(); }));
  };
  const statistics = () => {
    const dialog = sheet("空间信息");
    dialog.append(note("文件夹与收藏保存在当前用户的本机服务数据库。打开的文档标签页保存在本机浏览器。"),
      getWorkspace() ? note(`文件夹 ${getWorkspace().folders.length} 个 · 收藏 ${Object.values(getWorkspace().entries).filter((entry) => entry.starred).length} 份`) : note("整理数据暂未读取，请刷新后查看。"));
  };
  const advanced = h("details", { class: "craft-settings-advanced" }, [h("summary", { text: "服务配置与开始指南" }), details]);
  root.append(h("div", { class: "craft-space-profile" }, [
    h("span", { class: "craft-space-avatar", "aria-hidden": "true", text: "p." }),
    h("h3", { text: "我的空间" }),
  ]), h("div", { class: "craft-space-banner" }, [h("strong", { text: "Personal Agent" }), note("把资料、会议与个人安排，留在自己的工作空间。")]),
  h("h3", { class: "craft-settings-label", text: "空间设置" }),
  row("doc", "模板", templates), row("list", "统计", statistics),
  row("folder", "文件夹", () => ctx.navigate("folders")),
  row("list", "标签", () => {
    const dialog = sheet("标签");
    const content = h("div", {}, [note("选择标签可以查看对应文档。")]);
    dialog.append(content);
    const owner = currentUserId();
    api.libraryTags().then((response) => {
      if (!dialog.open || !content.isConnected || owner !== currentUserId()) return;
      for (const tag of response.data.tags) content.append(button(`${tag.label} · ${tag.count}`, () => { dialog.close(); libraryNavigate({ tag: tag.label }); }));
      if (!response.data.tags.length) content.append(note("暂无标签。打开文档可编辑它的标签。"));
    }).catch(() => { if (dialog.open && content.isConnected && owner === currentUserId()) content.append(note("标签暂时无法读取。")); });
  }),
  row("lock", "模型与 API Key", () => { advanced.open = true; details.querySelector('[aria-label="模型与 API Key"]')?.scrollIntoView({ block: "start", behavior: "smooth" }); }),
  row("lock", "空间设置与服务连接", () => { advanced.open = true; details.scrollIntoView({ block: "start", behavior: "smooth" }); }),
  h("h3", { class: "craft-settings-label", text: "常规设置" }),
  row("sun", "外观与语言", appearance), row("doc", "个人档案", () => $("btnProfile").click()));
  root.append(advanced);
}

export function renderCraftFolders(host) {
  const region = h("section", { class: "craft-folder-browser" });
  host.append(region, button("新建文件夹", () => folderDialog(), true));
  folderTree(region);
}
