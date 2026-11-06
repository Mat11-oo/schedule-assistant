import { h, toast, nextId } from "./core.js?v=__APP_VERSION__";
import { api } from "./api.js?v=__APP_VERSION__";

const TZ = "Asia/Shanghai";
const WEEK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
let range = {};
export const planningRange = () => ({ ...range });
export function resetPlanning() { range = {}; }
const text = (tag, value, cls = "") => h(tag, { text: value, class: cls });
const note = (value) => text("p", value, "ar-note");
const btn = (label, fn, primary = false) => h("button", { type: "button", class: `btn btn-${primary ? "primary" : "ghost"} btn-sm`, text: label, onClick: fn });
const actions = (...nodes) => h("div", { class: "ar-actions" }, nodes);
const dateKey = (value) => new Intl.DateTimeFormat("sv-SE", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value));
const format = (value) => new Intl.DateTimeFormat("zh-CN", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
const local = (value) => new Intl.DateTimeFormat("sv-SE", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(value)).replace(" ", "T");
const iso = (value) => {
  const d = new Date(`${value}:00+08:00`);
  if (!value || Number.isNaN(d.getTime())) throw new Error("请填写完整起止时间");
  return d.toISOString();
};
const span = (row) => `${format(row.start_at)} — ${format(row.end_at)}`;
const formValues = (form) => Object.fromEntries(new FormData(form));

function field(label, name, { value = "", type = "text", required = false, min, max, options } = {}) {
  const id = nextId("pa");
  const control = options ? h("select", { id, name, class: "field-input" }, Object.entries(options).map(([v, l]) => h("option", { value: v, text: l }))) :
    h("input", { id, name, class: "field-input", type, required, min, max, maxLength: type === "text" ? 1000 : null });
  control.value = value ?? "";
  return h("label", { class: "ar-field", for: id }, [text("span", label), control]);
}
function check(label, name, checked = false) {
  return h("label", { class: "ar-checkbox" }, [h("input", { type: "checkbox", name, checked }), text("span", label)]);
}
function errorBox() { return h("div", { "data-planning-error": "", role: "alert", class: "ar-error" }); }
async function submit(form, action) {
  if (!form.reportValidity()) return;
  const buttons = [...form.querySelectorAll("button")];
  buttons.forEach((b) => { b.disabled = true; });
  const error = form.querySelector("[data-planning-error]");
  error.textContent = "";
  try { await action(); }
  catch (e) { if (error.isConnected) error.textContent = e.message || "操作未完成，请刷新后重试"; }
  finally { buttons.forEach((b) => { b.disabled = false; }); }
}
function wire(form, action, label = "确认保存") {
  form.append(errorBox(), h("button", { type: "submit", class: "btn btn-primary", text: label }));
  form.addEventListener("submit", (e) => { e.preventDefault(); submit(form, action); });
  return form;
}

function controller(data, ctx) {
  const { openDialog, dialog, refresh, openTask } = ctx;
  const finish = async (message) => { dialog.close(); toast(message); await refresh(); };
  const safe = async (action) => { try { await action(); } catch (e) { toast(e.message || "操作未完成，请刷新后重试"); } };

  function preferences() {
    const p = data.preferences;
    const box = openDialog("可用时段与每天容量");
    box.append(note("上海时区。只在这些时段安排任务；午休等休息时间请留在时段之间。每日上限只计算任务时间块，固定会议会另外占用空闲时间。"));
    if (!p.configured) box.append(note("下面是可编辑的示例；确认保存前不会作为你的工作偏好。"));
    const form = h("form", { class: "ar-form" });
    for (let day = 0; day < 7; day++) {
      const value = p.configured ? p.weekly.filter((w) => w.weekday === day).map((w) => `${w.start}-${w.end}`).join(", ") : day < 5 ? "09:00-12:00, 13:00-18:00" : "";
      form.append(field(`${WEEK[day]}（多个时段用逗号分隔；留空休息）`, `week${day}`, { value }));
    }
    form.append(h("div", { class: "ar-form-grid" }, [
      field("每天任务投入上限（分钟）", "capacity", { type: "number", value: p.daily_limit_minutes, min: 15, max: 1440, required: true }),
      field("安排之间的缓冲（分钟）", "buffer", { type: "number", value: p.buffer_minutes, min: 0, max: 120, required: true }),
    ]), note("修改设置会影响后续建议；已确认时间块不会自动移动，可能需要重新排期。"));
    box.append(wire(form, async () => {
      const f = formValues(form), weekly = [];
      for (let day = 0; day < 7; day++) for (const part of f[`week${day}`].split(/[,，]/).map((s) => s.trim()).filter(Boolean)) {
        const m = /^(\d{2}:\d{2})\s*[-—–]\s*(\d{2}:\d{2})$/.exec(part);
        if (!m) throw new Error(`${WEEK[day]}请按 09:00-12:00 的格式填写`);
        weekly.push({ weekday: day, start: m[1], end: m[2] });
      }
      await api.planningPreferences({ version: p.version, confirmed: true, weekly,
        daily_limit_minutes: Number(f.capacity), buffer_minutes: Number(f.buffer), tz: TZ });
      await finish("可用时段已保存；已有安排保持原位");
    }));
  }

  function eventDialog(event = null) {
    const requestId = crypto.randomUUID().replace(/-/g, "");
    const box = openDialog(event ? "调整固定安排" : "登记固定安排");
    const form = h("form", { class: "ar-form" }, [field("安排名称", "title", { value: event?.title || "", required: true }),
      field("开始时间（上海时区）", "start_at", { type: "datetime-local", value: event ? local(event.start_at) : `${data.start_date}T09:00`, required: true }),
      field("结束时间（上海时区）", "end_at", { type: "datetime-local", value: event ? local(event.end_at) : `${data.start_date}T10:00`, required: true }),
      field("备注", "note", { value: event?.note || "" })]);
    const warning = h("div", { class: "pa-conflicts", role: "status" });
    form.append(warning, note("固定安排会如实保存；如与已有安排冲突，会保留冲突并提示你处理，不自动移动其他安排。"));
    const showConflicts = () => {
      warning.replaceChildren();
      const f = formValues(form);
      try {
        const start = new Date(iso(f.start_at)).getTime(), end = new Date(iso(f.end_at)).getTime();
        const buffer = data.preferences.buffer_minutes * 60000;
        const other = [...data.events.filter((e) => e.id !== event?.id), ...data.blocks];
        const matches = other.filter((r) => start < new Date(r.end_at).getTime() + buffer && new Date(r.start_at).getTime() - buffer < end);
        if (matches.length) warning.append(note(`当前查看范围内有 ${matches.length} 项重叠或缓冲不足：`), ...matches.slice(0, 8).map((r) => note(`${r.title} · ${span(r)}`)));
      } catch { /* The form and API validate incomplete times on submit. */ }
    };
    form.addEventListener("input", showConflicts); showConflicts();
    box.append(wire(form, async () => {
      const f = formValues(form), payload = { ...f, start_at: iso(f.start_at), end_at: iso(f.end_at), confirmed: true };
      if (!event) payload.request_id = requestId;
      const result = event ? await api.planningUpdateEvent(event.id, { ...payload, version: event.version }) : await api.planningCreateEvent(payload);
      await finish(result.data.conflicts.length ? "固定安排已保存；请查看时间线中的冲突提示" : "固定安排已保存到应用内");
    }, "确认保存固定安排"));
  }

  function timing(taskId) {
    const task = data.tasks.find((t) => t.id === taskId);
    if (!task) { toast("任务已变化，请刷新后重试"); return; }
    const settings = data.timings.find((t) => t.id === taskId);
    const box = openDialog("任务排期信息");
    box.append(text("h3", task.title), note(`总预计耗时：${task.estimated_minutes ?? "未估算"} 分钟。剩余耗时留空时使用总预计耗时，不会根据时间流逝自动扣减。`));
    const form = h("form", { class: "ar-form" }, [
      field("剩余耗时（分钟；留空使用总预计）", "remaining", { type: "number", value: settings?.remaining_minutes, min: 0, max: 100000 }),
      check("允许拆成多个专注时间块", "split", settings?.splittable || false),
      field("最小时间块（分钟）", "minimum", { type: "number", value: settings?.min_block_minutes || 30, min: 5, max: 480, required: true }),
      note("前置任务：只有已完成，或能完整排在本任务之前，才安排当前任务。按住 Command／Ctrl 可多选。"),
    ]);
    const choices = h("select", { multiple: true, name: "dependencies", class: "field-input pa-dependencies", "aria-label": "前置任务", size: 5 },
      data.tasks.filter((t) => t.id !== task.id && t.status !== "cancelled").map((t) => h("option", { value: t.id, text: t.title, selected: settings?.dependency_ids.includes(t.id) })));
    form.append(choices, note("这里只更新排期信息；任务的截止时间、状态及提醒请从任务详情调整。"));
    box.append(wire(form, async () => {
      const f = formValues(form);
      await api.planningTiming(task.id, { confirmed: true, version: settings?.version || 0, task_version: task.version,
        remaining_minutes: f.remaining === "" ? null : Number(f.remaining), splittable: f.split === "on", min_block_minutes: Number(f.minimum),
        dependency_ids: [...choices.selectedOptions].map((o) => o.value) });
      await finish("排期信息已保存；请生成新方案");
    }));
  }

  async function showPreview(result) {
    const proposal = result.data || result, p = proposal.preview;
    const box = openDialog(p.kind === "manual_move" ? "改期预览" : "排期预览");
    box.append(note(`${p.coverage} 预览有效至 ${format(proposal.expires_at)}。`));
    for (const message of p.warnings) box.append(note(message));
    const section = (title, rows, render) => {
      if (!rows.length) return;
      box.append(text("h3", `${title} · ${rows.length}`), ...rows.map(render));
    };
    section("新增安排", p.additions, (b) => h("article", { class: "ar-card pa-proposed" }, [text("strong", b.title), note(span(b)), note(b.reason)]));
    section("取消原时间块", p.removed, (b) => note(`${data.tasks.find((t) => t.id === b.task_id)?.title || b.title || "任务"} · ${span(b)}；原记录保留在历史中`));
    if (p.kept.length) box.append(h("details", {}, [text("summary", `保持原位 · ${p.kept.length}`), ...p.kept.map((b) => note(`${b.title} · ${span(b)}${b.locked ? " · 已锁定" : ""}`))]));
    section("尚未安排", p.unallocated, (t) => h("article", { class: "ar-card" }, [text("strong", t.title),
      note(`${t.reason}${t.remaining_minutes == null ? "" : `；尚缺 ${t.remaining_minutes} 分钟`}`), note(t.options.join("／")),
      actions(btn("调整排期信息", () => timing(t.task_id)), btn("调整任务", () => safe(() => openTask(t.task_id))))]));
    if (p.days.length) box.append(h("details", {}, [text("summary", "每天容量"), ...p.days.map((d) => note(`${d.date}：已有任务 ${d.already_booked_minutes} 分钟，新安排 ${d.added_minutes} 分钟，上限 ${d.daily_limit_minutes} 分钟；排期前空闲 ${d.free_minutes_before} 分钟`))]));
    if (p.conflicts.length) box.append(note(`保留的安排中仍有 ${p.conflicts.length} 处冲突或缓冲不足，请从时间线处理。`));
    const form = h("form", {}, [note("确认只保存上面列出的时间块变更；不会修改任务状态、截止时间或向外部日历写入。")]);
    const partial = p.unallocated.length > 0 || (!p.additions.length && p.removed.length > 0);
    if (partial) form.append(check("我已了解尚未安排的工作和取消项，确认只保存以上变更", "accept_partial"));
    const save = wire(form, async () => {
      if (partial && !new FormData(form).has("accept_partial")) throw new Error("请先核对未安排事项与取消项，并勾选确认");
      await api.planningConfirm(proposal.id);
      await finish("已保存到应用内；外部日历未同步");
    }, "确认保存这份方案");
    const submitButton = save.querySelector('button[type="submit"]');
    if (proposal.status !== "preview" || new Date(proposal.expires_at) <= new Date() || (!p.additions.length && !p.removed.length)) submitButton.disabled = true;
    save.append(btn("放弃这份方案", () => safe(async () => { await api.planningDismiss(proposal.id); await finish("方案已放弃；现有安排保持原位"); })));
    box.append(save);
  }

  function previewDialog(taskId = null) {
    if (!data.preferences.configured) { preferences(); return; }
    const box = openDialog("生成排期建议");
    const eligible = data.tasks.filter((t) => ["todo", "doing"].includes(t.status));
    const form = h("form", {}, [field("开始日期（含当天）", "start_date", { type: "date", value: data.start_date, required: true }),
      field("结束日期（含当天，最多 31 天）", "end_date", { type: "date", value: data.end_date, required: true }),
      note("先按截止时间，再按优先级安排；优先满足前置关系。未知耗时和时间不足会单独列出。")]);
    const list = h("div", { class: "pa-task-options", role: "group", "aria-label": "选择要安排的任务" }, eligible.map((t) => check(t.title, `task_${t.id}`, taskId ? t.id === taskId : true)));
    form.append(list, check("重排所选任务在日期范围内、尚未开始且未锁定的时间块", "replace", !!taskId),
      note("锁定、已开始或跨越日期范围的时间块会保留；不延长到未设置的工作时段。"));
    box.append(wire(form, async () => {
      const f = new FormData(form), taskIds = eligible.filter((t) => f.has(`task_${t.id}`)).map((t) => t.id);
      if (!taskIds.length) throw new Error("请至少选择一个未开始或进行中的任务");
      const result = await api.planningPreview({ start_date: f.get("start_date"), end_date: f.get("end_date"), task_ids: taskIds, replace_unlocked: f.has("replace") });
      await refresh(); await showPreview(result);
    }, "生成预览，暂不保存安排"));
  }

  function confirmAction(title, description, action) {
    const box = openDialog(title), form = h("form", {}, [note(description)]);
    box.append(wire(form, async () => { await action(); await finish("操作已保存；历史记录保留"); }, "确认执行"));
  }

  function move(block) {
    const box = openDialog("调整时间块");
    box.append(text("h3", block.title), note(`当前安排：${span(block)}。改期保持原时长，不改变工作量。`));
    const form = h("form", {}, [field("新开始时间（上海时区）", "start_at", { type: "datetime-local", value: local(block.start_at), required: true }),
      field("新结束时间（上海时区）", "end_at", { type: "datetime-local", value: local(block.end_at), required: true }),
      field("改期原因", "reason", { required: true })]);
    if (block.locked) form.append(check("我确认移动并解锁此时间块", "unlock"));
    box.append(wire(form, async () => {
      const f = formValues(form);
      const result = await api.planningMove(block.id, { version: block.version, start_at: iso(f.start_at), end_at: iso(f.end_at), reason: f.reason, unlock_confirmed: f.unlock === "on" });
      await refresh(); await showPreview(result);
    }, "预览改期"));
  }

  function blockButtons(block) {
    const future = new Date(block.start_at) > new Date();
    const row = actions(btn("查看任务", () => safe(() => openTask(block.task_id))), btn(block.locked ? "确认解锁" : "确认锁定", () => safe(async () => {
      await api.planningLock(block.id, { confirmed: true, version: block.version, locked: !block.locked }); await refresh();
    })));
    if (future) row.append(btn("改期", () => move(block)), btn("重新排期", () => previewDialog(block.task_id)), btn("取消时间块", () => confirmAction("取消时间块", `${block.title} · ${span(block)}。任务本身保留，剩余耗时不会自动改变。`, () => api.planningCancelBlock(block.id, { confirmed: true, version: block.version }))));
    return row;
  }

  return { preferences, eventDialog, timing, previewDialog, showPreview, blockButtons,
    cancelEvent: (e) => confirmAction("取消固定安排", `${e.title} · ${span(e)}。不会自动替你安排其他任务。`, () => api.planningCancelEvent(e.id, { confirmed: true, version: e.version })),
    history: () => safe(async () => {
      const result = await api.planningHistory(), box = openDialog("安排变更历史");
      box.append(note("最近 100 条记录；时间块记录的是计划，不是实际完成或实际耗时。"));
      if (!result.data.length) box.append(note("还没有安排变更记录。"));
      for (const row of result.data) box.append(h("article", { class: "ar-card" }, [note(`${format(row.created_at)} · ${row.reason}`),
        h("details", {}, [text("summary", "查看前后内容"), text("pre", JSON.stringify({ 修改前: row.before, 修改后: row.after }, null, 2), "ar-source-text")])]));
    }),
    export: () => safe(async () => {
      const content = await api.planningExport({ start_date: data.start_date, end_date: data.end_date });
      const url = URL.createObjectURL(new Blob([content], { type: "text/calendar;charset=utf-8" }));
      const a = h("a", { href: url, download: "personal-agent.ics" }); document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000); toast("日历文件已导出；需自行导入，不代表外部同步完成");
    }),
  };
}

export function renderPlanningInbox(host, data, ctx) {
  if (!data?.proposals.length) return;
  const c = controller(data, ctx), section = h("section", { class: "pa-section" }, [text("h3", `待确认排期 · ${data.proposals.length}`)]);
  section.append(note("未确认的预览不会占用时间；任务或日程变化后需要重新生成。最多显示最近 10 份有效预览。"));
  for (const p of data.proposals) section.append(h("article", { class: "ar-card" }, [text("strong", `${p.preview.kind === "manual_move" ? "改期" : "排期"}建议`),
    note(`生成于 ${format(p.created_at)} · 新增 ${p.preview.additions.length} 个时间块 · ${p.preview.unallocated.length} 项未完全安排`),
    actions(btn("核对方案", async () => { try { await c.showPreview(await api.planningProposal(p.id)); } catch (e) { toast(e.message); } }, true))]));
  host.append(section);
}

export function renderPlanning(host, data, ctx) {
  if (!data) return;
  const c = controller(data, ctx), section = h("section", { class: "pa-section", "aria-label": "智能安排" });
  section.append(text("h3", "时间安排"), note(`${data.coverage} · 上海时区`),
    actions(btn(data.preferences.configured ? "可用时段与容量" : "先设置可用时段", c.preferences, !data.preferences.configured),
      btn("登记固定安排", () => c.eventDialog()), btn("生成排期建议", () => c.previewDialog(), data.preferences.configured), btn("导出日历文件", c.export), btn("变更历史", c.history)));
  const filters = h("form", { class: "pa-range" }, [field("开始日期", "start_date", { type: "date", value: data.start_date, required: true }),
    field("结束日期", "end_date", { type: "date", value: data.end_date, required: true })]);
  section.append(wire(filters, async () => {
    const f = formValues(filters);
    if (f.end_date < f.start_date || (new Date(f.end_date) - new Date(f.start_date)) / 86400000 > 30) throw new Error("请选择 1 至 31 天的日期范围");
    range = { start_date: f.start_date, end_date: f.end_date }; await ctx.refresh();
  }, "查看日期范围"));
  if (data.conflicts.length) section.append(h("details", { class: "pa-conflicts", open: true }, [text("summary", `${data.conflicts.length} 处冲突或缓冲不足（最多列出 100 处）`),
    ...data.conflicts.map((x) => note(`${x.first.title} 与 ${x.second.title}：${x.kind === "overlap" ? "时间重叠" : "缓冲不足"}`))]));
  for (const issue of data.issues || []) section.append(note(issue));
  let day = data.start_date;
  while (day <= data.end_date) {
    const left = new Date(`${day}T00:00:00+08:00`), right = new Date(left.getTime() + 86400000);
    const rows = [...data.events.map((r) => ({ ...r, kind: "event" })), ...data.blocks.map((r) => ({ ...r, kind: "block" }))]
      .filter((r) => new Date(r.start_at) < right && new Date(r.end_at) > left).sort((a, b) => a.start_at.localeCompare(b.start_at));
    const group = h("section", { class: "pa-day" }, [text("h4", day)]);
    if (!rows.length) group.append(note("没有已确认安排；是否可工作以你设置的时段为准。"));
    for (const item of rows) {
      const card = h("article", { class: `ar-card pa-timeline-${item.kind}` }, [h("div", { class: "ar-card-head" }, [text("strong", item.title),
        text("span", item.kind === "event" ? "固定安排" : item.locked ? "专注时间 · 已锁定" : "专注时间", "ar-tag")]), note(span(item))]);
      if (item.kind === "event") card.append(actions(btn("调整", () => c.eventDialog(item)), btn("取消", () => c.cancelEvent(item))));
      else card.append(c.blockButtons(item));
      group.append(card);
    }
    section.append(group);
    day = dateKey(new Date(right.getTime() + 12 * 3600000));
  }
  const tasks = data.tasks.filter((t) => !["done", "cancelled"].includes(t.status));
  section.append(h("details", { class: "pa-task-settings" }, [text("summary", `任务排期信息 · ${tasks.length}`), note("填写剩余工作量、能否拆分和前置任务；实际完成仍需你明确反馈。"),
    ...tasks.map((t) => {
      const opt = data.timings.find((o) => o.id === t.id);
      return h("article", { class: "ar-card" }, [text("strong", t.title), note(`剩余：${opt?.remaining_minutes ?? t.estimated_minutes ?? "待估算"} 分钟 · ${opt?.splittable ? "允许拆分" : "整段完成"} · 前置任务 ${opt?.dependency_ids.length || 0} 项`),
        actions(btn("排期信息", () => c.timing(t.id)), btn("任务详情", () => ctx.openTask(t.id)))]);
    })]));
  host.append(section);
  renderPlanningInbox(host, data, ctx);
  return c;
}
