import { h } from "./core.js?v=__APP_VERSION__";
import { api } from "./api.js?v=__APP_VERSION__";
import { renderMarkdown } from "./markdown.js?v=__APP_VERSION__";
import { currentUserId } from "./identity.js?v=__APP_VERSION__";

export function documentAssistant(source) {
  const owner = currentUserId();
  const dialog = h("dialog", { class: "document-assistant", "aria-label": "文档助手" });
  const close = h("button", { type: "button", class: "btn btn-ghost", text: "×", "aria-label": "关闭文档助手", onClick: () => dialog.close() });
  const messages = h("div", { class: "document-assistant-messages", role: "log", "aria-live": "polite" });
  const question = h("textarea", { class: "field-input", placeholder: "围绕这份文档提问…", "aria-label": "文档问题", maxLength: 2000, required: true });
  const send = h("button", { type: "submit", class: "btn btn-primary", text: "执行" });
  const suggestions = h("div", { class: "document-assistant-suggestions" }, [h("h3", { text: "建议" })]);
  for (const label of ["添加摘要", "提升清晰度", "改进拼写和语法", "翻译成英文", "提取行动项与负责人", "指出风险和待确认的问题"]) {
    suggestions.append(h("button", { type: "button", class: "btn btn-ghost", text: `✧  ${label}`, onClick: () => { question.value = label; question.focus(); } }));
  }
  const status = h("p", { role: "status", class: "ar-note" });
  const form = h("form", { class: "document-assistant-composer" }, [
    h("div", { class: "document-context-chip", text: `▤  ${source.title}\n当前文档` }), question,
    h("p", { class: "ar-note", text: "执行会将问题与最多24,000字正文发送给已配置的模型服务。结果为建议稿。" }), status, send,
  ]);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (send.disabled || !question.value.trim()) return;
    const value = question.value.trim();
    send.disabled = true; question.disabled = true; status.textContent = "正在处理…";
    try {
      const result = await api.documentAssistant(source.id, { question: value });
      if (owner !== currentUserId()) { dialog.close(); return; }
      messages.append(h("p", { class: "document-assistant-question", text: value }));
      const response = h("div", { class: "document-prose" });
      response.innerHTML = renderMarkdown(result.data.answer);
      messages.append(response); suggestions.hidden = true;
      status.textContent = result.data.partial ? "仅使用正文前24,000字，结果覆盖不完整。" : "建议已生成，原文保持保存状态。";
      question.value = ""; response.scrollIntoView({ block: "nearest" });
    } catch (err) { status.textContent = err.message || "处理失败，请重试"; }
    finally { send.disabled = false; question.disabled = false; }
  });
  dialog.append(h("header", {}, [h("strong", { text: "新聊天" }), close]), suggestions, messages, form);
  dialog.addEventListener("close", () => { dialog.remove(); if (trigger.isConnected) trigger.focus({ preventScroll: true }); });
  const trigger = h("button", { type: "button", class: "btn btn-primary document-assistant-trigger", text: "", "aria-label": "打开文档助手", title: "文档助手", onClick: () => {
    if (!dialog.isConnected) document.body.append(dialog);
    dialog.showModal(); question.focus({ preventScroll: true });
  } });
  trigger.append(h("span", { class: "document-assistant-orb", "aria-hidden": "true" }));
  return trigger;
}
