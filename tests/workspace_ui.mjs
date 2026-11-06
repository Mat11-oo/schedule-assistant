// Real local API + isolated test identity. Synthetic QA errors never reach a model.
// Run against a disposable server: node --experimental-vm-modules tests/workspace_ui.mjs http://localhost:8793
import assert from "node:assert/strict";
import { api, base, failures, mount, transport, until } from "./arrangements_ui.mjs";

const questions = [];
let pendingWorkspace = null;
const dom = await mount({ hash: "#home", onRequest: async (url, options) => {
  if (url.endsWith("/questions")) {
    questions.push(JSON.parse(options.body));
    return new Response(JSON.stringify({ detail: { message: "Synthetic retryable failure" } }), { status: 503 });
  }
  if (url.endsWith("/library/workspace") && options.method === "POST" && pendingWorkspace) {
    const pending = pendingWorkspace; pendingWorkspace = null;
    await pending;
    return transport(url, options);
  }
  return null;
} });
const w = dom.window, d = w.document;
const body = () => d.getElementById("arrangementBody");
const dialog = () => d.getElementById("arrangementDialog");
const mainNav = (view) => d.querySelector(`.ws-nav [data-arrangement-view="${view}"]`).click();
const input = (name, value, host = dialog()) => {
  const field = host.querySelector(`[name="${name}"]`);
  assert.ok(field, `Field ${name}`); field.value = value;
  field.dispatchEvent(new w.Event("input", { bubbles: true }));
};
const click = (label, host = d) => {
  const control = [...host.querySelectorAll("button")].find((item) => item.textContent === label);
  assert.ok(control && !control.disabled, `Enabled control ${label}`); control.click();
};
const submit = (form) => form.dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
async function module(name) {
  const entry = [...dom.frontendModules.entries()].find(([url]) => new URL(url).pathname.endsWith(`/${name}.js`));
  assert.ok(entry, name); return (await entry[1]).namespace;
}
async function change(action, values = {}) {
  const current = await api("/api/arrangements/library/workspace");
  return api("/api/arrangements/library/workspace", { method: "POST", json: { action, ...values, version: current.version, confirmed: true } });
}

try {
  await until(() => d.querySelector('#craftFolders [data-folder-id="unsorted"]') && body().querySelector(".craft-home-search"), "initial home");
  mainNav("tasks");
  await until(() => body().querySelector(".craft-task-tabs"), "tasks loaded");
  assert.equal(d.getElementById("craftBack").disabled, false, "Initial home must remain in navigation history");
  d.getElementById("craftBack").click();
  await until(() => d.body.dataset.craftView === "home", "back uses browser history");
  assert.equal(d.getElementById("craftForward").disabled, false);
  d.getElementById("craftForward").click();
  await until(() => d.body.dataset.craftView === "tasks", "forward uses browser history");
  console.log("PASS initial home, back and forward history");

  d.querySelector('.workspace-sidebar [data-workspace-action="collect"]').click();
  input("title", "交互回归资料"); input("content", "我负责整理会议记录。合成输入，仅用于隔离验证。");
  dialog().close();
  d.querySelector('.workspace-sidebar [data-workspace-action="collect"]').click();
  assert.equal(dialog().querySelector('[name="title"]').value, "交互回归资料");
  assert.match(dialog().querySelector('[name="content"]').value, /合成输入/);
  submit(dialog().querySelector("form"));
  await until(() => !dialog().open && body().querySelector(".ar-source-text"), "saved document");
  const sourceId = w.location.hash.slice(1).split("?")[0].split("/")[1];
  assert.match(sourceId, /^[a-f0-9]{32}$/);
  click("返回资料列表", body());
  await until(() => body().querySelector(".kb-library"), "document back to library");
  assert.equal(d.body.classList.contains("craft-document-open"), false);
  assert.ok(!w.location.hash.includes(sourceId));
  console.log("PASS draft recovery, save and consistent return to library");

  d.getElementById("craftAddFolder").click();
  input("name", "研究"); submit(dialog().querySelector("form"));
  await until(() => !dialog().open && d.querySelector('#craftFolders [data-folder-id]:not([data-folder-id="unsorted"])'), "folder saved");
  const state = await api("/api/arrangements/library/workspace");
  const parent = state.folders.find((folder) => folder.name === "研究");
  await change("move_source", { source_id: sourceId, folder_id: parent.id });
  const second = (await change("create_folder", { name: "生活" })).folders.find((folder) => folder.name === "生活");
  const craft = await module("craft"); await craft.loadWorkspace();
  d.querySelector(`#craftFolders [data-folder-id="${parent.id}"]`).click();
  await until(() => body().querySelector(".kb-library") && d.getElementById("arrangementTitle").textContent === "研究", "folder view");
  assert.ok(w.location.hash.includes(parent.id));
  input("q", "不存在的词", body()); submit(body().querySelector("form"));
  await until(() => body().textContent.includes("没有符合条件的资料"), "filtered folder");
  click("清除筛选", body());
  await until(() => body().querySelector(".kb-result"), "clear within folder");
  assert.equal(d.getElementById("arrangementTitle").textContent, "研究");
  assert.ok(w.location.hash.includes(parent.id));
  d.querySelector(`#craftFolders [data-folder-id="${second.id}"]`).click();
  await until(() => d.getElementById("arrangementTitle").textContent === "生活", "second folder");
  d.getElementById("craftBack").click();
  await until(() => d.getElementById("arrangementTitle").textContent === "研究" && body().querySelector(".kb-result"), "back restores folder");
  console.log("PASS folder persistence, scoped filtering and navigation restoration");

  craft.folderDialog(parent);
  await change("create_folder", { name: "另一个窗口新增" });
  await craft.loadWorkspace();
  input("name", "不应静默覆盖"); submit(dialog().querySelector("form"));
  await until(() => dialog().textContent.includes("其他页面更新"), "stale form conflict");
  const unchanged = await api("/api/arrangements/library/workspace");
  assert.equal(unchanged.folders.find((folder) => folder.id === parent.id).name, "研究");
  assert.ok(dialog().textContent.includes("重新读取后再编辑"));
  dialog().close();
  console.log("PASS stale open forms cannot overwrite newer workspace state");

  let release;
  pendingWorkspace = new Promise((resolve) => { release = resolve; });
  craft.folderDialog(); input("name", "延迟保存目录"); submit(dialog().querySelector("form"));
  await until(() => pendingWorkspace === null, "delayed request started");
  dialog().close();
  d.querySelector('.workspace-sidebar [data-workspace-action="collect"]').click();
  input("title", "保留新窗口"); release();
  await until(() => d.getElementById("craftFolders").textContent.includes("延迟保存目录"), "delayed save completed");
  assert.equal(dialog().open, true);
  assert.equal(dialog().querySelector('[name="title"]').value, "保留新窗口");
  dialog().close();
  console.log("PASS delayed save leaves a newly opened dialog intact");

  const candidate = await api(`/api/arrangements/sources/${sourceId}/candidates`, { method: "POST", json: {
    title: "仅存参考的合成事项", quote: "我负责整理会议记录。", owner: "self", deadline_text: "",
  } });
  d.dispatchEvent(new w.CustomEvent("craft:navigate", { detail: "inbox" }));
  await until(() => body().textContent.includes("仅存参考的合成事项"), "pending candidate");
  click("核对并处理", body());
  assert.equal([...dialog().querySelectorAll("button")].find((item) => item.textContent === "确认关联").disabled, true);
  input("title", ""); click("仅保存参考", dialog());
  await until(() => !dialog().open, "reference ignores unrelated invalid task fields");
  const original = await api(`/api/arrangements/sources/${sourceId}`);
  assert.equal(original.candidates.find((item) => item.id === candidate.id).status, "reference");
  assert.equal((await api("/api/arrangements/tasks")).length, 0);
  console.log("PASS reference decisions do not require task fields, empty link action disabled");

  await api(`/api/arrangements/sources/${sourceId}/candidates`, { method: "POST", json: {
    title: "确认后完成的合成任务", quote: "我负责整理会议记录。", owner: "self", deadline_text: "",
  } });
  d.dispatchEvent(new w.CustomEvent("craft:navigate", { detail: "inbox" }));
  await until(() => body().textContent.includes("确认后完成的合成任务"), "confirmable candidate");
  click("核对并处理", body()); click("确认创建任务", dialog());
  await until(() => !dialog().open, "task confirmed");
  mainNav("tasks");
  await until(() => body().querySelector('.craft-task-row input[type="checkbox"]'), "confirmed task listed");
  body().querySelector('.craft-task-row input[type="checkbox"]').click();
  await until(() => body().querySelector(".craft-task-empty"), "completed task leaves unfinished scope");
  body().querySelector('.craft-task-tabs [aria-label="全部任务"]').click();
  await until(() => body().querySelector('.craft-task-row input[type="checkbox"]')?.checked, "completed task retained in all scope");
  assert.equal((await api("/api/arrangements/tasks"))[0].status, "done");
  console.log("PASS source → candidate → confirmation → task completion → retained completed record");

  const materials = await module("materials");
  const fixture = { id: "f".repeat(32), title: "Synthetic QA", content: "synthetic text", candidates: [], material: {
    kind: "web", url: "https://example.test/reference", version: 1, selected_version: 1,
    current_source_id: "f".repeat(32), jobs: [], artifacts: [], speakers: {}, coverage: {},
    units: [{ id: "unit-1", ordinal: 0, text: "synthetic text", locator: {}, start_char: 0, end_char: 14 }],
  } };
  const host = d.createElement("section"); body().append(host);
  function renderQuestion() {
    host.replaceChildren();
    return materials.renderMaterial({ source: fixture, container: host, dialog: dialog(), status: { model_ready: false }, onReload() {}, onOpen() {} }).qa;
  }
  let qa = renderQuestion();
  const ask = async (value, count) => {
    input("question", value, qa); qa.querySelector('[name="allow_cloud"]').checked = true;
    submit(qa.querySelector("form"));
    await until(() => questions.length === count && qa.textContent.includes("Synthetic retryable failure"), "synthetic QA failure");
  };
  await ask("问题 A", 1);
  await ask("问题 A", 2);
  assert.equal(questions[0].request_id, questions[1].request_id);
  await ask("问题 B", 3);
  assert.notEqual(questions[1].request_id, questions[2].request_id);
  qa = renderQuestion();
  assert.equal(qa.querySelector("textarea").value, "问题 B");
  assert.equal(qa.querySelector('[name="allow_cloud"]').checked, false, "Cloud consent must remain explicit");
  console.log("PASS QA drafts and idempotency, using synthetic failures without a model call");
  assert.deepEqual(failures, []);
  console.log("PASS workspace DOM integration with isolated API/storage");
} finally { dom.window.close(); }
