import { getWorkspace, starSource, moveSourceDialog } from "./craft.js?v=__APP_VERSION__";
import { h, icon, toast, nextId } from "./core.js?v=__APP_VERSION__";
import { api } from "./api.js?v=__APP_VERSION__";

const defaults = () => ({ q: "", kind: "all", status: "all", tag: "", scope: "latest", start_date: "", end_date: "", offset: 0, folder_id: "all", starred: "false", sort: "recent" });
let filters = defaults();
let layout = "gallery";
export function resetLibrary() { filters = defaults(); layout = "gallery"; }
export function setLibraryLocation(values = {}) {
  const initial = defaults();
  filters = { ...initial, ...Object.fromEntries(Object.entries(values).filter(([key]) => Object.hasOwn(initial, key))) };
}
export function getLibraryLocation() { return { ...filters }; }
export function libraryLocationParams() {
  const initial = defaults();
  return new URLSearchParams(Object.entries(filters).filter(([key, value]) => String(value) !== String(initial[key]))).toString();
}
function clearSearchFilters() {
  filters = { ...defaults(), folder_id: filters.folder_id, starred: filters.starred };
}
const KINDS = { all: "全部类型", message: "消息", document_text: "粘贴文档", meeting_transcript: "会议文字稿", pdf: "PDF", audio: "会议录音", web: "网页" };
const text = (tag, value, cls = "") => h(tag, { text: value, class: cls });
const note = (value) => text("p", value, "ar-note");
const btn = (label, action, primary = false) => h("button", { type: "button", text: label, onClick: action, class: `btn btn-${primary ? "primary" : "ghost"} btn-sm` });
const format = (value) => value ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "未提供";

function field(label, name, value, options = null, type = "text") {
  const id = nextId("library");
  const control = options ? h("select", { name, id, class: "field-input" }, Object.entries(options).map(([key, title]) => h("option", { value: key, text: title }))) :
    h("input", { name, id, type, class: "field-input", maxLength: name === "q" ? 200 : name === "tags" ? 2000 : 1000 });
  control.value = value || "";
  if (name === "q") { control.dataset.librarySearch = ""; control.placeholder = "搜索标题、摘要和原文…"; control.setAttribute("type", "search"); }
  return h("label", { for: id, class: "ar-field" }, [text("span", label), control]);
}

function requestFilters() {
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== ""));
}

function tagsDialog(metadata, title, ctx) {
  const box = ctx.openDialog("编辑资料标签");
  box.append(text("h3", title), note("标签跟随整份资料保存，适用于它的所有修订版本。最多 20 个，每个 30 字，用逗号分隔。留空可清除全部标签。"));
  const input = field("标签", "tags", metadata.tags.join(", "));
  const error = h("div", { class: "ar-error", role: "alert" });
  const save = h("button", { type: "submit", class: "btn btn-primary", text: "确认保存标签" });
  const form = h("form", {}, [input, error, save]);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); save.disabled = true; error.textContent = "";
    try {
      const values = new FormData(form).get("tags").split(/[,，\n]/).map((v) => v.trim()).filter(Boolean);
      await api.librarySaveTags(metadata.source_id, { version: metadata.version, tags: values, confirmed: true });
      toast("资料标签已保存");
      if (ctx.dialog.open && form.isConnected) { ctx.dialog.close(); await ctx.refresh(); }
    } catch (err) { error.textContent = err.message || "标签保存失败，请刷新后重试"; }
    finally { save.disabled = false; }
  });
  box.append(form);
}

export function renderLibraryMetadata(host, source, ctx) {
  const region = h("section", { class: "kb-metadata", "aria-label": "资料标签" }, [note("正在读取标签…")]);
  host.append(region);
  async function load() {
    try {
      const result = await api.libraryMetadata(source.id);
      if (!region.isConnected) return;
      region.replaceChildren(note(result.data.tags.length ? `标签 ${result.data.tags.join(" · ")}` : "尚未设置标签"),
        btn("编辑标签", () => tagsDialog(result.data, source.title, ctx)));
    } catch (err) {
      if (region.isConnected) region.replaceChildren(note(err.message || "标签暂时无法读取"), btn("重新读取标签", load));
    }
  }
  load();
}

function highlight(value, terms) {
  const node = h("span");
  if (!terms.length) { node.textContent = value; return node; }
  const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).sort((a, b) => b.length - a.length);
  const pattern = new RegExp(escaped.join("|"), "giu");
  let position = 0;
  for (const match of value.matchAll(pattern)) {
    node.append(document.createTextNode(value.slice(position, match.index)), text("mark", match[0]));
    position = match.index + match[0].length;
  }
  node.append(document.createTextNode(value.slice(position)));
  return node;
}

function locationLabel(hit) {
  const loc = hit.locator;
  if (hit.kind === "summary") return "整理摘要";
  if (hit.kind === "title") return "资料标题";
  if (loc.page) return `PDF 第 ${loc.page} 页${loc.table ? `，表格 ${loc.table}` : ""}`;
  if (Number.isFinite(loc.start)) {
    const time = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
    return `录音 ${time(loc.start)}–${time(loc.end)}`;
  }
  return hit.unit_id ? "网页原文片段" : "原文位置";
}

export function renderLibrary(host, ctx) {
  const root = h("section", { class: "kb-library", "aria-label": "个人知识库" });
  host.append(root);
  let generation = 0;

  async function load() {
    const ticket = ++generation;
    root.setAttribute("aria-busy", "true");
    root.replaceChildren(note("正在检索资料…"));
    try {
      const [search, catalog, vector] = await Promise.all([
        api.librarySearch(requestFilters()),
        api.libraryTags().catch(() => ({ data: { tags: [], unavailable: true } })),
        api.libraryVectorStatus().catch(() => ({ data: { provider_configured: false, unavailable: true } })),
      ]);
      if (!root.isConnected || ticket !== generation || !ctx.isCurrent()) return;
      const data = search.data;
      root.replaceChildren();
      const tagOptions = Object.assign(Object.create(null), { "": "全部标签" });
      for (const tag of catalog.data.tags) tagOptions[tag.label] = `${tag.label}（${tag.count} 份）`;
      if (filters.tag && !Object.hasOwn(tagOptions, filters.tag)) tagOptions[filters.tag] = filters.tag;
      const activeCount = [filters.kind !== "all", filters.status !== "all", !!filters.tag, filters.scope !== "latest", !!filters.start_date, !!filters.end_date].filter(Boolean).length;
      const form = h("form", { class: "kb-search", role: "search" }, [
        h("div", { class: "kb-search-row" }, [field("查找你的资料", "q", filters.q), h("button", { type: "submit", class: "btn btn-primary", text: "搜索" })]),
        h("details", { class: "kb-filter-details", open: activeCount > 0 }, [
          text("summary", activeCount ? `筛选条件 · 已启用 ${activeCount} 项` : "筛选条件"),
          h("div", { class: "kb-filter-grid" }, [
            field("类型", "kind", filters.kind, KINDS),
            field("整理状态", "status", filters.status, { all: "全部状态", pending: "待整理", processing: "处理中", ready: "已整理", attention: "需要处理" }),
            field("标签", "tag", filters.tag, tagOptions),
            field("资料版本", "scope", filters.scope, { latest: "当前版本", history: "历史版本", all: "全部版本" }),
            field("保存日期起（上海时区）", "start_date", filters.start_date, null, "date"),
            field("保存日期止（上海时区）", "end_date", filters.end_date, null, "date"),
          ]),
          h("div", { class: "ar-actions" }, [h("button", { type: "submit", class: "btn btn-primary btn-sm", text: "应用筛选" }), btn("清除筛选", () => { clearSearchFilters(); load(); })]),
        ]),
      ]);
      form.addEventListener("submit", (event) => {
        event.preventDefault(); filters = { ...filters, ...Object.fromEntries(new FormData(form)), offset: 0 }; load();
      });
      const items = h("div", { class: `kb-items is-${layout}` });
      const viewButtons = h("div", { class: "kb-view-toggle", "aria-label": "资料显示方式" });
      for (const [key, label] of [["gallery", "画廊"], ["grid", "卡片"], ["list", "列表"]]) {
        const toggle = h("button", { type: "button", text: label, "aria-pressed": String(layout === key), onClick: () => {
          layout = key;
          items.className = `kb-items is-${key}`;
          for (const control of viewButtons.children) control.setAttribute("aria-pressed", String(control === toggle));
        } });
        viewButtons.append(toggle);
      }
      const sort = h("select", { class: "field-input kb-sort", "aria-label": "文档排序" }, [
        h("option", { value: "recent", text: "最近保存" }), h("option", { value: "oldest", text: "最早保存" }), h("option", { value: "title", text: "文档名称" }),
      ]);
      sort.value = filters.sort;
      sort.addEventListener("change", () => { filters.sort = sort.value; filters.offset = 0; load(); });
      const location = getWorkspace()?.folders.find((folder) => folder.id === filters.folder_id);
      const title = filters.starred === "true" ? "收藏" : filters.folder_id === "unsorted" ? "未分类" : location?.name || "所有文档";
      document.getElementById("arrangementTitle").textContent = title;
      document.getElementById("workspaceTitle").textContent = title;
      document.dispatchEvent(new CustomEvent("craft:location", { detail: filters }));
      root.append(form, h("div", { class: "kb-toolbar" }, [note(`共 ${data.total} 份文档`), h("div", { class: "craft-browser-controls" }, [viewButtons, sort])]));
      root.append(note(data.coverage));
      if (document.body.dataset.focusLibrarySearch) {
        delete document.body.dataset.focusLibrarySearch;
        form.querySelector("[data-library-search]").focus();
      }
      if (catalog.data.unavailable) root.append(note("标签目录暂时无法读取，可以从资料卡片选择已有标签。"));
      if (catalog.data.truncated) root.append(note("标签列表显示前 500 项，可直接在资料卡片中选择其他标签。"));
      root.append(h("details", { class: "kb-vector" }, [text("summary", "语义检索接入状态"),
        note(vector.data.unavailable ? "向量模型配置状态暂时无法读取。" : vector.data.provider_configured ? `已选择向量模型 ${vector.data.model}，分块接口已准备，索引构建待接入。` : "资料分块接口已准备，向量模型配置与索引构建待接入。") ]));
      if (!data.items.length) root.append(h("div", { class: "ar-empty" }, [text("h3", "没有符合条件的资料"), note("可以调整检索词、标签、类型或版本范围。原文需要完成提取后才能检索。") ]));
      root.append(items);
      for (const source of data.items) {
        const cover = h("button", { type: "button", class: "kb-cover", "data-kind": source.kind, "aria-label": `打开 ${source.title}`, onClick: () => ctx.openSource(source.id) }, [
          icon(source.kind === "audio" ? "mic" : source.kind === "web" ? "external" : "doc"),
          h("span", { class: "kb-paper-title", text: source.title }),
          h("span", { class: "kb-paper-excerpt", text: source.hits[0]?.quote || "正文尚未完成提取，打开文档查看处理状态。" }),
        ]);
        const card = h("article", { class: "ar-card kb-result", draggable: true }, [cover,
          h("div", { class: "ar-card-head" }, [h("h3", {}, [h("button", { type: "button", class: "kb-title-button", text: source.title, onClick: () => ctx.openSource(source.id) })]),
          text("span", source.is_history ? `历史版本 ${source.source_version}` : source.source_version ? `当前版本 ${source.source_version}` : KINDS[source.kind], "ar-tag")]),
          note(`${KINDS[source.kind]} · 保存于 ${format(source.created_at)}${source.partial ? " · 部分内容" : ""}`)]);
        card.addEventListener("dragstart", (event) => event.dataTransfer.setData("application/x-pa-source", source.id));
        const more = () => {
          const box = ctx.openDialog(source.title);
          const starred = getWorkspace()?.entries[source.root_id]?.starred ?? source.workspace?.starred ?? false;
          const marker = h("span"); box.append(marker);
          box.append(btn(starred ? "取消收藏" : "收藏文档", async () => { if (await starSource(source, !starred) && marker.isConnected && ctx.dialog.open) ctx.dialog.close(); }),
            btn("移动到文件夹", () => moveSourceDialog(source)), btn("打开文档", () => { ctx.dialog.close(); ctx.openSource(source.id); }));
        };
        card.addEventListener("contextmenu", (event) => { event.preventDefault(); more(); });
        card.append(h("button", { type: "button", class: "btn-icon kb-document-more", "aria-label": `更多操作 ${source.title}`, onClick: more }, [icon("more")]));
        if (source.workspace?.starred) card.append(h("span", { class: "kb-starred", "aria-label": "已收藏" }, [icon("star")]));
        if (source.job_stage) card.append(note(`${source.job_stage} · ${source.analysis_status === "ready" ? "已整理" : "可打开资料查看进度"}`));
        if (source.tags.length) card.append(h("div", { class: "kb-tags" }, source.tags.map((tag) => btn(tag, () => { filters.tag = tag; filters.offset = 0; load(); }))));
        for (const hit of source.hits) {
          const excerpt = h("blockquote", { class: "kb-excerpt" }, [
            document.createTextNode(hit.has_before ? "…" : ""), highlight(hit.quote, data.terms), document.createTextNode(hit.has_after ? "…" : ""),
          ]);
          card.append(excerpt, btn(`${locationLabel(hit)}${hit.matched ? "中的命中" : "摘录"}`, () => ctx.openSource(source.id, hit)));
        }
        card.append(h("div", { class: "ar-actions" }, [btn("打开资料", () => ctx.openSource(source.id)), btn("编辑标签", async () => {
          try { const metadata = await api.libraryMetadata(source.id); if (root.isConnected && ctx.isCurrent()) tagsDialog(metadata.data, source.title, ctx); }
          catch (err) { toast(err.message || "标签读取失败"); }
        })]));
        items.append(card);
      }
      const pagination = h("div", { class: "ar-actions" }, [note(data.total ? `${data.offset + 1}–${data.offset + data.items.length} / ${data.total}` : "0 / 0")]);
      if (data.offset) pagination.append(btn("上一页", () => { filters.offset = Math.max(0, data.offset - data.limit); load(); }));
      if (data.next_offset !== null) pagination.append(btn("下一页", () => { filters.offset = data.next_offset; load(); }));
      root.append(pagination);
      if (data.next_offset === null && data.offset + data.items.length < data.total) root.append(note("已到当前分页边界，缩小检索范围后可继续查找。"));
    } catch (err) {
      if (root.isConnected && ticket === generation && ctx.isCurrent()) root.replaceChildren(note(err.message || "资料检索失败"), btn("重新检索", load),
        btn("清除筛选", () => { clearSearchFilters(); load(); }));
    } finally { if (ticket === generation) root.removeAttribute("aria-busy"); }
  }
  load();
}
