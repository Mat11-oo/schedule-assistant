import { documentAssistant } from "./document-assistant.js?v=__APP_VERSION__";
import { renderDocument } from "./document.js?v=__APP_VERSION__";
import { renderCraftHome, renderCraftSettings, renderCraftFolders, refreshCraftHome, inspectDocument } from "./craft.js?v=__APP_VERSION__";
import { $, h, icon, toast } from "./core.js?v=__APP_VERSION__";
import { api } from "./api.js?v=__APP_VERSION__";
import { ensureSession, currentUserId } from "./identity.js?v=__APP_VERSION__";
import { renderMaterial, uploadDialog, webDialog, resetMaterialDrafts, hasMaterialDrafts } from "./materials.js?v=__APP_VERSION__";
import { renderPlanning, renderPlanningInbox, planningRange, resetPlanning } from "./planning.js?v=__APP_VERSION__";
import { renderBrief, briefSettings, openSavedBrief, renderSourceUpdates, attachDuplicateSuggestions, sourceUpdatePicker } from "./followup.js?v=__APP_VERSION__";
import { renderLibrary, renderLibraryMetadata, resetLibrary, setLibraryLocation, getLibraryLocation, libraryLocationParams } from "./library.js?v=__APP_VERSION__";
import { renderSetup, renderStartHint } from "./setup.js?v=__APP_VERSION__";

const TZ = "Asia/Shanghai";
const VIEWS = { home: "首页", chat: "与小助对话", tasks: "任务", today: "日历", inbox: "待核对事项", sources: "所有文档", setup: "空间设置", connections: "连接", shared: "与我共享", folders: "文件夹" };
const VIEW_HINTS = { home: "", tasks: "", today: "留出时间，专注重要的事", inbox: "核对原文，让下一步更清楚", sources: "", setup: "", connections: "模型与本机服务", shared: "", folders: "" };
const OWNERS = { self: "我的行动项", other: "他人事项", unknown: "归属待确认", reference: "参考建议" };
const STATES = { todo: "未开始", doing: "进行中", waiting: "等待中", done: "已完成", cancelled: "已取消" };
const KINDS = { message: "消息", document_text: "文档正文", meeting_transcript: "会议文字稿" };
let view = "home", generation = 0, cache = null, sourceId = "", taskFilter = "active";
let sourceHit = null;
let pendingSetupAction = "";
let craftTaskFilter = "unscheduled";
let workspaceDirty = false;
let sourceDraft = null;
let panel, body, dialog, statusBox;

const text = (tag, value, cls = "") => h(tag, { text: value, class: cls });
const button = (label, fn, primary = false) => h("button", { type: "button", class: `btn btn-${primary ? "primary" : "ghost"} btn-sm`, onClick: fn, text: label });
const note = (value) => text("p", value, "ar-note");
const format = (value) => value ? new Intl.DateTimeFormat("zh-CN", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "未设置";
const dayKey = (value) => new Intl.DateTimeFormat("sv-SE", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value));
const localTime = (value) => value ? new Intl.DateTimeFormat("sv-SE", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(value)).replace(" ", "T") : "";
const isoTime = (value) => value ? new Date(`${value}:00+08:00`).toISOString() : null;
const errorMessage = (err) => err.message || (err.status === 422 ? "请检查输入内容和时间格式" : "读取或保存失败，请重试");

function alertIn(container, err) {
  container.replaceChildren(text("p", errorMessage(err), "ar-error"));
  container.setAttribute("role", "alert");
}

function input(label, name, { value = "", type = "text", required = false, maxLength = 200, options = null } = {}) {
  const id = `ar-${name}`;
  const control = options ? h("select", { id, name, class: "field-input" }, Object.entries(options).map(([v, l]) => h("option", { value: v, text: l }))) :
    h(type === "textarea" ? "textarea" : "input", { id, name, type: type === "textarea" ? null : type, required, maxLength, class: "field-input", rows: type === "textarea" ? 5 : null });
  control.value = value ?? "";
  return h("label", { class: "ar-field", for: id }, [text("span", label), control]);
}

function readForm(form) {
  return Object.fromEntries(new FormData(form));
}

function taskPayload(form) {
  const f = readForm(form);
  return { title: f.title, due_at: isoTime(f.due_at), estimated_minutes: f.estimated_minutes ? Number(f.estimated_minutes) : null,
    priority: f.priority, note: f.note, reminder_at: isoTime(f.reminder_at), confirmed: true };
}

function taskFields(task) {
  return h("div", { class: "ar-form-grid" }, [
    input("任务名称", "title", { value: task.title, required: true }),
    input("截止时间（上海时区，可留空）", "due_at", { type: "datetime-local", value: localTime(task.due_at) }),
    input("预计耗时（分钟，可留空）", "estimated_minutes", { type: "number", value: task.estimated_minutes }),
    input("优先级", "priority", { value: task.priority || "normal", options: { high: "重要", normal: "普通", low: "较低" } }),
    input("站内提醒时间（上海时区，可留空）", "reminder_at", { type: "datetime-local", value: localTime(task.reminder_at) }),
    input("备注／等待原因", "note", { type: "textarea", value: task.note, maxLength: 2000 }),
  ]);
}

function openDialog(title) {
  dialog.replaceChildren();
  const close = button("关闭", () => dialog.close());
  dialog.append(h("div", { class: "ar-dialog-head" }, [h("h2", { id: "arDialogTitle", text: title }), close]));
  if (!dialog.open) dialog.showModal();
  return dialog;
}

async function submit(form, action) {
  const error = form.querySelector("[data-error]");
  const controls = [...form.querySelectorAll("button, input, textarea, select")];
  const disabled = new Map(controls.map((control) => [control, control.disabled]));
  form.querySelectorAll("button").forEach((control) => { control.disabled = true; });
  error.replaceChildren();
  try {
    // Actions capture FormData before their first await. Freeze the fields once
    // that payload exists so edits cannot be silently lost during a save.
    const pending = action();
    controls.forEach((control) => { control.disabled = true; });
    await pending;
  } catch (err) { alertIn(error, err); }
  finally { controls.forEach((control) => { control.disabled = disabled.get(control); }); }
}

function formError() { return h("div", { "data-error": "", role: "alert" }); }

export function resetArrangements() {
  resetPlanning();
  resetMaterialDrafts();
  sourceDraft = null;
  workspaceDirty = false;
  resetLibrary();
  sourceHit = null;
  pendingSetupAction = "";
  cache = null;
  sourceId = "";
  generation++;
  if (dialog?.open) dialog.close();
  craftTaskFilter = "unscheduled";
  if (view !== "chat") {
    if (location.hash.startsWith("#sources/")) history.replaceState(null, "", "#sources");
    navigate(view, false);
  }
}

export function initArrangements() {
  panel = $("arrangementPanel"); body = $("arrangementBody"); dialog = $("arrangementDialog"); statusBox = $("arrangementStatus");
  document.querySelectorAll("[data-arrangement-view]").forEach((el) => el.addEventListener("click", () => {
    if (el.dataset.arrangementView === "sources") { sourceId = ""; sourceHit = null; setLibraryLocation(); }
    navigate(el.dataset.arrangementView);
  }));
  document.addEventListener("workspace:action", (event) => {
    if (event.detail === "upload") uploadDialog(dialog, openSource, cache?.status);
    if (event.detail === "web") webDialog(dialog, openSource, cache?.status);
    if (event.detail === "collect") addSource({ kind: "document_text" });

  });
  for (const id of ["btnMemory", "btnProfile"]) $(id).addEventListener("click", () => { if (view !== "chat") navigate("chat"); });
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
  $("arRefresh").addEventListener("click", load);
  $("arAddSource").addEventListener("click", addSource);
  $("arUpload").addEventListener("click", () => uploadDialog(dialog, openSource, cache?.status));
  $("arWeb").addEventListener("click", () => webDialog(dialog, openSource, cache?.status));
  document.addEventListener("craft:navigate", (event) => navigate(event.detail));
  document.addEventListener("craft:open-source", (event) => openSource(event.detail.id));
  document.addEventListener("craft:library", (event) => {
    setLibraryLocation(event.detail); sourceId = ""; sourceHit = null; navigate("sources");
  });
  document.addEventListener("craft:workspace-changed", () => {
    if (view === "home") { refreshCraftHome(body); return; }
    if (["sources", "folders"].includes(view) && !(view === "sources" && sourceId)) {
      if (dialog.open) workspaceDirty = true; else load();
    }
  });
  document.addEventListener("craft:workspace-ready", () => {
    if (view === "home") refreshCraftHome(body);
  });
  window.addEventListener("beforeunload", (event) => {
    if (!(sourceDraft?.title?.trim() || sourceDraft?.content?.trim() || hasMaterialDrafts())) return;
    event.preventDefault(); event.returnValue = "";
  });
  dialog.addEventListener("close", () => {
    if (workspaceDirty) { workspaceDirty = false; load(); }
  });
  document.addEventListener("craft:location", () => {
    if (view === "sources" && !sourceId) {
      history.replaceState(null, "", navigationHash("sources"));
      document.dispatchEvent(new CustomEvent("craft:history-location", { detail: getLibraryLocation() }));
    }
  });
  window.addEventListener("hashchange", route);
  route();
}

function route() {
  const [path, query = ""] = location.hash.slice(1).split("?");
  const [value, id = ""] = path.split("/");
  if (value === "sources") setLibraryLocation(Object.fromEntries(new URLSearchParams(query)));
  sourceHit = null;
  delete panel.dataset.highlightCandidate;
  sourceId = value === "sources" && /^[a-f0-9]{32}$/.test(id) ? id : "";
  navigate(Object.hasOwn(VIEWS, value) ? value : "home", false, "browser");
}

function navigationHash(next) {
  const path = next === "sources" && sourceId ? `#sources/${sourceId}` : `#${next}`;
  const query = next === "sources" ? libraryLocationParams() : "";
  return query ? `${path}?${query}` : path;
}

function navigate(next, updateHash = true, navigation = "push") {
  if (next !== "today") pendingSetupAction = "";
  view = next;
  generation++;
  document.dispatchEvent(new CustomEvent("workspace:view", { detail: { view: next, title: VIEWS[next], sourceId: next === "sources" ? sourceId : "", library: next === "sources" ? getLibraryLocation() : null, navigation } }));
  if (next !== "sources") delete document.body.dataset.focusLibrarySearch;
  if (updateHash && location.hash !== navigationHash(next)) history.pushState(null, "", navigationHash(next));
  document.querySelectorAll("[data-arrangement-view]").forEach((el) => {
    el.setAttribute("aria-current", el.dataset.arrangementView === next ? "page" : "false");
  });
  $("main").hidden = next !== "chat";
  panel.hidden = next === "chat";
  document.body.classList.toggle("arrangement-active", next !== "chat");
  for (const id of ["arUpload", "arWeb", "arAddSource"]) $(id).hidden = ["setup", "home", "tasks", "connections", "shared", "folders"].includes(next);
  if (next !== "chat") {
    $("arrangementTitle").textContent = VIEWS[next];
    $("arrangementEyebrow").textContent = VIEW_HINTS[next];
    load();
  }
}

async function load() {
  if (view === "chat") return;
  const ticket = ++generation;
  body.setAttribute("aria-busy", "true");
  document.dispatchEvent(new CustomEvent("craft:document-loading"));
  body.classList.remove("ar-source-document");
  body.replaceChildren(note("正在读取…"));
  try {
    // Some legacy API helpers interpolate the owner before request() awaits
    // identity. Resolve the session first, including direct #today entry.
    await ensureSession();
    if (ticket !== generation) return;
    if (["setup", "connections"].includes(view)) {
      const result = await api.setupStatus();
      if (ticket !== generation) return;
      cache = { setup: result.data };
      statusBox.textContent = "服务配置、后台状态和开始指南。";
      body.replaceChildren();
      const settingsBody = h("div", { class: "craft-settings-details" });
      if (view === "setup") renderCraftSettings(body, settingsBody, { navigate, collect: addSource });
      else body.append(settingsBody);
      renderSetup(settingsBody, result.data, { navigate, collect: addSource, openToday: (action) => {
        pendingSetupAction = action;
        navigate("today");
      } });
      return;
    }
    const [state, tasks, candidates, sources, reminders, planning, brief, updates] = await Promise.all([
      api.arrangementStatus(), api.arrangementTasks(), api.arrangementCandidates(), api.arrangementSources(), api.proactivePending(),
      ["today", "inbox"].includes(view) ? api.planningState(planningRange()) : Promise.resolve({ data: null }),
      view === "today" ? api.followupBrief() : Promise.resolve({ data: null }),
      ["today", "inbox"].includes(view) ? api.followupUpdates() : Promise.resolve({ data: [] }),
    ]);
    if (ticket !== generation) return;
    cache = { status: state.data, tasks: tasks.data, candidates: candidates.data, sources: sources.data, reminders: reminders.data?.items || [], planning: planning.data, brief: brief.data, updates: updates.data };
    statusBox.textContent = view === "today" ? "排期无需模型密钥。先设置可用时段，建议经确认后保存到应用内；外部日历未连接。" :
      cache.status.model_ready ? "可使用模型整理；所有提取事项需确认后才成为任务。" : "模型尚未配置。可以先保存资料，手动选取原文、确认并管理任务。";
    body.replaceChildren();
    if (view === "home") {
      const homeTicket = generation;
      renderCraftHome(body, { navigate, isCurrent: () => view === "home" && generation === homeTicket });
    }
    if (view === "tasks") renderCraftTasks();
    if (view === "folders") renderCraftFolders(body);
    if (view === "shared") body.append(h("section", { class: "craft-task-empty" }, [icon("doc"), text("h3", "共享接入待完成"), note("当前工作空间的资料由本机当前用户管理。共享文档服务尚未接入。"), button("查看空间设置", () => navigate("setup"))]));
    if (view === "today") renderToday();
    if (view === "inbox") renderInbox();
    if (view === "sources") {
      if (sourceId) await renderSource(sourceId, ticket);
      else renderSources();
    }
  } catch (err) {
    if (err.kind === "session" && ticket === generation) { cache = null; navigate("chat"); return; }
    if (ticket === generation) { alertIn(body, err); body.append(button("重新读取", load)); }
  } finally { if (ticket === generation) body.removeAttribute("aria-busy"); }
}

function empty(title, description) {
  return h("div", { class: "ar-empty" }, [text("h3", title), note(description), button("收集一条信息", addSource, true)]);
}

function planningContext() {
  return { dialog, openDialog, refresh: load, openTask: async (id) => {
    try { const result = await api.arrangementTask(id); await editTask(result.data.task); }
    catch (err) { toast(errorMessage(err)); }
  } };
}

function followupContext() { return { ...planningContext(), navigate }; }

function renderToday() {
  const active = cache.tasks.filter((t) => !["done", "cancelled"].includes(t.status));
  const today = dayKey(new Date());
  const dueToday = active.filter((t) => t.due_at && dayKey(t.due_at) === today);
  renderStartHint(body, { navigate });
  body.append(note(`${format(new Date())} · 上海时区 · 任务清单最多显示最近 500 个任务；时间安排另按所选日期展示。`));
  body.append(h("div", { class: "ar-stats" }, [
    h("div", {}, [text("span", active.length, "ar-stat-number"), text("span", "进行中的任务", "ar-stat-label")]),
    h("div", {}, [text("span", dueToday.length, "ar-stat-number"), text("span", "今日截止", "ar-stat-label")]),
    h("div", {}, [text("span", active.filter((t) => t.overdue).length, "ar-stat-number"), text("span", "需要关注的逾期", "ar-stat-label")]),
  ]));
  body.append(note(cache.status.scheduler_enabled && cache.status.scheduler_alive ? "站内提醒已启用，运行结果可在左侧「定时」中查看。" : "后台提醒未启用或尚未运行；已保存的提醒不会自动触发，需启动调度器。"));
  renderBrief(body, cache.brief, followupContext());
  if (cache.reminders.length) {
    const reminders = h("section", { class: "ar-section" }, [text("h3", "站内提醒")]);
    for (const item of cache.reminders) {
      const row = h("article", { class: "ar-card" }, [text("strong", item.title), note(item.body)]);
      if (item.kind === "arrangement_brief" && item.payload?.brief_id) row.append(button("查看简报快照", () => openSavedBrief(item.payload.brief_id, followupContext())));
      row.append(button("我知道了", async () => {
        try { await api.dismissProactive(item.id); await load(); }
        catch (err) { toast(errorMessage(err)); }
      }));
      reminders.append(row);
    }
    body.append(reminders);
  }
  const planningController = renderPlanning(body, cache.planning, planningContext());
  renderSourceUpdates(body, cache.updates, followupContext());
  body.append(text("h3", "任务清单"));
  body.append(h("div", { class: "ar-actions" }, Object.entries({ active: "全部未完成", today: "今日截止", overdue: "已逾期", done: "已结束" }).map(([key, label]) => {
    const el = button(label, () => { taskFilter = key; body.replaceChildren(); renderToday(); }, taskFilter === key);
    el.setAttribute("aria-pressed", String(taskFilter === key)); return el;
  })));
  const shown = cache.tasks.filter((t) => taskFilter === "done" ? ["done", "cancelled"].includes(t.status) :
    !["done", "cancelled"].includes(t.status) && (taskFilter === "active" || (taskFilter === "overdue" ? t.overdue : t.due_at && dayKey(t.due_at) === today)));
  shown.sort((a, b) => (a.due_at || "9999").localeCompare(b.due_at || "9999"));
  if (!shown.length) body.append(empty("这里还没有任务", "从消息、文档正文或会议文字稿中整理事项，确认后会出现在这里。"));
  for (const task of shown) {
    const card = h("article", { class: "ar-card", "data-task-id": task.id }, [
      h("div", { class: "ar-card-head" }, [text("h3", task.title), text("span", task.overdue ? "已逾期" : STATES[task.status], task.overdue ? "ar-tag ar-warning" : "ar-tag")]),
      note(`截止：${format(task.due_at)} · 预计：${task.estimated_minutes ? `${task.estimated_minutes} 分钟` : "待估算"} · ${{ high: "重要", normal: "普通", low: "较低" }[task.priority]}`),
      task.note ? note(task.note) : null,
    ]);
    card.append(h("div", { class: "ar-actions" }, [
      button("查看／调整", () => editTask(task)),
      ...task.sources.slice(0, 3).map((s) => {
        const el = button(s.deleted ? "来源已删除" : "查看出处", () => openSource(s.source_id, s.candidate_id));
        el.disabled = !!s.deleted; return el;
      }),
    ]));
    body.append(card);
  }
  const action = pendingSetupAction;
  pendingSetupAction = "";
  if (action === "work_hours") planningController?.preferences();
  if (action === "plan") planningController?.previewDialog();
  if (action === "brief" && cache.brief) briefSettings(cache.brief, followupContext());
}

function renderCraftTasks() {
  const today = dayKey(new Date());
  const toolbar = h("div", { class: "craft-task-tabs", role: "tablist", "aria-label": "任务范围" });
  const scopes = [["unscheduled", "inbox", "未归档"], ["today", "sun", "今天"], ["upcoming", "calendar", "即将到来"], ["all", "list", "全部任务"]];
  for (const [key, symbol, label] of scopes) {
    toolbar.append(h("button", { type: "button", role: "tab", "aria-selected": String(craftTaskFilter === key), title: label, "aria-label": label, onClick: () => {
      craftTaskFilter = key; body.replaceChildren(); renderCraftTasks();
      body.querySelector('.craft-task-tabs [aria-selected="true"]').focus({ preventScroll: true });
    } }, [icon(symbol), h("span", { text: label })]));
  }
  toolbar.querySelectorAll('[role="tab"]').forEach((control) => {
    control.tabIndex = control.getAttribute("aria-selected") === "true" ? 0 : -1;
  });
  toolbar.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const controls = [...toolbar.querySelectorAll('[role="tab"]')];
    const current = controls.indexOf(document.activeElement);
    if (current < 0) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? controls.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + controls.length) % controls.length;
    controls[next].click(); body.querySelector('.craft-task-tabs [aria-selected="true"]').focus();
  });
  body.append(toolbar);
  const tasks = cache.tasks.filter((task) => {
    if (craftTaskFilter === "all") return task.status !== "cancelled";
    if (["done", "cancelled"].includes(task.status)) return false;
    if (craftTaskFilter === "unscheduled") return !task.due_at;
    if (craftTaskFilter === "today") return task.overdue || task.due_at && dayKey(task.due_at) === today;
    return task.due_at && dayKey(task.due_at) > today;
  });
  if (!tasks.length) body.append(h("div", { class: "craft-task-empty" }, [icon("inbox"), note(craftTaskFilter === "unscheduled" ? "尚无未设置截止时间的任务，可从资料核对并确认行动项。" : "这个范围里暂时没有任务。")]));
  for (const task of tasks) {
    const checked = task.status === "done";
    const toggle = h("input", { type: "checkbox", checked, "aria-label": `${checked ? "重新打开" : "完成"} ${task.title}` });
    toggle.addEventListener("change", async () => {
      toggle.disabled = true;
      const ticket = generation, owner = currentUserId();
      try {
        await api.arrangementUpdateTask(task.id, { title: task.title, status: toggle.checked ? "done" : "todo", version: task.version,
          due_at: task.due_at, estimated_minutes: task.estimated_minutes, priority: task.priority, note: task.note,
          reminder_at: task.reminder_at, confirmed: true });
        if (owner !== currentUserId()) return;
        toast(toggle.checked ? "任务已完成，可在全部任务中查看" : "任务已重新打开");
        if (view === "tasks" && ticket === generation) await load();
      } catch (err) { toggle.checked = checked; toast(errorMessage(err)); }
      finally { toggle.disabled = false; }
    });
    body.append(h("article", { class: `craft-task-row${checked ? " is-done" : ""}` }, [toggle,
      h("button", { type: "button", class: "craft-task-open", onClick: () => editTask(task) }, [text("strong", task.title), note(task.due_at ? format(task.due_at) : "尚未设置截止时间")]) ]));
  }
  body.append(h("div", { class: "craft-task-footer" }, [button("收集待办信息", () => addSource({ kind: "message" })), button(`待核对事项 · ${cache.candidates.length}`, () => navigate("inbox")), note("未归档显示尚未设置截止时间的任务。最多读取最近 500 个任务。") ]));
}

function renderInbox() {
  renderPlanningInbox(body, cache.planning, planningContext());
  renderSourceUpdates(body, cache.updates, followupContext());
  body.append(note("原文中的建议和他人事项不会自动成为你的任务。核对归属与时间后再采纳。最多显示最近 300 条待处理事项。"));
  if (!cache.candidates.length) body.append(empty("没有待确认的行动项", "资料中已处理的决定会保留，不会再次弹出。"));
  for (const candidate of cache.candidates) body.append(candidateCard(candidate));
}

function candidateCard(candidate) {
  const card = h("article", { class: "ar-card", "data-candidate-id": candidate.id }, [
    h("div", { class: "ar-card-head" }, [text("h3", candidate.title), text("span", OWNERS[candidate.owner], "ar-tag")]),
    text("blockquote", candidate.quote),
    candidate.deadline_text ? note(`原文时间：${candidate.deadline_text}（尚未换算）`) : note("尚未记录时间表达，请核对原文"),
    candidate.source_title ? note(`来自：${candidate.source_title}`) : null,
    candidate.assignee ? note(`原文负责人：${candidate.assignee}`) : null,
  ]);
  if (candidate.status === "pending") card.append(h("div", { class: "ar-actions" }, [
    button("核对并处理", () => reviewCandidate(candidate), true),
    button("查看出处", () => openSource(candidate.source_id, candidate.id)),
    button("用来源核对已有任务", () => sourceUpdatePicker(candidate, cache.planning?.tasks || cache.tasks, followupContext())),
  ]));
  else card.append(note(`已处理：${{ accept: "已创建任务", link: "已关联任务", reference: "仅保存参考", ignore: "已忽略" }[candidate.status] || candidate.status}`));
  return card;
}

function reviewCandidate(candidate) {
  const box = openDialog("核对行动项");
  box.append(text("blockquote", candidate.quote));
  if (candidate.deadline_text) box.append(note(`原文时间：${candidate.deadline_text}。请按来源时间核对后填写截止时间。`));
  const form = h("form", {}, [taskFields(candidate)]);
  const ownership = h("input", { type: "checkbox", name: "take_ownership" });
  if (candidate.owner !== "self") form.append(h("label", { class: "ar-checkbox" }, [ownership, "原文未明确由我承担；我确认将这项工作作为自己的任务。"]));
  const similar = cache.tasks.filter((t) => !["done", "cancelled"].includes(t.status));
  form.append(input("关联已有任务（关联不会修改任务内容）", "link_task_id", { options: { "": "请选择已有任务", ...Object.fromEntries(similar.map((t) => [t.id, t.title])) } }));
  const error = formError();
  const decide = (action) => {
    if (action === "accept" && !form.reportValidity()) return;
    if (action === "link" && !readForm(form).link_task_id) { alertIn(error, new Error("请先选择要关联的任务")); return; }
    if (action === "accept" && candidate.owner !== "self" && !ownership.checked) {
      alertIn(error, new Error("请先确认是否由你承担这项工作")); return;
    }
    submit(form, async () => {
      await api.arrangementDecide(candidate.id, { ...(action === "accept" ? taskPayload(form) : { title: candidate.title, note: readForm(form).note, confirmed: true }), action, take_ownership: ownership.checked,
        link_task_id: readForm(form).link_task_id || null });
      toast(action === "accept" ? "任务已保存" : "处理决定已保存");
      if (dialog.open && form.isConnected) { dialog.close(); await load(); }
    });
  };
  const linkControl = form.querySelector('[name="link_task_id"]');
  const linkButton = button("确认关联", () => decide("link"));
  linkButton.disabled = !linkControl.value;
  linkControl.addEventListener("change", () => { linkButton.disabled = !linkControl.value; });
  form.append(note("创建任务和设置提醒将在下方确认后保存。提醒只进入站内收件箱。"), error,
    h("div", { class: "ar-actions" }, [button("确认创建任务", () => decide("accept"), true), linkButton,
      button("仅保存参考", () => decide("reference")), button("忽略", () => decide("ignore"))]));
  form.addEventListener("submit", (e) => { e.preventDefault(); decide("accept"); });
  box.append(form);
  const suggestions = h("section", { class: "fu-matches", "aria-label": "可能重复的任务" });
  form.prepend(suggestions);
  attachDuplicateSuggestions(suggestions, candidate, { ctx: followupContext(), form, linkControl: form.querySelector('[name="link_task_id"]') });
}

function addSource(preset = {}) {
  const restoring = sourceDraft && (sourceDraft.title || sourceDraft.content);
  const initial = restoring ? sourceDraft : preset;
  const owner = currentUserId();
  const box = openDialog("新建文档／收集文字");
  box.append(note("粘贴消息、文档正文或已有会议文字稿。PDF 与录音可从「上传文件」或底部加号导入。"));
  const form = h("form", { class: "ar-form" }, [
    input("标题", "title", { required: true, value: initial.title || "" }),
    input("类型", "kind", { options: KINDS, value: initial.kind || "message" }),
    input("原始时间（上海时区；未知可留空）", "original_at", { type: "datetime-local", value: initial.original_at || "" }),
    input("正文（最多 20,000 字）", "content", { type: "textarea", required: true, maxLength: 20000, value: initial.content || "" }),
    note("保留原始时间，便于核对“明天／周五”等表达；请先移除密码和联系方式。"), formError(),
  ]);
  if (restoring) {
    box.append(note("已恢复本次会话中未保存的草稿。"));
    box.append(button("放弃这份草稿", () => { sourceDraft = null; addSource({ kind: initial.kind }); }));
    if (preset.content) box.append(button("放弃草稿并使用此模板", () => { sourceDraft = null; addSource(preset); }));
  }
  const rememberDraft = () => { sourceDraft = readForm(form); };
  form.addEventListener("input", rememberDraft);
  form.addEventListener("change", rememberDraft);
  form.append(h("button", { type: "submit", class: "btn btn-primary", text: "保存资料" }));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const draft = readForm(form);
    sourceDraft = draft;
    submit(form, async () => {
      const fields = [...form.querySelectorAll("input, select, textarea")];
      fields.forEach((field) => { field.disabled = true; });
      try {
        const result = await api.arrangementCreateSource({ ...draft, original_at: isoTime(draft.original_at), tz: TZ });
        if (owner !== currentUserId()) return;
        if (sourceDraft === draft) sourceDraft = null;
        toast(result.data.reused ? "已找到相同资料" : "资料已保存，可从所有文档查看");
        if (dialog.open && form.isConnected) { dialog.close(); openSource(result.data.id); }
      } finally { fields.forEach((field) => { field.disabled = false; }); }
    });
  });
  box.append(form);
  form.querySelector('[name="title"]').focus();
}

function renderSources() {
  const ticket = generation;
  renderLibrary(body, { dialog, openDialog, refresh: load,
    isCurrent: () => view === "sources" && !sourceId && generation === ticket,
    openSource: (id, hit = null) => openSource(id, "", hit) });
}

function openSource(id, candidateId = "", hit = null) {
  sourceId = id;
  sourceHit = hit;
  navigate("sources");
  if (candidateId) panel.dataset.highlightCandidate = candidateId;
  else delete panel.dataset.highlightCandidate;
}

async function renderSource(id, ticket) {
  const [response, metadata] = await Promise.all([api.arrangementSource(id), api.libraryMetadata(id).catch(() => null)]);
  if (ticket !== generation) return;
  const source = response.data;
  source.root_id = metadata?.data.root_id || source.id;
  body.classList.add("ar-source-document");
  body.replaceChildren(button("返回资料列表", () => { sourceId = ""; sourceHit = null; navigate("sources"); }), button("刷新资料", load), text("h3", source.title),
    note(`${source.material ? { pdf: "PDF 文档", audio: "会议录音", web: "网页", docx: "Word 文档", text: "文本文件" }[source.material.kind] : KINDS[source.kind]} · 原始时间：${format(source.original_at)} · ${source.tz}`));
  body.append(documentAssistant(source));
  const documentInfo = h("details", { class: "document-info" }, [h("summary", { text: "文档信息与标签" })]);
  body.append(documentInfo);
  renderLibraryMetadata(documentInfo, source, { dialog, openDialog, refresh: load });
  if (sourceHit && ["summary", "title"].includes(sourceHit.kind)) {
    const value = sourceHit.kind === "summary" ? source.summary : source.title;
    if (value.includes(sourceHit.quote)) body.append(text("h3", sourceHit.kind === "summary" ? "摘要中的命中" : "标题中的命中"), text("blockquote", sourceHit.quote));
  }
  if (source.material) {
    const viewer = renderMaterial({ source, container: body, dialog, status: cache.status, onReload: load, onOpen: openSource });
    if (source.content) body.append(button("手动添加事项", () => addCandidate(source)));
    body.append(text("h3", `关联事项 · ${source.candidates.length}`));
    for (const c of source.candidates) body.append(candidateCard(c));
    const target = source.candidates.find((c) => c.id === panel.dataset.highlightCandidate);
    if (target?.evidence?.length) viewer.locate(target.evidence[0]);
    else if (target) {
      const unit = source.material.units.find((u) => u.start_char <= target.quote_start && u.end_char >= target.quote_end);
      if (unit) viewer.locate({ unit_id: unit.id, quote: target.quote });
    }
    else if (sourceHit?.unit_id) viewer.locate({ unit_id: sourceHit.unit_id, quote: sourceHit.quote });
    inspectDocument({ source, root: body, qa: viewer.qa });
    return;
  }
  const feedback = h("div", { role: "status" });
  const analyze = button(source.analysis_status === "ready" ? "已完成模型整理" : "使用模型整理", async () => {
    analyze.disabled = true;
    feedback.textContent = "正在整理，原文已保存…";
    try { await api.arrangementAnalyze(id); await load(); }
    catch (err) { alertIn(feedback, err); }
    finally { analyze.disabled = source.analysis_status === "ready" || !cache.status.model_ready; }
  }, true);
  analyze.disabled = source.analysis_status === "ready" || !cache.status.model_ready;
  const documentActions = h("div", { class: "ar-actions" }, [analyze, button("手动添加事项", () => addCandidate(source))]);
  if (source.analysis_status === "processing") body.append(note("整理进行中，请稍后刷新；若进程中断，3 分钟后可重试。"));
  if (source.analysis_status === "failed") body.append(note("上次整理未完成，可重试或手动选取原文添加事项。"));

  const content = text("pre", "", "ar-source-text");
  const target = source.candidates.find((c) => c.id === panel.dataset.highlightCandidate);
  const sourceCharacters = Array.from(source.content);
  const hit = sourceHit?.kind === "content" && Number.isInteger(sourceHit.start_char) && sourceCharacters.slice(sourceHit.start_char, sourceHit.end_char).join("") === sourceHit.quote ? sourceHit : null;
  if (target || hit) {
    const start = target ? target.quote_start : hit.start_char;
    const end = target ? target.quote_end : hit.end_char;
    content.append(document.createTextNode(sourceCharacters.slice(0, start).join("")),
      text("mark", sourceCharacters.slice(start, end).join("")), document.createTextNode(sourceCharacters.slice(end).join("")));
  } else content.textContent = source.content;
  const reading = renderDocument(source.content);
  const reader = h("section", { class: "document-reading" }, [reading, content]);
  let raw = !!(target || hit);
  reading.hidden = raw; content.hidden = !raw;
  const toggle = button(raw ? "返回阅读排版" : "查看原始文本", () => {
    raw = !raw; reading.hidden = raw; content.hidden = !raw;
    toggle.textContent = raw ? "返回阅读排版" : "查看原始文本";
  });
  body.append(toggle, reader, documentActions, feedback);
  if (source.summary) body.append(text("h3", "摘要"), note(source.summary), note("模型初步整理，请核对原文和可能遗漏的事项。"));
  body.append(text("h3", `关联事项 · ${source.candidates.length}`));
  for (const c of source.candidates) body.append(candidateCard(c));
  if (target || hit) content.querySelector("mark")?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  inspectDocument({ source, root: body });
}

function addCandidate(source) {
  const selected = window.getSelection()?.toString() || "";
  const quote = source.content.includes(selected) ? selected : "";
  const box = openDialog("从原文添加事项");
  const form = h("form", { class: "ar-form" }, [
    input("事项名称", "title", { required: true }),
    input("归属", "owner", { options: OWNERS, value: "unknown" }),
    input("原文中的连续引文（必须能在正文中找到）", "quote", { type: "textarea", required: true, value: quote, maxLength: 2000 }),
    input("原文时间表达（可留空）", "deadline_text", {}), formError(),
    h("button", { type: "submit", class: "btn btn-primary", text: "添加到待处理" }),
  ]);
  form.addEventListener("submit", (e) => { e.preventDefault(); submit(form, async () => {
    await api.arrangementAddCandidate(source.id, readForm(form));
    toast("已添加待确认事项，尚未创建任务");
    if (dialog.open && form.isConnected) { dialog.close(); navigate("inbox"); }
  }); });
  box.append(form);
}

async function editTask(task) {
  const box = openDialog("查看与调整任务");
  const form = h("form", {}, [taskFields(task), input("状态", "status", { options: STATES, value: task.status }), formError(),
    h("button", { type: "submit", class: "btn btn-primary", text: "确认保存修改" })]);
  form.addEventListener("submit", (e) => { e.preventDefault(); submit(form, async () => {
    await api.arrangementUpdateTask(task.id, { ...taskPayload(form), version: task.version, status: readForm(form).status });
    toast("任务已更新；完成或取消后将撤销未来提醒");
    if (dialog.open && form.isConnected) { dialog.close(); await load(); }
  }); });
  box.append(note("完成、取消或进入等待状态时，会撤销尚未结束的任务时间块（包括锁定项）；完成与取消还会撤销未来提醒。已保存的计划历史会保留，不推断实际工作时长。"), form);
  const history = h("details", { class: "ar-history" }, [text("summary", "变更历史")]);
  box.append(history);
  try {
    const result = await api.arrangementTask(task.id);
    if (!box.open || !form.isConnected) return;
    for (const change of result.data.changes) {
      const action = { accept: "创建任务", link: "关联出处", source_change: "来源变更" }[change.after.action];
      const row = h("article", { class: "fu-line" }, [note(`${format(change.created_at)} · ${change.before.title || "创建／关联"} → ${change.after.title || change.after.task?.title || task.title} · ${STATES[change.after.status] || action || "更新"}`)]);
      if (change.after.action === "source_change") {
        row.append(note(change.after.reason), text("blockquote", change.after.quote));
        for (const [key, value] of Object.entries(change.after.changes || {})) {
          const display = (v) => key === "due_at" ? format(v) : key === "status" ? STATES[v] : v;
          row.append(note(`${{ title: "名称", due_at: "截止时间", status: "状态" }[key] || key}：${display(change.before[key])} → ${display(value)}`));
        }
      }
      history.append(row);
    }
  } catch (err) { history.append(note(errorMessage(err))); }
}
