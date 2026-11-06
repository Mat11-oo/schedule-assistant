"""Evidence-checked, budgeted map/reduce reading and source-bound questions."""
import json
import re
import time

from app.agent import cost_ledger, token_meter
from app.agent.harness import context
from app.agent.harness.leak import register_system_prompt, sandbox_values, scan_output
from app.agent.harness.traces import record_step
from app.arrangements.service import model_ready, validate_private_text
from app.gateway import llm_gateway as gateway
from app.data_access import current_user
from app.materials.providers import NeedsConfiguration, ProviderFailure
from app.materials.schemas import Answer, Digest, Reading
from app.prompts import load_prompt

PROMPTS = {name: load_prompt(f"material.{name}", "v1") for name in ("read", "digest", "answer")}
for name, prompt in PROMPTS.items():
    register_system_prompt(f"material.{name}", prompt)


def complete(name, payload, contract, *, original_texts=()):
    if not model_ready():
        raise NeedsConfiguration("文本模型尚未配置；已提取的原文仍可阅读和手动整理")
    if not cost_ledger.check(current_user()).ok:
        raise NeedsConfiguration("文本模型用量已达上限，请等待额度恢复或手动整理")
    for text in original_texts:
        validate_private_text(text)
    started, success, tokens = time.monotonic(), False, 0
    ctx = {"llm_calls": [], "guardrails": []}
    try:
        with context.step_context(user_id=current_user(), thread_id="materials", sandbox=sandbox_values(current_user(), strict=True)) as ctx:
            with token_meter.metered() as meter:
                try:
                    output = gateway.chat_json(PROMPTS[name] + "\nJSON Schema：\n" + json.dumps(contract.model_json_schema(), ensure_ascii=False),
                        json.dumps(payload, ensure_ascii=False), task="summary", response_model=contract,
                        timeout=60, max_tokens=10000, fallback={})
                finally:
                    tokens = meter.tokens
        if output.get("_parse") or str(output.get("_mode", "")).startswith("mock"):
            raise ProviderFailure("文本模型未返回可用结果")
        value = contract.model_validate({k: v for k, v in output.items() if not k.startswith("_")})
        guard = scan_output(value.model_dump_json(), user_id=current_user(), inputs=tuple(original_texts), strict=True)
        if guard["blocked"]:
            raise ProviderFailure("模型输出未通过隐私检查")
        success = True
        return value.model_dump()
    finally:
        cost_ledger.record_spend(current_user(), cost_ledger.cny_for_tokens(tokens))
        record_step(thread_id="materials", user_id=current_user(), intent="material", tool=f"material_{name}",
            ok=success, error="" if success else "model_failed", latency_ms=(time.monotonic() - started) * 1000,
            input_summary=f"资料片段；数量={len(original_texts)}", result_summary="结构化输出待核对" if success else "未完成",
            llm_calls=ctx["llm_calls"], guardrails=ctx["guardrails"])


def check_evidence(value, units):
    lookup = {u["id"]: u for u in units}
    def walk(node):
        if isinstance(node, dict):
            if "unit_id" in node and "quote" in node:
                unit = lookup.get(node["unit_id"])
                if not unit or node["quote"] not in unit["text"]:
                    raise ProviderFailure("模型引用不能在对应原文片段中定位")
            for item in node.values(): walk(item)
        elif isinstance(node, list):
            for item in node: walk(item)
    walk(value)
    return value


def batches(units, maximum=8000):
    group, size = [], 0
    for unit in units:
        if len(unit["text"]) > maximum:
            raise ProviderFailure("原文片段超过模型处理限制，请修订或拆分")
        if group and size + len(unit["text"]) > maximum:
            yield group; group, size = [], 0
        group.append(unit); size += len(unit["text"])
    if group: yield group


def read_batch(units, *, meeting=False, speakers=None, self_speakers=None):
    payload_units = [{"id": u["id"], "text": u["text"], "locator": {k: v for k, v in u["locator"].items()
                     if k in {"page", "start", "end", "speaker", "precision"}}} for u in units]
    result = complete("read", {"kind": "meeting" if meeting else "document", "speaker_names": speakers or {},
        "self_speakers": self_speakers or [], "units": payload_units}, Reading, original_texts=[u["text"] for u in units])
    check_evidence(result, units)
    lookup = {u["id"]: u for u in units}
    for item in result["key_data"]:
        quotes = "\n".join(ev["quote"] for ev in item["evidence"])
        if any(item[k] and item[k] not in quotes for k in ("value", "unit", "period", "scope")):
            raise ProviderFailure("关键数据的数值、单位或口径缺少逐字依据")
    for action in result["actions"]:
        assignee = action["assignee"]
        if assignee:
            in_quote = any(assignee in e["quote"] for e in action["evidence"])
            mapped = any(assignee == lookup[e["unit_id"]]["locator"].get("speaker") or
                assignee == (speakers or {}).get(lookup[e["unit_id"]]["locator"].get("speaker")) for e in action["evidence"])
            if not in_quote and not mapped:
                raise ProviderFailure("行动项负责人缺少原文或已确认发言人映射的依据")
        if action["deadline_text"] and not any(action["deadline_text"] in e["quote"] for e in action["evidence"]):
            raise ProviderFailure("行动项的截止时间缺少原文依据")
        if action["owner"] == "self":
            permitted = meeting and any(lookup[e["unit_id"]]["locator"].get("speaker") in (self_speakers or []) for e in action["evidence"])
            if not permitted:
                action["owner"] = "unknown"
    return result


def synthesize(findings, units, cached, save):
    # Hierarchical reduction bounds each request. Detailed findings and actions
    # stay available even if the overview necessarily condenses them.
    nodes = []
    for finding in findings:
        nodes.extend(finding["conclusions"] + finding["decisions"] + finding["risks"] + finding["open_questions"])
    if not nodes:
        return []
    level = 0
    while True:
        groups, group, size = [], [], 0
        for node in nodes:
            n = len(json.dumps(node, ensure_ascii=False))
            if group and size + n > 16000:
                groups.append(group); group, size = [], 0
            group.append(node); size += n
        if group: groups.append(group)
        output = []
        for index, group in enumerate(groups):
            key = f"digest:{level}:{index}"
            value = cached.get(key)
            if value is None:
                value = complete("digest", {"findings": group}, Digest,
                    original_texts=[e["quote"] for item in group for e in item["evidence"]])
                check_evidence(value, units)
                save(key, value)
            output.extend(value["overview"])
        if len(groups) == 1:
            return output
        if level >= 8 or len(output) >= len(nodes):
            raise ProviderFailure("总览过长且无法继续收敛，详细分段结果已保存")
        nodes, level = output, level + 1


def question(question, units):
    validate_private_text(question)
    terms = set(re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2}", question.lower()))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", question))
    terms.update(chinese[i:i + 2] for i in range(len(chinese) - 1))
    ranked = sorted(units, key=lambda u: sum(min(u["text"].lower().count(t), 3) for t in terms), reverse=True)
    selected, size = [], 0
    for unit in ranked:
        if size + len(unit["text"]) > 16000:
            continue
        selected.append(unit); size += len(unit["text"])
        if len(selected) >= 8: break
    if not selected:
        raise ValueError("资料尚无可供追问的文本")
    value = complete("answer", {"question": question, "units": [{"id": u["id"], "text": u["text"]} for u in selected]},
        Answer, original_texts=[question] + [u["text"] for u in selected])
    check_evidence(value, selected)
    if not value["evidence"]:
        value["answer"] = "本次检索到的原文中未找到足够依据。"
    return {**value, "question": question, "searched_units": len(units), "context_units": len(selected),
            "scope": "依据相关片段回答；检索可能遗漏跨段信息，请结合原文核对"}
