# 长文档速读与会议纪要：云服务接入

更新：2026-11-05。文件流程、云接口适配、版本、引用与任务衔接已实现。本机尚未配置 OCR、ASR 和真实文本模型；云服务实际效果与真实资料质量仍待联调。

## 1. 现在可以做什么

| 能力 | 当前实现 | 本轮验证边界 |
|---|---|---|
| PDF | 上传、逐页文字及表格提取、图像页 OCR 接口、分页预览、覆盖说明 | 真实解析与渲染通过；云 OCR 使用测试服务 |
| 网页／研报 | 公开网页读取、正文快照；PDF 研报走 PDF 流程 | 快照流程与原有 SSRF 防护已测试；动态、登录页面需粘贴正文 |
| 长文速读 | 按片段提炼结论、风险、关键数据与口径、行动项；分层生成总览 | 模型返回结构与引用校验通过；真实模型质量未测 |
| 会议 | 音频上传、解码分块、云转写、服务提供的时间戳与发言人、纪要与任务 | 解码、接口契约、版本和任务衔接已测试；真实转写质量未测 |
| 来源回查 | PDF 页码、表格、录音时间点、逐字引文；点击定位到片段与原图／音频 | 连接真实本地服务的 DOM 流程通过；真实浏览器视觉未验收 |
| 纠错 | 修订文字、映射发言人、明确哪些发言人是自己，生成新版本 | 旧版本与已确认任务的引用保持不变 |
| 追问 | 检索当前版本相关片段，生成带引用的答案并保存问答 | 检索与引用契约通过；实际回答质量未测 |
| 恢复与删除 | 成功片段缓存、失败重试、停止后续处理、进程中断保护、清理来源及派生内容 | API、文件、版本、任务保留和异常流程已测试 |

“转写服务提供发言人标签”与“知道标签对应谁”是两步。系统只保留服务返回的分组，姓名与本人身份由用户明确映射。跨音频分块的同名标签不能自动认定为同一人。

## 2. 在本机配置服务

### 推荐方案：阿里云百炼，北京地域

本项目已适配以下组合：`qwen-plus` 负责文字理解与纪要，`qwen3-vl-plus` 负责扫描页识别，`qwen-audio-3.1-asr-flash` 负责录音转写、逐句时间戳与发言人分组。三者可共用本机的一份 `DASHSCOPE_API_KEY`，但必须具有对应模型的权限。

1. 按[官方指引](https://help.aliyun.com/zh/model-studio/get-api-key)开通百炼并创建**北京地域** API Key。Key、业务空间及模型地址保持同地域。
2. 在本机终端进入项目目录后运行：

   ```bash
   .venv/bin/python scripts/configure_bailian.py
   ```

   在隐藏输入中粘贴 Key；脚本原子更新 `.env`，权限设为 `600`，保留登录密钥与数据路径，不发起云端调用。不要把 Key 写进命令参数或发到聊天。若有独立模型密钥、JSON 配置或路由覆盖，脚本会停止，避免生成含义不清的混合配置。
3. 运行配置检查：

   ```bash
   .venv/bin/python scripts/configure_bailian.py --check
   ```

   “本机配置齐全”只说明字段及路由齐全，不代表 Key 有效、模型已授权或真实处理成功。若 shell 已有同名环境变量，它们优先于 `.env`；需要先核对冲突。
4. 重启应用，然后对一份代表性 PDF 和一段短录音明确允许云处理，验收引用、数据、负责人、时间戳及账单。

没有 Key 时可先运行 `--prepare`，只保存模型与地址，保留空密钥状态。脚本默认使用北京公共域名；可添加 `--workspace 你的业务空间ID` 使用官方推荐的业务空间专属域名，后续每次运行均保持同一选项。

脚本配置的等效核心字段如下（密钥留空）：

```dotenv
DASHSCOPE_API_KEY=
LLM_FAST_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_FAST_MODEL=qwen-plus
LLM_FAST_PARAMS={"enable_thinking":false}
LLM_STRONG_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_STRONG_MODEL=qwen-plus
LLM_STRONG_PARAMS={"enable_thinking":false}
OCR_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OCR_MODEL=qwen3-vl-plus
OCR_ENABLE_THINKING=false
ASR_PROVIDER=dashscope
ASR_BASE_URL=https://dashscope.aliyuncs.com/api/v1
ASR_MODEL=qwen-audio-3.1-asr-flash
```

文字复用既有网关；OCR 使用兼容接口与 JSON 输出；语音使用百炼**原生 HTTP 接口**并显式设置 `speaker_diarization_enabled=true`，直接发送本机音频分块的 Base64，不需要另配 OSS 或公开录音 URL。语音时间戳由毫秒换算为录音时间，编号 `0` 也是有效发言人；跨块编号不自动合并身份。服务只返回全文而没有逐句结果时，会明确降为分块定位；返回不完整或矛盾的逐句结果会停止供核对。

OCR／ASR 的独立 `*_API_KEY` 仍优先；仅在已核验的百炼 HTTPS 域名允许使用共享 Key，防止误把共享密钥发送给其他服务。原生语音不使用 `ASR_RESPONSE_FORMAT` 和 `ASR_LANGUAGE`。网络中断及服务端异常仍要求先核对服务记录，再明确重试，不自动重发录音。

实现依据：[结构化输出与非思考模式](https://help.aliyun.com/zh/model-studio/qwen-structured-output)、[Qwen-Audio-ASR 原生 HTTP 接口](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-http-api)。实际识别质量与费用尚未实测，不以接口适配代替真实验收。

### 其他服务商：保留兼容接口方式

编辑项目根目录 `.env`，保留现有 `AUTH_SECRET`、`DATA_DIR` 等配置，只补充需要的字段。密钥留在本机，不应发到聊天或提交到仓库。

```dotenv
# 文本模型：用于速读、会议纪要、总览与资料问答。
# 也可沿用 README 中已有的 KIMI_API_KEY 等配置。
LLM_FAST_BASE_URL=你的文本服务的兼容接口地址
LLM_FAST_MODEL=你有权限使用的文本模型
LLM_FAST_API_KEY=在本机填写

# OCR：需要支持图片输入及 JSON 输出的 /chat/completions。
OCR_BASE_URL=你的图片模型服务的兼容接口地址
OCR_MODEL=支持图片和JSON输出的模型
OCR_API_KEY=在本机填写

# ASR：需要支持文件转写的 /audio/transcriptions。
# 完整的发言人工作流需要服务返回 speaker、start、end 和 text。
ASR_PROVIDER=openai
ASR_BASE_URL=你的转写服务的兼容接口地址
ASR_MODEL=支持说话人分段与时间戳的转写模型
ASR_API_KEY=在本机填写
ASR_RESPONSE_FORMAT=diarized_json
ASR_LANGUAGE=

MATERIAL_WORKER_ENABLED=1
```

这里的中文是待替换说明，不是可用凭据或真实模型名。不要直接以这些占位值启动联调。

上述方式使用 OpenAI 兼容协议，不预设服务商或模型名称。文本兼容接口不代表同时提供图片理解或音频转写。图片以 Base64 数据 URL 发送；转写参数及输出格式按模型能力选择。当前协议依据：[图像输入说明](https://developers.openai.com/api/docs/guides/images-vision?api-mode=chat)、[转写接口](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)。

如选择 OpenAI 服务，请先核对当前模型能力与停用说明；不同转写模型支持的格式、时间戳和说话人字段不相同，代码没有写入一个默认转写模型。见 [官方文件转写说明](https://developers.openai.com/api/docs/guides/speech-to-text)。

`ASR_RESPONSE_FORMAT` 可选 `diarized_json`、`verbose_json`、`json`。服务不返回说话人或逐句时间时，会明确标为部分能力／分块定位，不伪造逐句时间戳或发言人。

配置后重启应用，页面的服务状态才会反映新配置：

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8787 --env-file .env
```

依赖已加入 `requirements.txt` 与锁定文件。其他机器安装命令：

```bash
pip install -r requirements.txt -c requirements.lock
```

现有本地 SQLite 数据无需改表：本阶段使用独立的新表。Postgres 需通过原有 owner migration 创建新表与 RLS 后再启用工作进程；本轮未连接真实 Postgres。

## 3. 使用流程

打开 `http://127.0.0.1:8787/#sources`，点「上传文件」或「读取网页」。

1. 上传 PDF 或录音；可填写原始时间。未知时间留空，不用上传当天代替来源时间。
2. 选择是否允许已配置的云服务处理该资料。未允许时仍保存文件、提取 PDF 原生文字，扫描页和录音等待许可。
3. 允许云处理后，提取成功的资料会继续排队生成速读／纪要；也可在详情中手动发起。
4. 从处理状态查看完成范围、失败部分和待配置能力。成功的页、录音块和模型批次会缓存，重试复用已有结果。
5. 点引用定位页码或录音时间。数字保留单位、时间和口径；原文风险与 Agent 分析分开展示。
6. 会议中先映射发言人与本人身份，再按需生成新版本纪要。未映射的发言人不能自动视为当前用户。
7. 从关联事项进入现有确认流程。只有明确采纳才创建个人任务；其他事项可关联已有任务、保存参考或忽略。

模型总览是压缩后的综合结果，详细分段发现仍保留。资料缺页、OCR 不确定、发言人缺失或模型仅处理部分批次时，页面会标记对应范围；不能把部分结果当全文结论。

## 4. 修订、追问和删除

修订原文或转写、修改发言人映射会生成新的来源版本。旧纪要、问答、候选项与已确认任务继续指向原版本，不会随修订静默改变；新版本需重新生成整理结果并核对差异。

追问依据当前版本的相关片段，回答附逐字引文；无足够依据时说明未找到。当前使用关键词／中文相邻字匹配检索，每次最多选 8 段、16,000 字作为模型上下文，因此可能遗漏跨段材料；不能把局部追问当成全文穷尽检索。

「删除资料」的范围包括：原始文件、预览、全部修订版本文字、转写、结构化报告、问答、缓存云响应和引用摘录。已确认任务、状态和个人任务内容保留，出处标记为已删除；派生任务历史中的来源摘录同时清理。删除前页面会展示这个具体范围并要求明确确认。

删除或停止不能撤回已发出的云端请求，也不能代替服务商端的数据保留设置。删除失败时先关闭资料访问、标记文件清理待完成，重试删除可继续清理，不重复执行已经完成的步骤。

## 5. 后台处理与费用边界

- 每份资料在 SQLite／Postgres 中保存任务、阶段、进度和成功片段；关闭页面不停止后台工作，关闭服务会停止本机工作进程。
- 工作进程用心跳和一次性租约标记防止过期进程写回。检测到进程中断，不自动重发可能已经计费的云端请求。
- 云 OCR／ASR 客户端关闭自动重试。超时或服务端异常导致结果不明时，显示“需核对云端请求”，须明确确认后才可重试。
- 云服务可能收费。文本模型沿用现有估算额度；OCR／ASR 由服务商计费，当前未实现统一金额估算，不能把文本额度当作所有云费用的硬上限。另有单轮云调用次数和资料大小限制。
- 一次处理达到批次上限时保留已完成结果；用户确认继续后处理剩余内容。服务额度／权限错误会停止对应处理，不提供占位输出。

默认限制（可由本机 `.env` 调整）：

| 配置 | 默认值 |
|---|---|
| `MATERIAL_MAX_PDF_MB` | 50 MB |
| `MATERIAL_MAX_PAGES` | 300 页 |
| `MATERIAL_MAX_AUDIO_MB` | 200 MB |
| `MATERIAL_MAX_AUDIO_SECONDS` | 7200 秒 |
| `MATERIAL_MAX_EXTRACTED_CHARS` | 1,200,000 字符 |
| `MATERIAL_MAX_QUEUED_JOBS` | 每用户 20 个排队或运行任务 |
| `MATERIAL_CLOUD_CALL_LIMIT` | 一轮至多 40 次 OCR／ASR／分段阅读调用 |
| `MATERIAL_ANALYSIS_BATCH_LIMIT` | 一轮至多 40 个新增阅读批次 |

总览综合会另有文本模型调用，受现有文本额度检查约束，不计入 OCR／ASR 调用计数。实际费用以服务商账单为准。

音频本机解码为单声道 16 kHz WAV，按最长 300 秒分块后发送；分块边界及跨块发言人身份需核对。首版尚未实现实时录音、会议平台自动拉取和日历关联，也未把这些输入伪装为已接入。

## 6. API 与维护位置

前缀均为 `/api/arrangements`，身份来自既有服务端会话。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/uploads` | multipart 文件上传，立即保存并排队 |
| POST | `/web-sources` | 保存公开网页来源并排队 |
| POST | `/sources/{id}/reading` | 对选定版本分段阅读／生成纪要 |
| POST | `/jobs/{id}/retry` | 明确许可及必要的结果不明确认后继续 |
| POST | `/jobs/{id}/cancel` | 停止本地后续处理 |
| POST | `/sources/{id}/revisions` | 按当前版本号确认修订，创建新版本 |
| POST | `/sources/{id}/questions` | 基于当前版本的有引用问答 |
| GET | `/sources/{id}/file` | 鉴权后读取原附件；音频支持 Range |
| GET | `/sources/{id}/pages/{n}` | 鉴权后渲染 PDF 页面 PNG |
| DELETE | `/sources/{id}/material` | 确认范围后删除资料与派生内容 |

主要维护源：`app/materials/`（处理与接口）、`app/data_access/materials.py`（私有持久化）、`app/prompts/material.*/`（版本化提示词）、`static/js/materials.js`（页面）、`tests/test_materials.py`（服务契约与真实文件测试）。

## 7. 本轮验证结果

所有样本均为合成资料；本轮没有向真实付费 OCR／ASR／文本服务发送文件。

| 检查 | 结果 |
|---|---|
| 新增媒体测试 | 18 项通过：PDF 文字／表格／渲染、音频解码、真实 SDK 对本地测试 HTTP 服务、引用及负责人依据、修订、隔离、删除、租约和异常流程 |
| 百炼适配及本机配置 | 21 项通过：原生 HTTP 契约、毫秒时间戳、发言人 0、缺失／矛盾分段与时间范围、两种转写协议的错误不重试、OCR 非思考模式、共享 Key 域名限制、原子保存与既有配置保留；均为本地合成响应 |
| 全量回归检查点 | 1736 通过、128 跳过、1 排除；排除为既有归档目录没有 `.git` 的提交号测试 |
| 最终定向回归 | 84 项通过，覆盖百炼、文件流程与文字网关，包含全量检查点之后补充的 4 项兼容协议异常用例 |
| Mock 金标 | 42/42 通过；不代表真实模型效果 |
| 完整前端 DOM → 真实本地服务 | 上传 → PDF 后台解析 → 原图接口 → 修订新版本 → 原版本保留 → 删除通过；未配置 OCR 时明确等待配置 |
| 真实 Chrome | 已在新标签打开本地页面，但当前工具无法读取页面内容或有效截图，未完成视觉和点击验收 |
| PDF 渲染人工检查 | 合成页面 PNG 已检查，文字可见、无裁切；不代表复杂研报版面质量 |
| 真实云服务与真实资料 | 待用户本机配置服务并提供有权处理的代表性资料后验收 |

复现：

```bash
.venv/bin/python -m pytest -q tests/test_materials.py
.venv/bin/python -m pytest -q tests/test_bailian.py
.venv/bin/python -m pytest -q tests/test_bailian.py tests/test_materials.py tests/test_gateway.py
.venv/bin/python -m pytest -q -k 'not test_manifest_records_run_config'
MATERIAL_WORKER_ENABLED=0 AUTH_SECRET=regression-test-secret .venv/bin/python tests/golden/run_golden.py --mode mock --judge-gate
node --experimental-vm-modules tests/arrangements_ui.mjs http://127.0.0.1:8787 --materials
```

DOM 测试需要此前安装的 `data/ui-qa/node_modules/jsdom`、运行中的本地服务、未配置真实模型与 OCR、已启用提醒和材料工作进程。文件选择器、原生对话框、滚动与尺寸观察为模拟；所有 HTTP、文件解析、数据库保存及后台调度均为真实本地实现。

真实验收仍需：一份有扫描页／表格的代表性长文档、一段有多人发言和实际决议的会议录音。重点记录漏项、错误数字、建议误判为决议、负责人错误、引用定位、纠错操作量、处理时延与实际费用。
