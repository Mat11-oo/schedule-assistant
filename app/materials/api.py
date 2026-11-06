"""Authenticated uploads, durable jobs, versioned reading and evidence preview."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.arrangements.api import call
from app.arrangements.service import validate_private_text
from app.data_access import current_user
from app.data_access import materials as store
from app.materials import intelligence, parsers, providers, worker
from app.materials.schemas import DeleteInput, QuestionInput, RevisionInput, RunInput, WebInput

router = APIRouter(prefix="/api/arrangements", tags=["materials"])
MIMES = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "md": "text/markdown", "markdown": "text/markdown", "txt": "text/plain", "csv": "text/csv", "json": "application/json",
         "pdf": "application/pdf", "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
         "mp4": "audio/mp4", "webm": "audio/webm", "ogg": "audio/ogg", "flac": "audio/flac"}


@router.post("/uploads")
async def upload(file: UploadFile = File(...), title: str = Form(""), allow_cloud: bool = Form(False), original_at: str = Form("")):
    current_user()
    filename = re.sub(r"[\x00-\x1f\x7f]", "", Path(file.filename or "upload").name)[:200]
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in MIMES:
        raise HTTPException(422, detail={"message": "请选择 PDF、DOCX、MD、TXT、CSV、JSON 或支持的录音文件"})
    name = title.strip() or filename
    if not 1 <= len(name) <= 200:
        raise HTTPException(422, detail={"message": "标题须为 1 至 200 字"})
    call(validate_private_text, name)
    if original_at:
        try:
            original = datetime.fromisoformat(original_at)
            if original.tzinfo is None or original.utcoffset() is None: raise ValueError()
            original_at = original.isoformat()
        except ValueError:
            raise HTTPException(422, detail={"message": "原始时间格式无效，需包含时区；未知可留空"}) from None
    kind = extension if extension in {"pdf", "docx"} else "text" if extension in {"md", "markdown", "txt", "csv", "json"} else "audio"
    limit = int(os.getenv("MATERIAL_MAX_PDF_MB" if kind == "pdf" else "MATERIAL_MAX_AUDIO_MB", "50" if kind == "pdf" else "200")) * 1024 * 1024
    if kind in {"text", "docx"}:
        limit = 10 * 1024 * 1024
    key = uuid.uuid4().hex + "." + extension
    target = worker.stored_path(key)
    digest, size, first = hashlib.sha256(), 0, b""
    committed = False
    try:
        with open(target, "xb") as stream:
            os.chmod(target, 0o600)
            while block := await file.read(1024 * 1024):
                if not first: first = block[:16]
                size += len(block)
                if size > limit:
                    raise HTTPException(413, detail={"message": f"文件超过 {limit // 1024 // 1024} MB 限制，请拆分"})
                digest.update(block); stream.write(block)
            stream.flush(); os.fsync(stream.fileno())
        if size == 0 or kind == "pdf" and not first.startswith(b"%PDF-"):
            raise HTTPException(422, detail={"message": "文件为空或 PDF 格式无效"})
        # The legacy call helper accepts positional args; keep the transaction in
        # a closure so ownership still comes only from current_user().
        result = call(lambda: store.create_asset(title=name, kind=kind, filename=filename, mime=MIMES[extension],
            size=size, storage_key=key, checksum=digest.hexdigest(), allow_cloud=allow_cloud, original_at=original_at or None))
        committed = not result["data"]["reused"]
        return result
    finally:
        await file.close()
        if not committed: target.unlink(missing_ok=True)


@router.post("/web-sources")
def web_source(body: WebInput):
    from app.crawl.http import public_url
    from app.crawl.errors import CrawlError
    def save():
        validate_private_text(body.title)
        try:
            public_url(body.url)
        except CrawlError:
            raise ValueError("只支持不带账号密码的公开 HTTP／HTTPS 网页，不能读取本机或内网地址") from None
        return store.create_asset(title=body.title, kind="web", url=body.url, allow_cloud=body.allow_cloud)
    return call(save)


@router.post("/sources/{source_id}/reading")
def reading(source_id: str, body: RunInput):
    return call(lambda: store.enqueue(source_id, "analyze", body.allow_cloud))


@router.post("/jobs/{job_id}/retry")
def retry(job_id: str, body: RunInput):
    return call(lambda: store.retry(job_id, allow_cloud=body.allow_cloud, acknowledge_uncertain=body.acknowledge_uncertain))


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    return call(store.cancel, job_id)


@router.post("/sources/{source_id}/revisions")
def revision(source_id: str, body: RevisionInput):
    def save():
        for edit in body.edits: validate_private_text(edit.text, edit.speaker)
        for value in body.speaker_names.values(): validate_private_text(value)
        return store.revise(source_id, body)
    return call(save)


@router.post("/sources/{source_id}/questions")
def question(source_id: str, body: QuestionInput):
    def answer():
        validate_private_text(body.question)
        units = store.get_units(source_id)
        existing = store.artifact(source_id, "qa:" + body.request_id)
        if existing:
            if existing["question"] != body.question:
                raise store.Conflict("同一请求标识对应了不同问题，请刷新后重试")
            return existing
        if not body.allow_cloud:
            raise HTTPException(422, detail={"message": "请先允许配置的文本模型处理问题和相关资料片段"})
        try:
            result = intelligence.question(body.question, units)
        except providers.NeedsConfiguration as exc:
            raise HTTPException(503, detail={"message": str(exc)}) from None
        except providers.ProviderFailure as exc:
            raise HTTPException(502, detail={"message": str(exc)}) from None
        except Exception:
            raise HTTPException(502, detail={"message": "模型未返回可核对的答案；资料已保留，可稍后重试"}) from None
        return store.save_artifact(source_id, "qa:" + body.request_id, "qa", result)["data"]
    return call(answer)


@router.delete("/sources/{source_id}/material")
def delete_material(source_id: str, body: DeleteInput):
    def remove():
        result = store.delete_material(source_id)
        pending = False
        try:
            for key in result["cleanup"]: worker.stored_path(key).unlink(missing_ok=True)
            preview = worker.root_dir() / ("preview-" + result["root_id"])
            if preview.exists(): shutil.rmtree(preview)
        except OSError:
            pending = True
        if not pending: store.cleanup_done(result["root_id"])
        return {"deleted": True, "file_cleanup_pending": pending,
                "message": "资料与引用摘录已删除，已确认任务保留" if not pending else "资料访问已关闭，文件清理未完成，请重试删除"}
    return call(remove)


@router.get("/sources/{source_id}/file")
def original(source_id: str):
    record = call(store.get_asset, source_id)["data"]
    if not record["storage_key"]:
        raise HTTPException(404, detail={"message": "此资料没有原始附件"})
    target = worker.stored_path(record["storage_key"])
    if not target.exists():
        raise HTTPException(404, detail={"message": "原始附件不存在"})
    return FileResponse(target, media_type=record["mime"], filename=record["filename"],
        content_disposition_type="inline" if record["kind"] == "audio" else "attachment",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/sources/{source_id}/pages/{number}")
def page_image(source_id: str, number: int):
    record = call(store.get_asset, source_id)["data"]
    if record["kind"] != "pdf" or number < 1:
        raise HTTPException(404, detail={"message": "页码不存在"})
    # Never cache outside the authenticated store or allow user path components.
    directory = worker.root_dir() / ("preview-" + record["id"])
    directory.mkdir(exist_ok=True)
    target = directory / f"page-{number}.png"
    if not target.exists():
        temp = directory / (uuid.uuid4().hex + ".png")
        try:
            call(lambda: parsers.render_page(worker.stored_path(record["storage_key"]), number, temp))
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    return FileResponse(target, media_type="image/png", headers={"Cache-Control": "private, no-store"})
