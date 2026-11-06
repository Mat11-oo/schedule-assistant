"""A leased, recoverable worker; successful pages/chunks survive later failures."""
import copy
import json
import os
import tempfile
import threading
from pathlib import Path

from app import db
from app.data_access import user_scope, current_user
from app.data_access.arrangements import Conflict
from app.data_access import materials as store
from app.materials import intelligence, parsers, providers
from app.agent.harness.leak import sandbox_values
from app.privacy import redact_sensitive

_thread = None
_stop = threading.Event()
_lock = threading.Lock()


def root_dir():
    path = db.DATA_DIR / "materials"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stored_path(key):
    if not key or Path(key).name != key or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in key):
        raise ValueError("文件记录无效")
    return root_dir() / key


def clean(value):
    hidden = sandbox_values(current_user(), strict=True) + [os.getenv(k, "") for k in ("ASR_API_KEY", "OCR_API_KEY", "DASHSCOPE_API_KEY")]
    def redact(item):
        if isinstance(item, str):
            item = redact_sensitive(item)[0]
            for secret in hidden:
                if secret: item = item.replace(secret, "[隐私已隐藏]")
            return item
        if isinstance(item, list): return [redact(i) for i in item]
        if isinstance(item, dict): return {k: redact(v) for k, v in item.items()}
        return item
    return redact(value)


class JobRun:
    def __init__(self, job):
        self.job, self.id, self.token = job, job["id"], job["claim_token"]
        self.saved = store.parts(self.id)
        self.calls = 0
    def save(self, key, value):
        store.save_part(self.id, self.token, key, value)
        self.saved[key] = value
    def progress(self, **fields):
        store.progress(self.id, self.token, **fields)
    def cloud(self, key, prefix, fn):
        cache_key = "cloud:" + key
        existing = self.saved.get(cache_key, {})
        if existing.get("state") == "done": return existing["result"]
        if not self.job["allow_cloud"]:
            raise providers.NeedsConsent("需要你允许配置的云服务处理该资料，才能继续 OCR、转写或模型整理")
        if prefix and not providers.configured(prefix):
            raise providers.NeedsConfiguration(f"{prefix} 云服务尚未配置；文件与已完成片段已保留")
        if existing.get("state") in {"started", "uncertain"}:
            raise providers.UncertainRequest("上次云端调用结果不明，请核对后确认重试")
        if self.calls >= int(os.getenv("MATERIAL_CLOUD_CALL_LIMIT", "40")):
            raise providers.BatchLimit("本轮云处理批次上限已到，已完成部分保留；确认继续后只处理剩余部分")
        self.calls += 1
        self.save(cache_key, {"state": "started"})
        try:
            value = fn()
        except (providers.NeedsConfiguration, providers.ProviderFailure):
            self.save(cache_key, {"state": "failed"}); raise
        except Exception:
            self.save(cache_key, {"state": "uncertain"}); raise
        self.save(cache_key, {"state": "done", "result": value})
        return value


def _issue(exc):
    if isinstance(exc, providers.BatchLimit): return "partial", "batch_limit", str(exc)
    if isinstance(exc, providers.NeedsConfiguration): return "awaiting_configuration", "configuration_missing", str(exc)
    if isinstance(exc, providers.NeedsConsent): return "awaiting_consent", "cloud_consent", str(exc)
    if isinstance(exc, providers.UncertainRequest): return "awaiting_review", "cloud_uncertain", str(exc)
    if isinstance(exc, providers.ProviderFailure): return "failed", "processing_failed", clean(str(exc))
    if isinstance(exc, ValueError):
        message = str(exc)
        safe = clean(message) if message and "\u4e00" <= message[0] <= "\u9fff" and len(message) < 300 else "处理结果格式无效，请核对文件或服务配置后重试"
        return "failed", "processing_failed", safe
    return "failed", "processing_failed", "处理失败，文件与已完成片段已保存，可稍后重试"


def parse_pdf(run, asset, temporary):
    path = stored_path(asset["storage_key"])
    units, ready, issues = [], [], []
    total = 0
    for page in parsers.pdf_pages(path):
        number, total = page["page"], page["total"]
        key = f"page:{number}"
        run.progress(stage=f"读取第 {number} / {total} 页", total=total, done=len(ready))
        cached = run.saved.get(key)
        if cached and cached.get("complete"):
            units.extend(cached["units"]); ready.append(number); continue
        page = clean(page)
        text, tables, warnings = page["text"], page["tables"], []
        complete, error = True, None
        if page["need_ocr"] and not page["blank"]:
            try:
                image = parsers.render_page(path, number, Path(temporary) / f"page-{number}.png")
                value = run.cloud(f"ocr:{number}", "OCR", lambda: clean(providers.ocr(image, f"{run.id}:page:{number}")))
                # OCR sees the full page, including native text and image labels.
                text, tables, warnings = value["text"], value["tables"], value["uncertain"]
            except Exception as exc:
                if isinstance(exc, Conflict): raise
                complete, error = False, _issue(exc)
                warnings.append(error[2])
        locator = {"page": number, "method": "ocr" if page["need_ocr"] and complete else "native", "uncertain": warnings}
        pieces = parsers.split_text(text, locator)
        for index, table in enumerate(tables):
            table_text = "\n".join("\t".join(row) for row in table["rows"])
            if len(table_text) > 50000:
                complete, error = False, ("failed", "table_too_large", "单页表格过大，请拆分文件")
                continue
            table_pieces = parsers.split_text(table_text, {**locator, "table": index + 1, "rows": table["rows"]})
            for extra in table_pieces[1:]: extra["locator"].pop("rows", None)
            pieces.extend(table_pieces)
        run.save(key, {"complete": complete, "units": pieces, "warnings": warnings})
        units.extend(pieces)
        if complete: ready.append(number)
        elif error: issues.append({"page": number, "state": error[0], "code": error[1], "message": error[2]})
        if sum(len(u["text"]) for u in units) > int(os.getenv("MATERIAL_MAX_EXTRACTED_CHARS", "1200000")):
            raise ValueError("提取文字超过当前限制，请拆分文件")
        if error and error[0] == "awaiting_review":
            break  # Never issue more paid requests after an uncertain one.
    coverage = {"total_pages": total, "completed_pages": ready, "issues": issues,
                "note": "扫描及图像文字由 OCR 提取，请核对原图；联系方式与凭据会在索引文本中隐藏"}
    if not units and not issues:
        raise ValueError("PDF 没有可读文字或表格")
    if issues:
        priority = next((i for i in issues if i["state"] == "awaiting_review"), issues[0])
        state = priority["state"] if priority["state"].startswith("awaiting_") else "partial"
        return store.publish_parse(run.id, run.token, units, coverage, state=state, message=priority["message"], error_code=priority["code"])
    return store.publish_parse(run.id, run.token, units, coverage)


def parse_audio(run, asset, temporary):
    units, warnings, completed = [], [], []
    duration = 0
    try:
        for chunk in parsers.audio_chunks(stored_path(asset["storage_key"]), temporary):
            index = chunk["index"]
            run.progress(stage=f"转写录音第 {index} 块", done=len(completed), total=index)
            value = run.cloud(f"asr:{index}", "ASR", lambda: clean(providers.transcribe(chunk["path"], chunk["duration"],
                chunk["offset"], index, f"{run.id}:audio:{index}")))
            for unit in value["units"]:
                units.extend(parsers.split_text(unit["text"], unit["locator"]))
            warnings.extend(value["warnings"])
            completed.append(index)
            duration = chunk["offset"] + chunk["duration"]
            chunk["path"].unlink(missing_ok=True)
        if len(completed) > 1:
            warnings.append("长录音已分块处理；不同块的发言人标记不能直接视为同一人，分块边界请核对")
        coverage = {"completed_chunks": completed, "duration_seconds": duration, "warnings": list(dict.fromkeys(warnings)),
                    "speaker_labels": bool(units) and all(u["locator"].get("speaker") != "unknown" for u in units)}
        limited = not coverage["speaker_labels"] or any(u["locator"].get("precision") != "segment" for u in units)
        return store.publish_parse(run.id, run.token, units, coverage, state="partial" if limited else "completed",
            message="；".join(dict.fromkeys(warnings)))
    except Exception as exc:
        if isinstance(exc, Conflict): raise
        state, code, message = _issue(exc)
        return store.publish_parse(run.id, run.token, units, {"completed_chunks": completed,
            "duration_seconds": duration, "warnings": warnings + [message], "incomplete": True},
            state=state if state.startswith("awaiting_") else "partial" if units else "failed", message=message, error_code=code)


def analyze(run, source_id):
    from app.data_access.arrangements import source_detail
    source = source_detail(source_id)
    material, units = source["material"], source["material"]["units"]
    groups = list(intelligence.batches(units))
    findings, issue, calls = [], None, 0
    limit = int(os.getenv("MATERIAL_ANALYSIS_BATCH_LIMIT", "40"))
    if not intelligence.model_ready():
        raise providers.NeedsConfiguration("文本模型尚未配置；原文已提取，可阅读或手动整理")
    for index, group in enumerate(groups):
        run.progress(stage=f"提炼第 {index + 1} / {len(groups)} 组", total=len(groups), done=len(findings))
        key = f"reading:{index}"
        if key in run.saved:
            findings.append(run.saved[key]); continue
        if calls >= limit:
            issue = ("partial", "batch_limit", "本次模型批次上限已到，已完成部分保留；可确认后继续剩余部分")
            break
        try:
            value = run.cloud(key, None, lambda: intelligence.read_batch(group, meeting=material["kind"] == "audio",
                speakers=material["speakers"], self_speakers=material["self_speakers"]))
            run.save(key, value); findings.append(value); calls += 1
        except Exception as exc:
            if isinstance(exc, Conflict): raise
            issue = _issue(exc); break
    overview = []
    if findings:
        if not issue:
            try:
                # Digest substeps are cached too; the full findings remain intact.
                def save(key, value): run.save(key, value)
                overview = intelligence.synthesize(findings, units, run.saved, save)
            except Exception as exc:
                issue = _issue(exc)
        if issue:
            overview = [item for f in findings for item in f["conclusions"] + f["decisions"]][:12]
        store.publish_analysis(run.id, run.token, findings, overview,
            {"total_batches": len(groups), "completed_batches": len(findings), "source_partial": material["partial"],
             "note": "详细发现按片段保留；总览经过综合，需核对跨段上下文"}, partial=bool(issue) or material["partial"])
        if issue:
            # publish_analysis ends the lease, so a follow-up transition uses the
            # dedicated terminal-state setter under the same user identity.
            store.mark_issue(run.id, issue[0], issue[1], issue[2], run.token)
    elif issue:
        run.progress(state=issue[0], error_code=issue[1], message=issue[2])


def run_one(root_id=None):
    db.init_db()
    job = store.claim_next(root_id)
    if not job: return False
    with user_scope(job["worker_user_id"]):
        run = JobRun(job)
        pulse_stop = threading.Event()
        def pulse():
            with user_scope(job["worker_user_id"]):
                while not pulse_stop.wait(10):
                    try: run.progress()
                    except Exception: return
        heartbeat = threading.Thread(target=pulse, daemon=True); heartbeat.start()
        try:
            asset = store.get_asset(job["source_id"])
            with tempfile.TemporaryDirectory(prefix="material-", dir=root_dir()) as temporary:
                if job["kind"] == "analyze":
                    analyze(run, job["source_id"])
                elif asset["kind"] in {"text", "docx"}:
                    run.progress(stage="提取文档正文", total=1)
                    path = stored_path(asset["storage_key"])
                    value = clean(parsers.text_document(path, Path(asset["filename"]).suffix.lower().lstrip(".")))
                    result_source = store.publish_parse(run.id, run.token, value, {"format": asset["kind"], "images_extracted": False})
                    if job["allow_cloud"]:
                        store.enqueue(result_source, "analyze", True)
                elif asset["kind"] == "pdf":
                    result_source = parse_pdf(run, asset, temporary)
                    if job["allow_cloud"] and store.get_units(result_source):
                        store.enqueue(result_source, "analyze", True)
                elif asset["kind"] == "audio":
                    result_source = parse_audio(run, asset, temporary)
                    if job["allow_cloud"] and store.get_units(result_source):
                        store.enqueue(result_source, "analyze", True)
                else:
                    run.progress(stage="读取公开网页", total=1)
                    value = run.saved.get("web")
                    if value is None:
                        value = clean(parsers.fetch_web(asset["url"], current_user())); run.save("web", value)
                    result_source = store.publish_parse(run.id, run.token, value, {"url": asset["url"], "snapshot": True})
                    if job["allow_cloud"]:
                        store.enqueue(result_source, "analyze", True)
        except Conflict:
            pass  # Cancelled/deleted/reclaimed work cannot publish a late result.
        except Exception as exc:
            state, code, message = _issue(exc)
            try: run.progress(state=state, error_code=code, message=message)
            except Conflict: pass
        finally:
            pulse_stop.set(); heartbeat.join(timeout=1)
    return True


def _loop():
    while not _stop.is_set():
        try:
            if not run_one(): _stop.wait(1)
        except Exception:
            _stop.wait(2)


def start():
    global _thread
    if os.getenv("MATERIAL_WORKER_ENABLED", "1").lower() not in {"1", "true", "yes"}: return
    with _lock:
        if _thread and _thread.is_alive(): return
        _stop.clear(); _thread = threading.Thread(target=_loop, daemon=True, name="material-worker"); _thread.start()


def stop():
    _stop.set()
    if _thread: _thread.join(timeout=1)


def status():
    return {"enabled": os.getenv("MATERIAL_WORKER_ENABLED", "1").lower() in {"1", "true", "yes"},
            "alive": bool(_thread and _thread.is_alive() and not _stop.is_set())}
