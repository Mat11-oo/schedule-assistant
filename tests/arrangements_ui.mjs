// DOM integration against a real local API. This does not verify browser layout.
// Install the optional test dependency in ignored data/: npm install --prefix data/ui-qa jsdom
// Run: node --experimental-vm-modules tests/arrangements_ui.mjs http://127.0.0.1:8787
import assert from "node:assert/strict";
import vm from "node:vm";
import { createRequire } from "node:module";
import { setTimeout as delay } from "node:timers/promises";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { JSDOM, CookieJar, VirtualConsole } = require("../data/ui-qa/node_modules/jsdom");
export const base = process.argv[2] || "http://127.0.0.1:8787";
assert.match(base, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/);
const jar = new CookieJar(); // New test-only account, never adopt the user's browser cookies.
export const failures = [];

export async function transport(path, options = {}) {
  const url = new URL(path, base).href;
  const headers = new Headers(options.headers);
  const cookie = await jar.getCookieString(url);
  if (cookie) headers.set("Cookie", cookie);
  const response = await fetch(url, { ...options, headers });
  for (const item of response.headers.getSetCookie()) await jar.setCookie(item, url);
  return response;
}

export async function api(path, options = {}) {
  const response = await transport(path, { ...options, headers: { "Content-Type": "application/json" },
    body: options.json ? JSON.stringify(options.json) : undefined });
  assert.equal(response.status, 200, `${path}: ${await response.clone().text()}`);
  return (await response.json()).data;
}

export async function until(check, label, timeout = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await check()) return;
    await delay(40);
  }
  throw new Error(`Timed out: ${label}`);
}

export async function mount({ hash = "#today", onRequest = null, mobile = false } = {}) {
  const html = await (await transport("/")).text();
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (err) => failures.push(err.message));
  const dom = new JSDOM(html, { url: `${base}/${hash}`, runScripts: "outside-only", cookieJar: jar, virtualConsole, pretendToBeVisual: true });
  const w = dom.window;
  w.fetch = async (path, options = {}) => {
    const intercepted = await onRequest?.(String(path), options);
    if (intercepted) return intercepted;
    let body = options.body;
    if (body instanceof w.FormData) {
      const native = new FormData();
      for (const [key, value] of body.entries()) {
        if (typeof value === "string") native.append(key, value);
        else {
          const bytes = await new Promise((resolve, reject) => {
            const reader = new w.FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsArrayBuffer(value);
          });
          native.append(key, new Blob([bytes], { type: value.type }), value.name);
        }
      }
      body = native;
    }
    return transport(path, { ...options, body });
  };
  w.AbortController = globalThis.AbortController;
  w.AbortSignal = globalThis.AbortSignal;
  // Only native dialog display/scroll are simulated; production rendering and form code runs unchanged.
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event("close")); };
  w.HTMLElement.prototype.scrollIntoView = function () {};
  w.HTMLElement.prototype.scrollTo = function (options) { this.scrollTop = options?.top || 0; };
  w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.matchMedia = (query) => ({ matches: mobile && query.includes("max-width"), addEventListener() {}, removeEventListener() {} });
  const context = dom.getInternalVMContext();
  const modules = new Map();
  function moduleAt(url) {
    if (!modules.has(url)) modules.set(url, (async () => {
      const script = await (await transport(url)).text();
      return new vm.SourceTextModule(script, { identifier: url, context });
    })());
    return modules.get(url);
  }
  const entry = await moduleAt(`${base}/static/js/app.js`);
  await entry.link((specifier, parent) => moduleAt(new URL(specifier, parent.identifier).href));
  await entry.evaluate();
  dom.frontendModules = modules;
  return dom;
}

if (path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
let dom;
try {
  dom = await mount();
  let d = dom.window.document;
  function click(label, root = d) {
    const buttons = [...root.querySelectorAll("button")].filter((b) => b.textContent === label);
    assert.equal(buttons.length, 1, `Unique button: ${label}`);
    assert.ok(!buttons[0].disabled, `Enabled button: ${label}`);
    buttons[0].click();
  }
  function fill(name, value) {
    const el = d.querySelector(`#arrangementDialog [name="${name}"]`);
    assert.ok(el, name);
    el.value = value;
    el.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  }
  const bodyText = () => d.getElementById("arrangementBody").textContent;
  await until(() => bodyText().includes("这里还没有任务"), "load empty today");
  assert.match(d.getElementById("arrangementStatus").textContent, /模型尚未配置/);
  click("收集信息");
  fill("title", "【合成验收】评审资料");
  fill("kind", "document_text");
  fill("content", "我负责整理一页提纲。\n访谈只是建议。");
  click("保存资料");
  await until(() => bodyText().includes("我负责整理一页提纲。"), "source persisted and rendered");
  click("手动添加事项");
  fill("title", "【合成验收】整理提纲");
  fill("owner", "self");
  fill("quote", "我负责整理一页提纲。");
  click("添加到待处理");
  await until(() => bodyText().includes("核对并处理"), "candidate persisted and rendered");
  click("核对并处理");
  fill("estimated_minutes", "45");
  click("确认创建任务");
  await until(() => bodyText().includes("没有待确认的事项"), "confirmed candidate disappears");
  click("今日");
  await until(() => d.querySelector("[data-task-id]"), "task rendered");
  assert.match(bodyText(), /45 分钟/);
  const taskId = d.querySelector("[data-task-id]").dataset.taskId;
  click("查看出处");
  await until(() => d.querySelector(".ar-source-text mark"), "source highlight");
  assert.equal(d.querySelector(".ar-source-text mark").textContent, "我负责整理一页提纲。");
  console.log("PASS UI input → source → candidate → confirmed task → original evidence");

  const status = await api("/api/arrangements/status");
  assert.ok(status.scheduler_enabled && status.scheduler_alive, "Run local server with PROACTIVE_ENABLED=1 for the delivery check");
  let tasks = await api("/api/arrangements/tasks");
  let task = tasks.find((t) => t.id === taskId);
  const patch = Object.fromEntries(["title", "due_at", "estimated_minutes", "priority", "note", "status", "version"].map((k) => [k, task[k]]));
  task = await api(`/api/arrangements/tasks/${taskId}`, { method: "PATCH", json: { ...patch, confirmed: true,
    reminder_at: new Date(Date.now() + 2500).toISOString() } });
  const scheduleId = task.reminder_schedule_id;
  await until(async () => (await api("/api/proactive/pending")).items.some((i) => i.payload.schedule_id === scheduleId), "real scheduler delivers station inbox", 25000);
  assert.equal((await api(`/api/arrangements/tasks/${taskId}`)).task.status, "todo");
  click("今日");
  await until(() => bodyText().includes("个人任务提醒"), "real reminder rendered");
  console.log("PASS live scheduler → saved inbox reminder → DOM; task stays unfinished");

  click("查看／调整");
  fill("status", "done");
  click("确认保存修改");
  await until(() => bodyText().includes("这里还没有任务"), "finished task removed from active list");
  click("已结束");
  await until(() => d.querySelector("[data-task-id]"), "finished task available");
  assert.match(bodyText(), /已完成/);
  assert.equal((await api(`/api/arrangements/tasks/${taskId}`)).task.reminder_schedule_id, null);
  dom.window.close();

  dom = await mount(); d = dom.window.document;
  await until(() => bodyText().includes("这里还没有任务"), "reload");
  click("已结束");
  await until(() => bodyText().includes("【合成验收】整理提纲"), "server state survives a new DOM session");
  assert.equal((await api("/api/arrangements/tasks")).filter((t) => t.id === taskId).length, 1);
  assert.equal((await api(`/api/arrangements/tasks/${taskId}`)).changes.length, 3);
  console.log("PASS UI complete task → reminder cancelled → history → reload recovery");
  if (process.argv.includes("--materials")) {
    const sourceText = "SYNTHETIC VALIDATION MATERIAL. Prepare the review outline before Friday. This is a test, not a real project.";
    function pdf(scanned = false) {
      const stream = scanned ? "q 500 0 0 600 50 100 cm /Im0 Do Q" : `BT /F1 10 Tf 30 740 Td (${sourceText}) Tj ET`;
      const objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> /XObject << /Im0 6 0 R >> >> /Contents 5 0 R >>`,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
        "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /ASCIIHexDecode /Length 7 >>\nstream\nffffff>\nendstream"];
      let result = "%PDF-1.4\n", offsets = [0];
      objects.forEach((object, index) => { offsets.push(Buffer.byteLength(result)); result += `${index + 1} 0 obj\n${object}\nendobj\n`; });
      const xref = Buffer.byteLength(result);
      result += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n` + offsets.slice(1).map((n) => `${String(n).padStart(10, "0")} 00000 n \n`).join("");
      result += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
      return Buffer.from(result);
    }
    async function uploadThroughDOM(name, content, title, cloud = false) {
      click("上传文件");
      const form = d.querySelector("#arrangementDialog form");
      const file = form.querySelector('[name="file"]');
      Object.defineProperty(file, "files", { value: [new dom.window.File([content], name, { type: name.endsWith(".pdf") ? "application/pdf" : "audio/wav" })], configurable: true });
      form.querySelector('[name="title"]').value = title;
      form.querySelector('[name="allow_cloud"]').checked = cloud;
      // Native file picker selection is simulated; the production submit handler
      // and FormData are unchanged and sent to the actual HTTP upload endpoint.
      form.dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
      await until(() => !d.getElementById("arrangementDialog").open && bodyText().includes(title), "upload renders saved material");
    }
    await uploadThroughDOM("synthetic-report.pdf", pdf(), "【合成验收】PDF 原文");
    await until(() => d.querySelector("[data-unit-id]"), "worker publishes PDF text", 15000);
    assert.match(bodyText(), /SYNTHETIC VALIDATION MATERIAL/);
    assert.match(bodyText(), /页面覆盖：1 \/ 1/);
    let sources = await api("/api/arrangements/sources");
    const originalSourceId = sources.find((s) => s.title === "【合成验收】PDF 原文").id;
    const pageImage = await transport(`/api/arrangements/sources/${originalSourceId}/pages/1`);
    assert.equal(pageImage.status, 200); assert.equal(pageImage.headers.get("content-type"), "image/png");
    fs.mkdirSync("data/qa-materials", { recursive: true });
    fs.writeFileSync("data/qa-materials/synthetic-page.png", Buffer.from(await pageImage.arrayBuffer()));
    click("修订转写／原文");
    const revisionForm = d.querySelector("#arrangementDialog form");
    revisionForm.querySelector("textarea").value = "CORRECTED SYNTHETIC TEXT. The review outline was updated.";
    click("确认保存为新版本");
    await until(() => bodyText().includes("版本 2") && bodyText().includes("CORRECTED SYNTHETIC TEXT"), "versioned correction persisted");
    assert.match((await api(`/api/arrangements/sources/${originalSourceId}`)).content, /SYNTHETIC VALIDATION MATERIAL/);
    click("删除资料");
    d.querySelector('#arrangementDialog input[type="checkbox"]').checked = true;
    click("确认删除资料");
    await until(() => !d.getElementById("arrangementDialog").open && !bodyText().includes("【合成验收】PDF 原文"), "material deletion removes all versions from source list");
    assert.equal((await transport(`/api/arrangements/sources/${originalSourceId}/file`)).status, 404);
    console.log("PASS DOM file upload → actual PDF worker → page preview → correction/new version → original retained → deletion");

    await uploadThroughDOM("synthetic-scan.pdf", pdf(true), "【合成验收】扫描页待配置", true);
    await until(() => bodyText().includes("等待服务配置"), "missing OCR configuration stays explicit");
    assert.match(bodyText(), /OCR 云服务尚未配置/);
    assert.ok(!bodyText().includes("已完成模型整理"));
    console.log("PASS missing OCR configuration retains the uploaded scan without fabricated results");
  }
  assert.deepEqual(failures, [], "No DOM execution errors");
  console.log("PASS DOM integration (real API/storage, synthetic input; native browser layout NOT checked)");
} catch (err) {
  console.error("DOM failure state:", dom?.window.document.getElementById("arrangementBody")?.textContent,
    dom?.window.document.getElementById("arrangementDialog")?.textContent, failures);
  throw err;
} finally {
  dom?.window.close();
}

}
