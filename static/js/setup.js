import { h, toast } from "./core.js?v=__APP_VERSION__";
import { api } from "./api.js?v=__APP_VERSION__";
import { currentUserId } from "./identity.js?v=__APP_VERSION__";

const text = (tag, value, cls = "") => h(tag, { text: value, class: cls });
const note = (value) => text("p", value, "ar-note");
const btn = (label, action, primary = false) => h("button", { type: "button", text: label, onClick: action, class: `btn btn-${primary ? "primary" : "ghost"} btn-sm` });
const actions = (...nodes) => h("div", { class: "ar-actions" }, nodes);
const format = (value) => value ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "待安排";
const CONFIG_LABELS = { configured: "配置已填写", incomplete: "待补齐配置", review: "需要核对" };

function card(title, state, details, controls = []) {
  return h("article", { class: "ar-card setup-card" }, [h("div", { class: "ar-card-head" }, [text("h3", title), text("span", state, "ar-tag")]),
    ...details.map(note), controls.length ? actions(...controls) : null]);
}

export function renderStartHint(host, ctx) {
  const key = "pa.start-guide.seen.v1:" + currentUserId();
  try { if (localStorage.getItem(key) === "1") return; } catch { /* The current page can still dismiss the hint. */ }
  const region = h("aside", { class: "setup-hint", "aria-label": "开始使用" }, [text("strong", "从一条信息开始安排"),
    note("开始指南会带你完成保存资料、确认任务、设置时段和排期。"), actions(btn("打开开始指南", () => ctx.navigate("setup"), true), btn("已了解", () => {
      try { localStorage.setItem(key, "1"); } catch { /* Browser storage may be disabled. */ }
      region.remove();
    }))]);
  host.append(region);
}

export function renderSetup(host, data, ctx) {
  renderModelSettings(host);
  host.append(note(`运行版本 ${data.version} · 状态更新于 ${format(data.generated_at)}`));
  const guide = h("section", { class: "setup-guide", "aria-label": "首次使用指南" }, [text("h3", "开始使用")]);
  const steps = [
    { title: "保存一条信息", complete: data.progress.source_saved, description: "收集一条消息或粘贴已有文字，保存后从原文手动添加事项。", label: "收集信息", action: ctx.collect },
    { title: "核对并确认任务", complete: data.progress.task_confirmed, description: "在待处理核对归属、截止时间和耗时，再确认创建或关联任务。", label: "打开待处理", action: () => ctx.navigate("inbox") },
    { title: "保存可用时段", complete: data.progress.work_hours_saved, description: "设置工作时段、休息间隔与每天任务容量，并登记已有固定安排。", label: "设置可用时段", action: () => ctx.openToday("work_hours") },
    { title: "确认一份排期", complete: data.progress.plan_saved, description: "选择任务，核对安排预览和未安排原因，确认后保存到今日时间线。", label: "生成排期建议", action: () => ctx.openToday("plan") },
  ];
  for (const [index, step] of steps.entries()) guide.append(card(`${index + 1}. ${step.title}`, step.complete ? "已保存过" : "可以开始",
    [step.description], [btn(step.label, step.action, !step.complete)]));
  guide.append(card("5. 查看每日简报", "可选设置", ["简报汇总今日安排和需要决定的事项，可选择每日生成时间与内容。"],
    [btn("查看今日简报", () => ctx.navigate("today")), btn("简报设置", () => ctx.openToday("brief"))]));
  host.append(guide);

  const services = h("section", { class: "setup-services", "aria-label": "服务配置" }, [text("h3", "服务配置"),
    note("这里显示运行进程的配置快照。保存模型设置后重新进入本页可更新状态。实际服务响应需联调核对。")]);
  const models = data.services.text;
  const modelDetails = models.roles.map((role) => `${role.label} · ${role.configured ? "配置已填写" : "待补齐"} · ${role.models.map((m) => `${m.model} @ ${m.host || "地址待填写"}`).join(" → ") || "模型待选择"}`);
  services.append(card("文字理解与资料整理", CONFIG_LABELS[models.status], modelDetails));
  for (const [key, title] of [["ocr", "扫描页识别"], ["asr", "录音转写"]]) {
    const service = data.services[key];
    services.append(card(title, CONFIG_LABELS[service.status], [service.model || "模型待填写", service.host || "服务地址待填写"]));
  }
  services.append(card("语义检索", "接入准备中", [data.services.vector.model ? `已选择模型 ${data.services.vector.model}` : "向量模型待选择",
    "资料分块接口已准备，向量索引构建与语义查询待接入。"]));
  const command = text("pre", data.configure_command, "ar-source-text");
  services.append(text("h4", "终端配置入口"), note("最后配置服务密钥时，在本机终端运行下面的命令，按隐藏输入提示填写。完成后重新启动应用。"), command,
    btn("复制配置命令", async () => {
      try { await navigator.clipboard.writeText(data.configure_command); toast("配置命令已复制"); }
      catch { toast("请手动选择并复制上方命令"); }
    }));
  host.append(services);

  const scheduler = data.background.reminders;
  const materials = data.background.materials;
  const backgroundLabel = (enabled, alive) => alive ? "运行中" : enabled ? "待启动" : "待开启";
  host.append(h("section", { class: "setup-runtime", "aria-label": "后台与个人设置" }, [text("h3", "后台与个人设置"),
    card("站内提醒调度", backgroundLabel(scheduler.proactive_enabled, scheduler.scheduler_alive), ["已保存的任务提醒和每日简报由应用运行期间的后台调度处理。"]),
    card("资料后台处理", backgroundLabel(materials.enabled, materials.alive), ["上传资料的提取、转写和整理会保存处理进度。"]),
    card("工作时段", data.personal.work_hours_configured ? "已设置" : "待设置", ["排期使用已确认的可用时段和每天容量。"], [btn("管理工作时段", () => ctx.openToday("work_hours"))]),
    card("每日简报", data.personal.brief_enabled ? "已开启站内推送" : "可选开启", [
      `每天设定时间 ${data.personal.brief_at}，上海时区`,
      data.personal.brief_enabled ? `下一次记录时间 ${format(data.personal.brief_next_run_at)}` : "可在今日直接阅读简报，也可以开启每日站内推送。",
    ], [btn("管理简报", () => ctx.openToday("brief"))]),
    card("日历连接", "应用内安排", ["工作时段与时间块保存在应用内，可从今日导出日历文件。外部日历自动同步待接入。"], [btn("查看今日安排", () => ctx.navigate("today"))]),
  ]));
  host.append(h("details", { class: "setup-storage" }, [text("summary", "本机运行与数据保存"),
    note(`数据存储 ${data.storage.backend}`), text("pre", data.storage.data_dir, "ar-source-text"),
    note("双击项目中的启动入口打开应用，停止入口会关闭本项目服务。关闭网页后，后台服务继续运行。"),
    note("备份时先停止应用，再保存数据目录和项目 .env 文件。恢复时使用同一份本机配置以继续访问原会话数据。"),
  ]));
}

function renderModelSettings(host) {
  const region = h("section", { class: "setup-services", "aria-label": "模型与 API Key" }, [text("h3", "模型与 API Key"), note("正在读取配置…")]);
  host.append(region);
  const owner = currentUserId();
  const active = () => region.isConnected && owner === currentUserId();
  async function load() {
    try {
      const response = await api.modelSettings();
      if (!active()) return;
      const state = response.data;
      region.replaceChildren(text("h3", "模型与 API Key"), note("配置用于本机应用的所有会话。密钥保存到本机 .env 文件，只有当前系统用户可读写。留空保留原密钥，未使用的服务可将模型和地址均留空。保存后对后续请求生效。"));
      if (!state.editable) { region.append(note("已有高级模型路由，请在本机配置文件中管理。")); return; }
      const form = h("form", { class: "ar-form" });
      const fields = {};
      for (const [slot, label] of [["fast", "文字理解与对话"], ["strong", "复杂任务"], ["ocr", "扫描件识别"], ["asr", "录音转写"]]) {
        const current = state.slots[slot];
        const presets = slot === "asr" ? [
          ["百炼", "https://dashscope.aliyuncs.com/api/v1", "qwen-audio-3.1-asr-flash", "dashscope"],
        ] : slot === "ocr" ? [
          ["百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3-vl-plus", "openai"],
        ] : [
          ["百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus", "openai"],
          ["DeepSeek", "https://api.deepseek.com/v1", "deepseek-flash", "openai"],
          ["Kimi", "https://api.moonshot.cn/v1", "kimi-k3", "openai"],
        ];
        const input = (name, value, type = "text") => h("input", { class: "field-input", name: `${slot}_${name}`, type, value,  autocomplete: "off", "aria-label": `${label} ${name}` });
        const base = input("服务地址", current.base_url);
        const model = input("模型", current.model);
        const key = input("API Key", "", "password");
        key.placeholder = current.has_key ? "已填写，留空保留" : "输入 API Key";
        const protocol = h("select", { class: "field-input", "aria-label": `${label} 协议` }, [h("option", { value: "openai", text: "OpenAI 兼容协议" }), ...(slot === "asr" ? [h("option", { value: "dashscope", text: "百炼录音转写协议" })] : [])]);
        protocol.value = current.protocol;
        const provider = h("select", { class: "field-input", "aria-label": `${label} 服务商` }, [h("option", { value: "", text: "当前配置／自定义" }), ...presets.map(([name], index) => h("option", { value: String(index), text: name }))]);
        const choices = h("datalist", { id: `modelOptions_${slot}` }, presets.map(([, , value]) => h("option", { value })));
        model.setAttribute("list", choices.id);
        provider.addEventListener("change", () => {
          if (provider.value === "") return;
          const [, url, value, mode] = presets[Number(provider.value)];
          base.value = url; model.value = value; protocol.value = mode; key.value = "";
        });
        fields[slot] = { base, model, key, protocol };
        form.append(h("fieldset", { class: "ar-form model-config-group" }, [h("legend", { text: label }), provider,
          h("label", {}, [text("span", "模型名称，可选择或手动填写"), model]), choices,
          h("label", {}, [text("span", "服务地址"), base]), protocol,
          h("label", {}, [text("span", "API Key"), key])]));
      }
      const error = h("p", { class: "ar-error", role: "alert" });
      const save = h("button", { type: "submit", class: "btn btn-primary", text: "保存模型配置" });
      form.append(error, actions(save, btn("重新读取配置", load)));
      form.addEventListener("submit", async (event) => {
        event.preventDefault(); error.textContent = "";
        const slots = Object.fromEntries(Object.entries(fields).map(([slot, f]) => [slot, { base_url: f.base.value.trim(), model: f.model.value.trim(), api_key: f.key.value.trim(), protocol: f.protocol.value }]));
        const controls = [...form.querySelectorAll("input, select, button")];
        controls.forEach((control) => { control.disabled = true; });
        try {
          await api.saveModelSettings({ version: state.version, slots });
          Object.values(fields).forEach((f) => { f.key.value = ""; });
          if (active()) { toast("模型配置已保存，后续请求使用新配置"); await load(); }
        } catch (err) {
          if (active()) error.textContent = err.message || "保存失败，请重试";
        } finally { controls.forEach((control) => { control.disabled = false; }); }
      });
      region.append(note("选择预设后可修改模型和地址。录音转写需选择匹配的协议。保存仅检查配置格式，密钥权限和服务可用性需实际调用验证。"), form);
    } catch (err) {
      if (active()) region.replaceChildren(text("h3", "模型与 API Key"), note(err.message || "读取失败"), btn("重新读取", load));
    }
  }
  load();
}
