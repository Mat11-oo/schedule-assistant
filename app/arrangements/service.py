"""Model-assisted extraction; unavailable models leave an honest manual workflow."""
import json
import time

from fastapi import HTTPException

from app.arrangements.schemas import Extraction
from app.agent import cost_ledger, token_meter
from app.agent.harness import context as trace_context
from app.agent.harness.traces import record_step
from app.data_access import arrangements as store, current_user
from app.gateway import llm_gateway as gateway
from app.privacy import redact_sensitive
from app.prompts import load_prompt
from app.agent.harness.leak import register_system_prompt, sandbox_values, scan_output

PROMPT = load_prompt("arrangement.extract", "v1")
register_system_prompt("arrangement.extract", PROMPT)


def validate_private_text(*values):
    try:
        hidden = sandbox_values(current_user(), strict=True)
    except Exception:
        raise HTTPException(503, detail={"code": "privacy_unavailable", "message": "隐私检查暂不可用，请稍后再试"}) from None
    for value in values:
        if isinstance(value, str) and (redact_sensitive(value)[1] or any(v in value for v in hidden)):
            raise ValueError("内容含密码、Cookie 或联系方式等敏感信息，请移除后再保存；敏感凭据请使用安全输入区")


def model_ready():
    return gateway.get_llm_override() is not None or gateway.has_llm(task="summary")


def analyze(source_id):
    source = store.source_detail(source_id)
    if source.get("material"):
        raise HTTPException(422, detail={"message": "文件资料请使用分段速读／纪要入口，以保留完整覆盖和来源定位"})
    if not model_ready():
        raise HTTPException(503, detail={"code": "model_unavailable", "message": "尚未配置模型，可手动选取原文添加待确认事项"})
    if not cost_ledger.check(current_user()).ok:
        raise HTTPException(429, detail={"code": "cost_cap", "message": "模型用量已达上限；原文已保存，可手动整理或等待额度恢复"})
    if not store.claim_analysis(source_id):
        return store.source_detail(source_id)
    started, succeeded = time.monotonic(), False
    context = {"llm_calls": [], "guardrails": []}
    meter_tokens = 0
    try:
        validate_private_text(source["title"], source["content"])
        with trace_context.step_context(user_id=current_user(), thread_id=f"arrangement:{source_id}",
                                         sandbox=sandbox_values(current_user(), strict=True)) as context:
            with token_meter.metered() as meter:
                try:
                    output = gateway.chat_json(PROMPT + "\nJSON Schema：\n" + json.dumps(Extraction.model_json_schema(), ensure_ascii=False),
                        json.dumps({"title": source["title"], "kind": source["kind"], "original_at": source["original_at"],
                                    "tz": source["tz"], "content": source["content"]}, ensure_ascii=False),
                        task="summary", response_model=Extraction, timeout=45, max_tokens=5000, fallback={})
                finally:
                    meter_tokens = meter.tokens
        if output.get("_parse") or str(output.get("_mode", "")).startswith("mock"):
            raise ValueError("模型未返回有效结果")
        result = Extraction.model_validate({k: v for k, v in output.items() if not k.startswith("_")})
        validate_private_text(result.model_dump_json())
        checked = scan_output(result.model_dump_json(), user_id=current_user(),
                              inputs=(source["content"],), strict=True)
        if checked["blocked"]:
            raise ValueError("输出检查未通过")
        store.finish_analysis(source_id, result)
        succeeded = True
    except Exception:
        store.finish_analysis(source_id)
        raise HTTPException(502, detail={"code": "analysis_failed", "message": "整理未完成或引用检查未通过；原文已保存，可重试或手动整理"}) from None
    finally:
        cost_ledger.record_spend(current_user(), cost_ledger.cny_for_tokens(meter_tokens))
        record_step(thread_id=f"arrangement:{source_id}", user_id=current_user(), tool="arrangement_extract",
            intent="arrangement", ok=succeeded, latency_ms=(time.monotonic() - started) * 1000,
            input_summary=f"文本整理；字符数={len(source['content'])}",
            result_summary="已生成待确认事项" if succeeded else "整理未完成", error="" if succeeded else "analysis_failed",
            llm_calls=context["llm_calls"], guardrails=context["guardrails"])
    return store.source_detail(source_id)
