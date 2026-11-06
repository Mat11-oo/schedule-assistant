import { h, toast, nextId } from "./core.js?v=__APP_VERSION__";
import { api } from "./api.js?v=__APP_VERSION__";

const LABELS = { schedule: "今日安排", due: "临期任务", overdue: "逾期任务", waiting: "等待事项", conflicts: "时间冲突", decisions: "待决定事项" };
const STATES = { todo: "未开始", doing: "进行中", waiting: "等待中", done: "已完成", cancelled: "已取消" };
const text = (tag, value, cls = "") => h(tag, { text: value, class: cls });
const note = (value) => text("p", value, "ar-note");
const btn = (label, fn, primary = false) => h("button", { type: "button", text: label, class: `btn btn-${primary ? "primary" : "ghost"} btn-sm`, onClick: fn });
const actions = (...nodes) => h("div", { class: "ar-actions" }, nodes);
const format = (value) => value ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "未设置";
const local = (value) => value ? new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(value)).replace(" ", "T") : "";
const iso = (value) => value ? new Date(`${value}:00+08:00`).toISOString() : null;
const check = (label, name, checked = false) => h("label", { class: "ar-checkbox" }, [h("input", { type: "checkbox", name, checked }), text("span", label)]);

function field(label, name, { value = "", type = "text", required = false, options, min, max } = {}) {
  const id = nextId("fu");
  const input = options ? h("select", { id, name, class: "field-input" }, Object.entries(options).map(([k, v]) => h("option", { value: k, text: v }))) :
    h("input", { id, name, type, class: "field-input", required, min, max, maxLength: type === "text" ? 1000 : null });
  input.value = value ?? "";
  return h("label", { class: "ar-field", for: id }, [text("span", label), input]);
}
async function safe(action) {
  try { await action(); } catch (e) { toast(e.message || "操作未完成，请重试"); }
}
function formSubmit(form, label, action) {
  const error = h("div", { class: "ar-error", role: "alert" });
  form.append(error, h("button", { type: "submit", class: "btn btn-primary", text: label }));
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); if (!form.reportValidity()) return;
    const buttons = [...form.querySelectorAll("button")]; buttons.forEach((b) => { b.disabled = true; }); error.textContent = "";
    try { await action(); } catch (e) { if (error.isConnected) error.textContent = e.message || "保存失败，请刷新后重试"; }
    finally { buttons.forEach((b) => { b.disabled = false; }); }
  });
  return form;
}

export function renderBriefContents(host, brief, ctx) {
  host.append(note(`截至 ${format(brief.generated_at)} · 上海时区`), note(brief.coverage));
  const summary = h("div", { class: "fu-stats" });
  for (const key of brief.sections) summary.append(text("div", `${LABELS[key]} ${brief.counts[key]} 项`));
  host.append(summary);
  for (const key of ["due", "overdue", "waiting"]) {
    const rows = brief.groups[key];
    if (!rows) continue;
    const section = h("details", { open: rows.length > 0 }, [text("summary", key === "due" ? `未来 ${brief.lookahead_days} 天内到期 · ${brief.counts[key]}` : `${LABELS[key]} · ${brief.counts[key]}`)]);
    for (const task of rows) section.append(h("article", { class: "fu-line" }, [text("strong", task.title), note(`截止：${format(task.due_at)} · ${STATES[task.status]}`),
      btn("查看当前任务", () => safe(() => ctx.openTask(task.id)))]));
    if (!rows.length) section.append(note("没有符合条件的任务。"));
    host.append(section);
  }
  if (brief.groups.schedule) host.append(h("details", { open: false }, [text("summary", `今日已确认安排 · ${brief.counts.schedule}`),
    ...brief.groups.schedule.map((r) => h("article", { class: "fu-line" }, [text("strong", r.title), note(`${format(r.start_at)} — ${format(r.end_at)} · ${r.kind === "event" ? "固定安排" : "专注时间"}`)]))]));
  if (brief.groups.conflicts) {
    for (const c of brief.groups.conflicts) host.append(note(`${c.first.title} 与 ${c.second.title}：${c.kind === "overlap" ? "时间重叠" : "缓冲不足"}`));
    for (const message of brief.groups.issues || []) host.append(note(message));
  }
  if (brief.decisions) host.append(note(`需要决定：${brief.decisions.candidates} 条候选事项、${brief.decisions.plans} 份排期、${brief.decisions.updates} 份来源变更。`),
    actions(btn("打开待处理", () => { ctx.dialog?.close(); ctx.navigate("inbox"); }), btn("查看时间安排", () => { ctx.dialog?.close(); ctx.navigate("today"); })));
  host.append(note(brief.limits));
}

export async function openSavedBrief(id, ctx) {
  await safe(async () => {
    const result = await api.followupSavedBrief(id), box = ctx.openDialog(`${result.data.brief.day} 已保存简报`);
    box.append(note("这是保存当时的快照；下方任务按钮会打开当前状态。"));
    renderBriefContents(box, result.data.brief, ctx);
  });
}

export function briefSettings(state, ctx) {
  const p = state.preferences, box = ctx.openDialog("每日简报设置");
  box.append(note("只推送到站内收件箱，不向飞书、微信或邮件发送。应用与后台提醒调度器运行时才会生成；错过时间时补一份当天简报，不补发历史日期。"));
  const form = h("form", {}, [check("开启每日站内简报", "enabled", p.enabled),
    field("每天生成时间（上海时区）", "at", { type: "time", value: p.at, required: true }),
    field("临期任务查看天数（含今天）", "lookahead", { type: "number", min: 1, max: 14, value: p.lookahead_days, required: true }), note("选择简报内容：")]);
  for (const [key, label] of Object.entries(LABELS)) form.append(check(label, `section_${key}`, p.sections.includes(key)));
  box.append(formSubmit(form, "确认保存简报设置", async () => {
    const f = new FormData(form), sections = Object.keys(LABELS).filter((k) => f.has(`section_${k}`));
    if (!sections.length) throw new Error("请至少选择一项简报内容");
    await api.followupBriefPreferences({ confirmed: true, version: p.version, enabled: f.has("enabled"), at: f.get("at"), lookahead_days: Number(f.get("lookahead")), sections });
    ctx.dialog.close(); toast("简报设置已保存；关闭推送不会改变任务"); await ctx.refresh();
  }));
}

export function renderBrief(host, state, ctx) {
  if (!state) return;
  const section = h("section", { class: "ar-card fu-brief", "aria-label": "今日简报" }, [h("div", { class: "ar-card-head" }, [text("h3", "今日简报"),
    text("span", state.preferences.enabled ? `站内推送 ${state.preferences.at}` : "每日推送未开启", "ar-tag")])]);
  renderBriefContents(section, state.brief, ctx);
  section.append(actions(btn("简报设置", () => briefSettings(state, ctx)), btn("保存当前简报快照", () => safe(async () => {
    await api.followupSaveBrief(); toast("已保存当前简报快照"); await ctx.refresh();
  }))));
  if (state.saved.length) section.append(h("details", {}, [text("summary", "最近保存的简报"), ...state.saved.map((r) =>
    h("div", { class: "fu-line" }, [note(`${format(r.created_at)} · ${r.origin === "scheduled" ? "定时生成" : "手动保存"}`), btn("查看快照", () => openSavedBrief(r.id, ctx))]))]));
  host.append(section);
}

export function showSourceUpdate(proposal, ctx) {
  const box = ctx.openDialog("来源变更预览");
  box.append(text("h3", proposal.task_title), note(`来自 ${proposal.source_title}，原始时间 ${proposal.source_original_at || "未提供"}，来源时区 ${proposal.source_tz}`),
    text("blockquote", proposal.quote), note(`原文时间表述：${proposal.deadline_text || "未明确"}`), note(`变更原因：${proposal.reason}`));
  const table = h("table", { class: "fu-diff" }, [h("thead", {}, [h("tr", {}, [text("th", "字段"), text("th", "原值"), text("th", "确认后")])])]);
  const rows = h("tbody");
  const show = (key, value) => key === "due_at" ? format(value) : key === "status" ? STATES[value] : value;
  for (const [key, value] of Object.entries(proposal.changes)) rows.append(h("tr", {}, [text("td", { title: "任务名称", due_at: "截止时间", status: "责任处理／状态" }[key]),
    text("td", show(key, proposal.before[key])), text("td", show(key, value))]));
  table.append(rows); box.append(table, note("确认会关联这条新出处；已有排期预览会失效。等待或取消会撤销未结束时间块；取消还会撤销任务提醒。日期变化不会自动移动已确认时间块。"));
  if (proposal.stale) box.append(note("任务或候选已变化，这份预览不能再保存；请放弃后重新核对。"));
  const form = h("form", {}, [check("我已核对出处、原值与新值，确认更新该任务", "confirm")]);
  formSubmit(form, "确认应用这份变更", async () => {
    if (!new FormData(form).has("confirm")) throw new Error("请先勾选确认");
    const result = await api.followupConfirmUpdate(proposal.id);
    ctx.dialog.close(); toast(result.data.message); await ctx.refresh();
    if (result.data.replan_recommended) ctx.navigate("today");
  });
  form.querySelector('button[type="submit"]').disabled = proposal.stale || proposal.status !== "preview" || new Date(proposal.expires_at) <= new Date();
  form.append(btn("放弃变更建议", () => safe(async () => { await api.followupDismissUpdate(proposal.id); ctx.dialog.close(); await ctx.refresh(); })));
  box.append(note(`预览有效至 ${format(proposal.expires_at)}`), form);
}

function proposeSourceUpdate(candidate, task, ctx) {
  const box = ctx.openDialog("用新来源核对已有任务");
  box.append(text("h3", task.title), text("blockquote", candidate.quote),
    note(`来源原始时间 ${candidate.source_original_at || "未提供"}，来源时区 ${candidate.source_tz || "待核对"}`),
    note(`原文时间表述 ${candidate.deadline_text || "未明确"}。请依据来源时间核对截止日期。`));
  const form = h("form", {}, [
    check("修改任务名称", "change_title"), field("新任务名称", "title", { value: task.title }),
    check("修改截止时间（勾选后留空表示明确清除）", "change_due"),
    field("新截止时间（上海时区）", "due_at", { type: "datetime-local", value: local(task.due_at) }),
    field("责任变化如何处理", "responsibility", { value: "keep_self", options: { keep_self: "保留当前责任和任务状态", wait_other: "改为等待他人，暂停时间安排", cancel_self: "取消我的这项任务" } }),
    field("变更依据／原因", "reason", { required: true }),
    note("这里只处理你自己的任务，不替他人分配工作或向对方发消息。已完成任务不会自动重开。")]);
  box.append(formSubmit(form, "生成新旧对比，暂不修改任务", async () => {
    const f = new FormData(form);
    const result = await api.followupProposeUpdate(candidate.id, { task_id: task.id, task_version: task.version,
      title: f.get("title"), due_at: iso(f.get("due_at")), change_title: f.has("change_title"), change_due: f.has("change_due"),
      responsibility: f.get("responsibility"), reason: f.get("reason") });
    await ctx.refresh(); showSourceUpdate(result.data, ctx);
  }));
}

export function renderSourceUpdates(host, proposals, ctx) {
  if (!proposals?.length) return;
  const section = h("section", { class: "pa-section" }, [text("h3", `待确认来源变更 · ${proposals.length}`)]);
  for (const p of proposals) section.append(h("article", { class: "ar-card" }, [text("strong", p.task_title),
    note(`${p.source_title} · ${p.stale ? "已有新改动，需重新核对" : "确认后才更新任务"}`), btn("核对新旧差异", () => showSourceUpdate(p, ctx), true)]));
  host.append(section);
}

export async function attachDuplicateSuggestions(host, candidate, { ctx, form, linkControl }) {
  const loading = note("正在检查可能相同的已有任务…"); host.append(loading);
  try {
    const response = await api.followupMatches(candidate.id);
    if (!host.isConnected || !form.isConnected) return;
    host.replaceChildren();
    if (!response.data.length) { host.append(note("未发现标题相近的任务；这不代表语义上一定没有重复。")); return; }
    host.append(text("h3", "可能相同的已有任务"), note("以下是文字和来源关系提示，不会自动合并；不同日期、不同轮次可能是独立任务。"));
    for (const match of response.data) {
      const t = match.task, row = h("article", { class: "ar-card" }, [text("strong", t.title), note(`截止：${format(t.due_at)} · ${STATES[t.status]}`), note(match.reasons.join("；"))]);
      const controls = actions(btn("选为关联任务", () => {
        if (![...linkControl.options].some((o) => o.value === t.id)) linkControl.append(h("option", { value: t.id, text: `${t.title}（${STATES[t.status]}）` }));
        linkControl.value = t.id; linkControl.dispatchEvent(new Event("change", { bubbles: true })); toast("已选中；请点击下方「确认关联」保存，任务内容不会变化");
      }), btn("查看任务", () => safe(() => ctx.openTask(t.id))), btn("这不是同一件事", () => safe(async () => {
        await api.followupDistinct(candidate.id, t.id); row.remove(); toast("已记住这次判断");
      })));
      if (!["done", "cancelled"].includes(t.status)) controls.append(btn("核对来源变更", () => proposeSourceUpdate(candidate, t, ctx)));
      else row.append(note("旧任务已结束；关联新出处不会重新打开任务。"));
      row.append(controls); host.append(row);
    }
  } catch (e) { if (host.isConnected) host.replaceChildren(note(`重复提示暂不可用：${e.message || "请稍后重试"}。仍可手动关联。`)); }
}

export function sourceUpdatePicker(candidate, taskList, ctx) {
  const box = ctx.openDialog("选择需要核对的已有任务"), form = h("form", {}, [note("标题差异较大时可手动选择任务；选择本身不会关联或修改。")]);
  const eligible = taskList.filter((t) => !["done", "cancelled"].includes(t.status));
  form.append(field("已有任务", "task_id", { options: { "": "请选择", ...Object.fromEntries(eligible.map((t) => [t.id, t.title])) } }));
  box.append(formSubmit(form, "继续核对来源变更", async () => {
    const id = new FormData(form).get("task_id");
    if (!id) throw new Error("请选择已有任务");
    const result = await api.arrangementTask(id); proposeSourceUpdate(candidate, result.data.task, ctx);
  }));
}
