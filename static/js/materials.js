import { currentUserId } from "./identity.js?v=__APP_VERSION__";
import { h, toast } from "./core.js?v=__APP_VERSION__";
import { api } from "./api.js?v=__APP_VERSION__";

const questionDrafts = new Map();
export function resetMaterialDrafts() { questionDrafts.clear(); }
export function hasMaterialDrafts() { return [...questionDrafts.values()].some((draft) => draft.text.trim()); }

const STATES = { queued: "等待处理", running: "处理中", awaiting_configuration: "等待服务配置", awaiting_consent: "等待云处理许可", awaiting_review: "需核对云端请求", partial: "部分完成", completed: "已完成", failed: "未完成", cancelled: "已停止" };
const CATEGORIES = { conclusions: "结论", risks: "风险与注意事项", topics: "会议议题", decisions: "正式决议", open_questions: "未决问题" };
const note = (value) => h("p", { class: "ar-note", text: value });
const button = (value, fn, primary = false) => h("button", { type: "button", class: `btn btn-${primary ? "primary" : "ghost"} btn-sm`, text: value, onClick: fn });
const heading = (value) => h("h3", { text: value });
const error = (box, err) => { box.textContent = err.message || "处理未完成，请稍后重试"; box.className = "ar-error"; };
const time = (n) => Number.isFinite(n) ? `${Math.floor(n / 60).toString().padStart(2, "0")}:${Math.floor(n % 60).toString().padStart(2, "0")}` : "时间待核对";

function modal(dialog, title) {
  dialog.replaceChildren(h("div", { class: "ar-dialog-head" }, [h("h2", { id: "arDialogTitle", text: title }), button("关闭", () => dialog.close())]));
  if (!dialog.open) dialog.showModal();
  return dialog;
}

function field(label, name, { type = "text", value = "", required = false, maxLength = 200, options } = {}) {
  const el = options ? h("select", { name, class: "field-input" }, options.map(([v, l]) => h("option", { value: v, text: l }))) :
    h(type === "textarea" ? "textarea" : "input", { name, type: type === "textarea" ? null : type, required, maxLength, class: "field-input", rows: type === "textarea" ? 6 : null });
  if (type !== "file") el.value = options && !options.some(([key]) => key === value) ? options[0]?.[0] || "" : value;
  return h("label", { class: "ar-field" }, [h("span", { text: label }), el]);
}

function cloudCheckbox() {
  const control = h("input", { type: "checkbox", name: "allow_cloud" });
  return { control, node: h("label", { class: "ar-checkbox" }, [control,
    "允许我在本机配置的云服务处理这份资料：OCR 会发送页面图像，转写会发送录音，文本模型会接收提取后的相关文字；可能产生服务费用。"])};
}

async function work(form, action) {
  const box = form.querySelector("[data-error]");
  const buttons = [...form.querySelectorAll("button")];
  buttons.forEach((b) => { b.disabled = true; }); box.textContent = "正在保存／处理…"; box.className = "ar-note";
  try { await action(); }
  catch (err) { error(box, err); }
  finally { buttons.forEach((b) => { b.disabled = false; }); }
}

function errors() { return h("div", { "data-error": "", role: "status" }); }

export function uploadDialog(dialog, onSaved, status = {}) {
  const box = modal(dialog, "上传文档或会议录音");
  box.append(note("DOCX／文本最多 10 MB；PDF 最多 50 MB／300 页；录音最多 200 MB／120 分钟。上传后可离开页面，处理进度保存在服务端。请移除不希望发送给云服务的内容。"));
  const form = h("form", { class: "ar-form" });
  const fileField = field("选择 PDF、DOCX、MD、TXT 或录音文件", "file", { type: "file", required: true });
  fileField.querySelector("input").accept = ".pdf,.docx,.md,.markdown,.txt,.csv,.json,.mp3,.wav,.m4a,.mp4,.webm,.ogg,.flac";
  const consent = cloudCheckbox();
  form.append(fileField, field("资料标题（可留空，默认文件名）", "title"), consent.node,
    field("原始时间（上海时区；未知可留空）", "original_at", { type: "datetime-local" }),
    note("未允许云处理时仍会保存文件和提取 PDF 原生文字；扫描页与录音将等待你的许可。服务缺少配置时会保留进度。"),
    serviceStatus(status), errors(), h("button", { type: "submit", class: "btn btn-primary", text: "上传并处理" }));
  form.addEventListener("submit", (e) => { e.preventDefault(); work(form, async () => {
    const file = form.querySelector('[name="file"]').files[0];
    if (!file) throw new Error("请选择文件");
    const extension = file.name.split(".").pop().toLowerCase();
    const limit = ["docx", "md", "markdown", "txt", "csv", "json"].includes(extension) ? 10 : extension === "pdf" ? 50 : 200;
    if (file.size > limit * 1024 * 1024) throw new Error(`文件超过 ${limit} MB，请先拆分`);
    const data = new FormData(); data.append("file", file); data.append("title", form.querySelector('[name="title"]').value);
    data.append("allow_cloud", String(consent.control.checked));
    const original = form.querySelector('[name="original_at"]').value;
    if (original) data.append("original_at", new Date(`${original}:00+08:00`).toISOString());
    const response = await api.materialUpload(data);
    toast(response.data.reused ? "已找到相同资料" : "文件已保存，后台开始处理");
    if (dialog.open && form.isConnected) { dialog.close(); onSaved(response.data.source_id); }
  }); });
  box.append(form);
}

export function webDialog(dialog, onSaved, status = {}) {
  const box = modal(dialog, "读取公开网页");
  const form = h("form", { class: "ar-form" }, [field("标题", "title", { required: true }), field("公开网页地址", "url", { type: "url", required: true, maxLength: 2000 })]);
  const consent = cloudCheckbox();
  form.append(consent.node, note("不访问登录页面或本机／内网地址；动态网页无法读取时可粘贴正文。"), serviceStatus(status), errors(),
    h("button", { type: "submit", class: "btn btn-primary", text: "保存并读取" }));
  form.addEventListener("submit", (e) => { e.preventDefault(); work(form, async () => {
    const result = await api.materialWeb({ title: form.querySelector('[name="title"]').value, url: form.querySelector('[name="url"]').value, allow_cloud: consent.control.checked });
    if (dialog.open && form.isConnected) { dialog.close(); onSaved(result.data.source_id); }
    else toast("网页已保存，可从所有文档查看");
  }); });
  box.append(form);
}

function serviceStatus(status) {
  const describe = (state) => `文本模型：${state.model_ready ? "已配置" : "未配置"} · OCR：${state.cloud_services?.ocr?.configured ? "已配置" : "未配置"} · 转写：${state.cloud_services?.asr?.configured ? "已配置" : "未配置"}`;
  if (Object.hasOwn(status, "model_ready")) return note(describe(status));
  const region = note("正在读取模型配置…");
  api.arrangementStatus().then((response) => {
    if (region.isConnected) region.textContent = describe(response.data);
  }).catch(() => {
    if (region.isConnected) region.textContent = "模型配置状态暂时无法读取，可从左侧「设置」查看。";
  });
  return region;
}

export function renderMaterial({ source, container, dialog, status, onReload, onOpen }) {
  const material = source.material;
  const root = h("section", { class: "mt-material" });
  const selectedVersion = material.selected_version;
  const old = selectedVersion && selectedVersion !== material.version;
  root.append(note(`版本 ${selectedVersion || "尚未提取"}${old ? " · 正在查看历史版本，已确认任务仍引用这个版本" : ""}`));
  if (old) root.append(button("查看最新版本", () => onOpen(material.current_source_id)));
  root.append(serviceStatus(status));
  const jobs = h("div", { class: "mt-jobs" }); root.append(jobs);
  let lastSignature = "";
  let lastJobs = new Map(material.jobs.map((j) => [j.id, j.state]));

  function consentAction(title, action, uncertain = false) {
    const box = modal(dialog, title);
    const form = h("form", { class: "ar-form" });
    const consent = cloudCheckbox();
    form.append(consent.node, serviceStatus(status));
    let acknowledge;
    if (uncertain) {
      acknowledge = h("input", { type: "checkbox" });
      form.append(h("label", { class: "ar-checkbox" }, [acknowledge, "我已核对上次云端请求，确认重试；这可能产生重复处理费用。"]));
    }
    form.append(errors(), h("button", { type: "submit", class: "btn btn-primary", text: "确认并继续" }));
    form.addEventListener("submit", (e) => { e.preventDefault(); work(form, async () => {
      if (uncertain && !acknowledge.checked) throw new Error("请先核对并确认上次云端请求");
      await action({ allow_cloud: consent.control.checked, acknowledge_uncertain: !!acknowledge?.checked });
      dialog.close(); await onReload();
    }); });
    box.append(form);
  }

  function showJobs(records) {
    jobs.replaceChildren();
    for (const job of records.slice(0, 4)) {
      const row = h("article", { class: "mt-job" }, [h("strong", { text: `${job.kind === "parse" ? "原文提取／转写" : "速读／纪要"} · ${STATES[job.state] || job.state}` }),
        note(`${job.stage}${job.total ? ` · 已完成 ${job.done} / ${job.total} 组` : ""}`), job.message ? note(job.message) : null]);
      if (["queued", "running"].includes(job.state)) row.append(button("停止后续处理", async () => {
        try { await api.materialCancel(job.id); await onReload(); } catch (err) { toast(err.message || "停止失败"); }
      }));
      else if (job.state !== "completed") row.append(button("核对后继续／重试", () => consentAction("继续处理资料", (value) => api.materialRetry(job.id, value), ["awaiting_review", "cancelled"].includes(job.state))));
      jobs.append(row);
    }
  }
  showJobs(material.jobs);
  root.append(coverage(material));
  const actions = h("div", { class: "ar-actions" });
  if (material.units.length) actions.append(button(material.kind === "audio" ? "生成会议纪要" : "生成文档速读", () => consentAction("生成有原文依据的整理结果", (value) => api.materialRead(source.id, value)), true));
  if (material.units.length && !old) {
    actions.append(button("修订转写／原文", () => reviseDialog(source, dialog, onOpen)));
    if (material.kind === "audio") actions.append(button("映射发言人与本人身份", () => speakersDialog(source, dialog, onOpen)));
  }
  if (material.kind !== "web") actions.append(h("a", { class: "btn btn-ghost btn-sm", href: `/api/arrangements/sources/${encodeURIComponent(source.id)}/file`, download: material.filename, text: "下载原始文件" }));
  actions.append(button("删除资料", () => {
    const box = modal(dialog, "删除这份资料");
    const form = h("form", { class: "ar-form" }, [note(`将删除「${source.title}」的原始文件、所有修订版本的文字、纪要、问答和引用摘录。已确认任务及其状态保留，出处标记为已删除。已经发出的云端请求无法撤销。`)]);
    const confirm = h("input", { type: "checkbox", required: true });
    form.append(h("label", { class: "ar-checkbox" }, [confirm, "我确认删除上述资料内容"]), errors(), h("button", { type: "submit", class: "btn btn-primary", text: "确认删除资料" }));
    form.addEventListener("submit", (e) => { e.preventDefault(); work(form, async () => {
      if (!confirm.checked) throw new Error("请先确认删除范围");
      const result = await api.materialDelete(source.id);
      if (result.data.file_cleanup_pending) throw new Error(result.data.message);
      dialog.close(); toast("资料已删除，已确认任务保留"); onOpen("");
    }); });
    box.append(form);
  }));
  root.append(actions);
  const overview = h("section", { class: "mt-report" });
  const unitBox = h("div", { class: "mt-units" });
  const preview = h("div", { class: "mt-preview" });
  const previewOption = h("details", { class: "mt-preview-option" }, [
    h("summary", { text: material.kind === "audio" ? "播放原始录音（可选）" : material.kind === "pdf" ? "预览原始 PDF（可选）" : "查看来源地址" }), preview,
  ]);
  let image, audio, page = 0;
  const unitMap = new Map(material.units.map((u) => [u.id, u]));
  const names = material.speakers || {};
  if (material.kind === "pdf") {
    image = h("img", { alt: "PDF 第 1 页预览", loading: "lazy", src: `/api/arrangements/sources/${source.id}/pages/1` });
    image.addEventListener("error", () => { image.alt = "页面预览暂不可用，请下载原文件核对"; });
    preview.append(image);
  } else if (material.kind === "audio") {
    audio = h("audio", { controls: true, preload: "metadata", src: `/api/arrangements/sources/${source.id}/file` });
    preview.append(audio, note("点击转写时间点可播放对应录音。身份只根据你的映射确认。"));
  } else preview.append(note(material.url));

  function locatorLabel(unit) {
    const loc = unit.locator;
    if (loc.page) return `第 ${loc.page} 页${loc.table ? ` · 表格 ${loc.table}` : ""}`;
    if (Number.isFinite(loc.start)) return `${time(loc.start)}–${time(loc.end)} · ${names[loc.speaker] || (loc.speaker === "unknown" ? "发言人待确认" : loc.speaker)}`;
    return `${["text", "docx"].includes(material.kind) ? "正文片段" : "网页片段"} ${unit.ordinal + 1}`;
  }

  function locate(evidence) {
    const unit = unitMap.get(evidence.unit_id);
    if (!unit) { toast("该引用来自其他版本，请切换到对应来源"); return; }
    page = Math.floor(material.units.indexOf(unit) / 25); showUnits(evidence);
    if (image && unit.locator.page) {
      image.src = `/api/arrangements/sources/${source.id}/pages/${unit.locator.page}`;
      image.alt = `PDF 第 ${unit.locator.page} 页预览`;
    }
    if (audio && Number.isFinite(unit.locator.start)) {
      previewOption.open = true;
      const seek = () => {
        if (!audio.isConnected) return;
        try { audio.currentTime = unit.locator.start; audio.play()?.catch(() => {}); } catch { /* The audio controls remain available for manual playback. */ }
      };
      if (audio.readyState >= 1) seek();
      else audio.addEventListener("loadedmetadata", seek, { once: true });
    }
    unitBox.querySelector(`[data-unit-id="${unit.id}"]`)?.scrollIntoView?.({ block: "center", behavior: "auto" });
  }

  function citations(items) {
    return h("div", { class: "mt-citations" }, items.map((e) => button(unitMap.has(e.unit_id) ? locatorLabel(unitMap.get(e.unit_id)) : "来源待核对", () => locate(e))));
  }

  function showUnits(evidence) {
    unitBox.replaceChildren();
    const pages = Math.max(1, Math.ceil(material.units.length / 25));
    unitBox.append(h("div", { class: "ar-actions" }, [
      button("上一组", () => { page = Math.max(0, page - 1); showUnits(); }), note(`${page + 1} / ${pages} 组 · 共 ${material.units.length} 段`),
      button("下一组", () => { page = Math.min(pages - 1, page + 1); showUnits(); }),
    ]));
    for (const unit of material.units.slice(page * 25, page * 25 + 25)) {
      const snippet = h("pre", { class: "ar-source-text" });
      if (evidence?.unit_id === unit.id && evidence.quote && unit.text.includes(evidence.quote)) {
        const at = unit.text.indexOf(evidence.quote);
        snippet.append(document.createTextNode(unit.text.slice(0, at)), h("mark", { text: evidence.quote }), document.createTextNode(unit.text.slice(at + evidence.quote.length)));
      } else snippet.textContent = unit.text;
      const card = h("article", { class: "mt-unit", "data-unit-id": unit.id }, [button(locatorLabel(unit), () => locate({ unit_id: unit.id, quote: "" })), snippet]);
      if (unit.locator.uncertain?.length) card.append(note(`识别提示：${unit.locator.uncertain.join("；")}`));
      if (unit.locator.rows) {
        const table = h("table", { class: "mt-table" }, [h("tbody", {}, unit.locator.rows.map((row) => h("tr", {}, row.map((cell) => h("td", { text: cell || "（空白）" })))))]);
        card.append(h("details", {}, [h("summary", { text: "查看提取的表格结构" }), h("div", { class: "mt-table-scroll" }, [table])]));
      }
      unitBox.append(card);
    }
  }
  showUnits();

  const report = material.artifacts.find((a) => a.kind === "reading");
  if (report) {
    overview.append(heading(material.kind === "audio" ? "会议纪要" : "文档速读"));
    if (report.data.partial) overview.append(note("这是部分整理结果，未完成范围见处理状态与覆盖说明。"));
    overview.append(note(`已整理 ${report.data.coverage.completed_batches} / ${report.data.coverage.total_batches} 组。${report.data.coverage.note}`));
    const renderFinding = (item) => h("article", { class: "mt-finding" }, [h("p", { text: item.text }),
      item.origin === "analysis" ? h("span", { class: "ar-tag", text: "Agent 分析，需核实" }) : null, citations(item.evidence)]);
    overview.append(heading("总览"), ...report.data.overview.map(renderFinding));
    for (const [key, label] of Object.entries(CATEGORIES)) {
      const items = report.data.findings.flatMap((f) => f[key] || []);
      if (items.length) overview.append(h("details", { open: key === "decisions" }, [h("summary", { text: `${label} · ${items.length}` }), ...items.map(renderFinding)]));
    }
    const points = report.data.findings.flatMap((f) => f.key_data || []);
    if (points.length) overview.append(heading("关键数据与口径"), ...points.map((p) => h("article", { class: "mt-finding" }, [
      h("strong", { text: `${p.label}：${p.value}${p.unit ? ` ${p.unit}` : ""}` }), note(`时间：${p.period || "原文未注明"} · 口径：${p.scope || "原文未注明"}`), citations(p.evidence),
    ])));
  }
  root.append(overview, heading(material.kind === "audio" ? "录音与转写" : material.kind === "pdf" ? "原文与页码" : "文档正文"), h("div", { class: "mt-layout" }, [
    ...(["pdf", "audio"].includes(material.kind) || material.url ? [previewOption] : []), unitBox,
  ]));

  const qa = h("section", { class: "mt-qa" }, [heading("基于这份资料追问")]);
  for (const item of material.artifacts.filter((a) => a.kind === "qa").slice(-10)) {
    qa.append(h("article", { class: "mt-finding" }, [h("strong", { text: item.data.question }), h("p", { text: item.data.answer }),
      item.data.missing ? note(item.data.missing) : null, note(item.data.scope), citations(item.data.evidence)]));
  }
  if (material.units.length) {
    const form = h("form", { class: "ar-form" }, [field("问题", "question", { type: "textarea", required: true, maxLength: 2000 })]);
    const owner = currentUserId();
    const key = `${owner}:${source.id}`;
    const draft = questionDrafts.get(key) || { text: "", requestId: "" };
    const input = form.querySelector("textarea");
    input.value = draft.text;
    input.addEventListener("input", () => {
      if (input.value !== draft.text) { draft.text = input.value; draft.requestId = ""; }
      questionDrafts.set(key, draft);
    });
    const consent = cloudCheckbox();
    form.append(consent.node, errors(), h("button", { type: "submit", class: "btn btn-primary btn-sm", text: "依据原文回答" }));
    form.addEventListener("submit", (e) => { e.preventDefault(); work(form, async () => {
      if (!consent.control.checked) throw new Error("请先允许已配置文本模型处理问题与相关片段");
      const question = input.value;
      if (question !== draft.text) { draft.text = question; draft.requestId = ""; }
      draft.requestId ||= crypto.randomUUID();
      const requestId = draft.requestId;
      questionDrafts.set(key, draft);
      await api.materialQuestion(source.id, { question, allow_cloud: true, request_id: requestId });
      if (owner !== currentUserId()) return;
      if (draft.text === question && draft.requestId === requestId) questionDrafts.delete(key);
      if (root.isConnected) await onReload();
      else toast("原文回答已保存，回到这份资料后刷新即可查看");
    }); });
    qa.append(form);
  }
  root.append(qa);
  container.append(root);

  // Refresh job state without replacing an actively edited question or dialog.
  const signature = (m) => JSON.stringify([m.current_source_id, m.units?.length, m.jobs.map((j) => [j.id, j.state, j.done, j.stage])]);
  lastSignature = signature(material);
  async function poll() {
    if (!root.isConnected) return;
    try {
      const response = await api.arrangementSource(source.id);
      if (!root.isConnected) return;
      const next = response.data.material;
      showJobs(next.jobs);
      const finished = next.jobs.some((j) => !["queued", "running"].includes(j.state) && lastJobs.get(j.id) !== j.state);
      if (signature(next) !== lastSignature && (finished || next.current_source_id !== material.current_source_id || next.units.length !== material.units.length)) {
        const editing = dialog.open || (root.contains(document.activeElement) || qa.contains(document.activeElement)) && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
        if (!editing) { onOpen(next.current_source_id); return; }
        if (!root.querySelector(".mt-new-result")) root.prepend(h("div", { class: "mt-new-result" }, [button("后台结果已更新，点击刷新", () => onOpen(next.current_source_id))]));
      }
      lastSignature = signature(next);
      lastJobs = new Map(next.jobs.map((j) => [j.id, j.state]));
      if (next.jobs.some((j) => ["queued", "running"].includes(j.state))) window.setTimeout(poll, 2500);
    } catch { /* preserve readable content and the explicit refresh action */ }
  }
  if (material.jobs.some((j) => ["queued", "running"].includes(j.state))) window.setTimeout(poll, 2500);
  return { locate, qa };
}

function coverage(material) {
  const c = material.coverage || {};
  const box = h("section", { class: "mt-coverage" });
  if (c.total_pages) box.append(note(`页面覆盖：${c.completed_pages?.length || 0} / ${c.total_pages} 页提取完成。${material.partial ? "仍有页面需要处理或核对。" : ""}`));
  for (const i of c.issues || []) box.append(note(`第 ${i.page} 页：${i.message}`));
  if (c.completed_chunks) box.append(note(`录音：已处理 ${c.completed_chunks.length} 块，已知处理时长 ${time(c.duration_seconds)}${c.incomplete ? "，后续录音尚未完成" : ""}。`));
  for (const warning of c.warnings || []) box.append(note(warning));
  if (c.note) box.append(note(c.note));
  return box;
}

function reviseDialog(source, dialog, onOpen) {
  const m = source.material;
  const box = modal(dialog, "修订原文／转写");
  box.append(note("修订生成新版本；旧纪要、引用和已确认任务保持原样。重新生成纪要后，请核对新旧任务变化。"));
  const form = h("form", { class: "ar-form" });
  const options = m.units.map((u) => [u.id, `${u.ordinal + 1} · ${u.text.slice(0, 45)}`]);
  form.append(field("选择片段", "unit_id", { options }), field("修订文字", "text", { type: "textarea", required: true, value: m.units[0].text, maxLength: 4000 }));
  form.querySelector("select").addEventListener("change", (e) => { form.querySelector("textarea").value = m.units.find((u) => u.id === e.target.value).text; });
  form.append(errors(), h("button", { type: "submit", class: "btn btn-primary", text: "确认保存为新版本" }));
  form.addEventListener("submit", (e) => { e.preventDefault(); work(form, async () => {
    const unit = m.units.find((u) => u.id === form.querySelector("select").value);
    const response = await api.materialRevise(source.id, { version: m.selected_version, confirmed: true,
      edits: [{ unit_id: unit.id, text: form.querySelector("textarea").value, speaker: unit.locator.speaker || "" }], speaker_names: m.speakers, self_speakers: m.self_speakers });
    dialog.close(); onOpen(response.data.source_id);
  }); });
  box.append(form);
}

function speakersDialog(source, dialog, onOpen) {
  const m = source.material;
  const labels = [...new Set(m.units.map((u) => u.locator.speaker).filter((v) => v && v !== "unknown"))];
  const box = modal(dialog, "确认发言人与本人身份");
  if (!labels.length) { box.append(note("转写服务未提供可区分的发言人标记。请配置支持说话人分段的服务后重新处理；不能从未知标记推断身份。")); return; }
  box.append(note("同一人在不同录音块中可能有不同标记，可填写相同姓名。只勾选明确属于你的发言人，后续纪要才能识别你的承诺。"));
  const form = h("form", { class: "ar-form" });
  const rows = labels.map((label) => {
    const name = field(`${label} 的姓名（可留空）`, "speaker", { value: m.speakers[label] || "", maxLength: 80 });
    const own = h("input", { type: "checkbox" }); own.checked = m.self_speakers.includes(label);
    form.append(name, h("label", { class: "ar-checkbox" }, [own, "这位发言人是我"]));
    return { label, name: name.querySelector("input"), own };
  });
  form.append(errors(), h("button", { type: "submit", class: "btn btn-primary", text: "确认映射并生成新版本" }));
  form.addEventListener("submit", (e) => { e.preventDefault(); work(form, async () => {
    const names = Object.fromEntries(rows.filter((r) => r.name.value.trim()).map((r) => [r.label, r.name.value.trim()]));
    const self = rows.filter((r) => r.own.checked).map((r) => r.label);
    const response = await api.materialRevise(source.id, { version: m.selected_version, confirmed: true, speaker_names: names, self_speakers: self, edits: [] });
    dialog.close(); onOpen(response.data.source_id);
  }); });
  box.append(form);
}
