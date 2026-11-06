"""Media versions, resumable jobs and evidence under the existing identity scope.

Every published source version is immutable. Tasks keep their original source
even when a later transcription or speaker map is corrected.
"""
import copy
import hashlib
import json
import os
import uuid
from datetime import timedelta

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, func, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from app import db
from app.data_access import current_user, NotFound, system_scope
from app.data_access.arrangements import ArrangementSource, ArrangementCandidate, Conflict, owned, now, iso, serialize, _new_candidate
from app.data_access.session import memory_session


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class MaterialAsset(db.Base):
    __tablename__ = "material_assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # Root source id.
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    current_source_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(String(100), default="")
    filename: Mapped[str] = mapped_column(String(200), default="")
    mime: Mapped[str] = mapped_column(String(80), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    url: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=0)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    cleanup_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class MaterialVersion(db.Base):
    __tablename__ = "material_versions"
    __table_args__ = (UniqueConstraint("root_id", "number", name="uq_material_version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # Immutable source id.
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    root_id: Mapped[str] = mapped_column(String(32), index=True)
    number: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    partial: Mapped[bool] = mapped_column(Boolean, default=False)
    coverage_json: Mapped[str] = mapped_column(Text, default="{}")
    speakers_json: Mapped[str] = mapped_column(Text, default="{}")
    self_speakers_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class MaterialUnit(db.Base):
    __tablename__ = "material_units"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    locator_json: Mapped[str] = mapped_column(Text, default="{}")
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)


class MaterialJob(db.Base):
    __tablename__ = "material_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    root_id: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(80), default="等待处理")
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    allow_cloud: Mapped[bool] = mapped_column(Boolean, default=False)
    claim_token: Mapped[str] = mapped_column(String(32), default="")
    heartbeat_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(80), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class MaterialPart(db.Base):
    __tablename__ = "material_parts"
    __table_args__ = (UniqueConstraint("job_id", "part_key", name="uq_material_part"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    part_key: Mapped[str] = mapped_column(String(120))
    data_json: Mapped[str] = mapped_column(Text)


class MaterialArtifact(db.Base):
    __tablename__ = "material_artifacts"
    __table_args__ = (UniqueConstraint("user_id", "source_id", "request_key", name="uq_material_artifact"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    request_key: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))
    data_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class MaterialCandidateRef(db.Base):
    __tablename__ = "material_candidate_refs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # Candidate id.
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    evidence_json: Mapped[str] = mapped_column(Text)
    assignee: Mapped[str] = mapped_column(String(100), default="")


def asset_for(session, source_id, lock=False):
    version = session.get(MaterialVersion, source_id)
    root = version.root_id if version else source_id
    return owned(session, MaterialAsset, root, lock=lock)


def unit_dict(row):
    return {**serialize(row), "locator": json.loads(row.locator_json)} | {"locator_json": None}


def job_dict(row):
    return {k: v for k, v in serialize(row).items() if k not in {"claim_token", "heartbeat_at"}}


def candidate_refs(session, candidates):
    for item in candidates:
        ref = session.get(MaterialCandidateRef, item["id"])
        if ref:
            item["evidence"] = json.loads(ref.evidence_json)
            item["assignee"] = ref.assignee
    return candidates


def detail(session, source_id, *, full=True):
    try:
        asset = asset_for(session, source_id)
    except NotFound:
        return None
    if asset.deleted:
        raise NotFound()
    result = {k: v for k, v in serialize(asset).items() if k not in {"storage_key", "cleanup_json"}}
    jobs = session.scalars(select(MaterialJob).where(MaterialJob.root_id == asset.id).order_by(MaterialJob.created_at.desc()).limit(20))
    result["jobs"] = [job_dict(j) for j in jobs]
    if full:
        versions = session.scalars(select(MaterialVersion).where(MaterialVersion.root_id == asset.id).order_by(MaterialVersion.number.desc()))
        result["versions"] = [{"source_id": v.id, "number": v.number, "partial": v.partial, "created_at": iso(v.created_at)} for v in versions]
        version = session.get(MaterialVersion, source_id)
        result["selected_version"] = version.number if version else 0
        result["coverage"] = json.loads(version.coverage_json) if version else {}
        result["speakers"] = json.loads(version.speakers_json) if version else {}
        result["self_speakers"] = json.loads(version.self_speakers_json) if version else []
        result["partial"] = version.partial if version else True
        result["units"] = [unit_dict(u) for u in session.scalars(select(MaterialUnit).where(
            MaterialUnit.source_id == source_id).order_by(MaterialUnit.ordinal))]
        result["artifacts"] = [{"id": a.id, "kind": a.kind, "data": json.loads(a.data_json), "created_at": iso(a.created_at)}
            for a in session.scalars(select(MaterialArtifact).where(MaterialArtifact.source_id == source_id).order_by(MaterialArtifact.created_at))]
    return result


def create_asset(*, title, kind, filename="", mime="", size=0, storage_key="", checksum="", url="", allow_cloud=False, original_at=None):
    fingerprint = hashlib.sha256(dump({"file": checksum, "kind": kind, "title": title, "url": url, "original_at": original_at}).encode()).hexdigest()
    with db.memory_transaction() as session:
        existing = session.scalar(select(ArrangementSource).where(ArrangementSource.fingerprint == fingerprint))
        if existing:
            asset = asset_for(session, existing.id)
            return {"source_id": asset.current_source_id, "root_id": asset.id, "reused": True}
        _queue_cap(session)
        source = ArrangementSource(user_id=current_user(), fingerprint=fingerprint, title=title, content="",
            kind="meeting_transcript" if kind == "audio" else "document_text", original_at=original_at, tz="Asia/Shanghai")
        session.add(source); session.flush()
        asset = MaterialAsset(id=source.id, current_source_id=source.id, user_id=current_user(), kind=kind,
            storage_key=storage_key, filename=filename, mime=mime, size=size, url=url)
        session.add(asset); session.flush()
        job = MaterialJob(user_id=current_user(), root_id=asset.id, source_id=source.id, kind="parse", allow_cloud=allow_cloud)
        session.add(job); session.flush()
        return {"source_id": source.id, "root_id": asset.id, "job": job_dict(job), "reused": False}


def get_asset(source_id):
    db.init_db()
    with memory_session() as session:
        owned(session, ArrangementSource, source_id)
        asset = asset_for(session, source_id)
        if asset.deleted:
            raise NotFound()
        return serialize(asset)


def get_units(source_id):
    with memory_session() as session:
        owned(session, ArrangementSource, source_id)
        return [unit_dict(u) for u in session.scalars(select(MaterialUnit).where(MaterialUnit.source_id == source_id).order_by(MaterialUnit.ordinal))]


def enqueue(source_id, kind, allow_cloud=False):
    with db.memory_transaction() as session:
        asset = asset_for(session, source_id, lock=True)
        if asset.deleted: raise NotFound()
        if kind == "analyze" and session.get(MaterialVersion, source_id) is None:
            raise ValueError("请先完成文本提取或转写")
        existing = session.scalar(select(MaterialJob).where(MaterialJob.root_id == asset.id,
            MaterialJob.source_id == source_id, MaterialJob.kind == kind).order_by(MaterialJob.created_at.desc()))
        if existing:
            return job_dict(existing)
        _queue_cap(session)
        job = MaterialJob(user_id=current_user(), root_id=asset.id, source_id=source_id, kind=kind, allow_cloud=allow_cloud)
        session.add(job); session.flush()
        return job_dict(job)


def _queue_cap(session):
    count = session.scalar(select(func.count(MaterialJob.id)).where(MaterialJob.state.in_(["queued", "running"])))
    if count >= int(os.getenv("MATERIAL_MAX_QUEUED_JOBS", "20")):
        raise ValueError("等待处理的文件过多，请完成或停止部分处理后再提交")


def retry(job_id, *, allow_cloud, acknowledge_uncertain=False):
    with db.memory_transaction() as session:
        job = owned(session, MaterialJob, job_id, lock=True)
        if job.state in {"queued", "running", "completed"}:
            raise Conflict("该处理正在进行或已完成，请刷新查看")
        calls = session.scalars(select(MaterialPart).where(MaterialPart.job_id == job_id)).all()
        uncertain = [p for p in calls if p.part_key.startswith("cloud:") and json.loads(p.data_json).get("state") in {"started", "uncertain"}]
        if (job.state == "awaiting_review" or uncertain) and not acknowledge_uncertain:
            raise Conflict("上次云端请求的结果不明，可能已计费；请核对服务端记录并明确确认重试")
        for part in uncertain:
            part.data_json = dump({"state": "retry_allowed"})
        job.state, job.message, job.error_code = "queued", "", ""
        job.allow_cloud, job.claim_token = allow_cloud, ""
        job.updated_at = now()
        return job_dict(job)


def mark_issue(job_id, state, code, message, token):
    with db.memory_transaction() as session:
        job = owned(session, MaterialJob, job_id, lock=True)
        if job.claim_token != token or job.state not in {"completed", "partial"}:
            raise Conflict("处理状态已改变，请刷新")
        job.state, job.error_code, job.message = state, code, message
        job.updated_at = now()


def cancel(job_id):
    with db.memory_transaction() as session:
        job = owned(session, MaterialJob, job_id, lock=True)
        if job.state not in {"queued", "running"}:
            raise Conflict("该处理已停止")
        job.state, job.claim_token = "cancelled", ""
        job.message = "已停止本地后续处理；已经发出的云端请求无法撤销，重试前请核对服务记录"
        job.updated_at = now()
        return job_dict(job)


def claim_next(root_id=None):
    # This is only called by the trusted worker, never an API route.
    with system_scope(), db.memory_transaction() as session:
        query = select(MaterialJob).where(or_(MaterialJob.state == "queued",
            (MaterialJob.state == "running") & (MaterialJob.heartbeat_at < now() - timedelta(seconds=90))))
        if root_id:
            query = query.where(MaterialJob.root_id == root_id)
        job = session.scalar(query.order_by(MaterialJob.created_at).with_for_update(skip_locked=True))
        if job is None:
            return None
        stale = job.state == "running"
        if stale:
            # An interrupted cloud request cannot safely be replayed unattended.
            job.state, job.error_code = "awaiting_review", "worker_interrupted"
            job.message = "处理进程曾中断；已完成片段仍在，请核对可能已发送的云端请求后确认重试"
            job.claim_token = ""
            return None
        job.state, job.claim_token, job.heartbeat_at = "running", uuid.uuid4().hex, now()
        job.attempts += 1; job.updated_at = now()
        session.flush()
        return {**serialize(job), "worker_user_id": job.user_id}


def _leased(session, job_id, token):
    row = owned(session, MaterialJob, job_id, lock=True)
    if row.state != "running" or row.claim_token != token:
        raise Conflict("处理已停止或由新的进程接管")
    return row


def progress(job_id, token, **patch):
    with db.memory_transaction() as session:
        job = _leased(session, job_id, token)
        for k, v in patch.items():
            if k not in {"stage", "total", "done", "state", "message", "error_code"}:
                raise ValueError("不支持的处理字段")
            setattr(job, k, v)
        job.heartbeat_at = job.updated_at = now()


def parts(job_id):
    with memory_session() as session:
        owned(session, MaterialJob, job_id)
        return {p.part_key: json.loads(p.data_json) for p in session.scalars(select(MaterialPart).where(MaterialPart.job_id == job_id))}


def save_part(job_id, token, key, payload):
    with db.memory_transaction() as session:
        _leased(session, job_id, token)
        row = session.scalar(select(MaterialPart).where(MaterialPart.job_id == job_id, MaterialPart.part_key == key))
        if row:
            row.data_json = dump(payload)
        else:
            session.add(MaterialPart(user_id=current_user(), job_id=job_id, part_key=key, data_json=dump(payload)))


def _new_version(session, asset, units, coverage, *, partial=False, speakers=None, self_speakers=None):
    units = copy.deepcopy(units)
    speakers, self_speakers = speakers or {}, self_speakers or []
    digest = hashlib.sha256(dump({"units": units, "speakers": speakers, "self": self_speakers, "coverage": coverage}).encode()).hexdigest()
    previous = session.get(MaterialVersion, asset.current_source_id)
    if previous and previous.digest == digest:
        return previous.id
    root = owned(session, ArrangementSource, asset.id)
    number = asset.version + 1
    source = root if number == 1 else ArrangementSource(id=uuid.uuid4().hex, user_id=current_user(),
        fingerprint=f"material:{asset.id}:{number}", title=root.title, content="", kind=root.kind,
        original_at=root.original_at, tz=root.tz)
    if number != 1:
        session.add(source)
    source.content = ""
    for ordinal, item in enumerate(units):
        locator = item.get("locator", {})
        prefix = ""
        if "page" in locator:
            prefix = f"[第 {locator['page']} 页]\n"
        elif "start" in locator:
            label = locator.get("speaker", "unknown")
            name = speakers.get(label, label)
            prefix = f"[{locator['start']:.2f}–{locator['end']:.2f} 秒 · {name}]\n"
        else:
            prefix = f"[{'正文片段' if asset.kind in {'text', 'docx'} else '网页片段'} {ordinal + 1}]\n"
        source.content += prefix
        start = len(source.content)
        source.content += item["text"]
        end = len(source.content)
        source.content += "\n\n"
        session.add(MaterialUnit(user_id=current_user(), source_id=source.id, ordinal=ordinal,
            text=item["text"], locator_json=dump(locator), start_char=start, end_char=end))
    version = MaterialVersion(id=source.id, user_id=current_user(), root_id=asset.id, number=number, digest=digest,
        partial=partial, coverage_json=dump(coverage), speakers_json=dump(speakers), self_speakers_json=dump(self_speakers))
    session.add(version)
    asset.version, asset.current_source_id = number, source.id
    session.flush()
    return source.id


def publish_parse(job_id, token, units, coverage, *, state="completed", message="", error_code=""):
    with db.memory_transaction() as session:
        job = _leased(session, job_id, token)
        asset = owned(session, MaterialAsset, job.root_id, lock=True)
        source_id = _new_version(session, asset, units, coverage, partial=state != "completed") if units else asset.current_source_id
        job.state, job.message, job.error_code = state, message, error_code
        if "total_pages" in coverage:
            job.total, job.done = coverage["total_pages"], len(coverage["completed_pages"])
        elif "completed_chunks" in coverage:
            job.total = max(job.total, len(coverage["completed_chunks"]))
            job.done = len(coverage["completed_chunks"])
        elif state == "completed":
            job.total, job.done = 1, 1
        job.stage, job.updated_at, job.heartbeat_at = "提取完成" if state == "completed" else "部分提取或等待处理", now(), now()
        return source_id


def revise(source_id, payload):
    with db.memory_transaction() as session:
        asset = asset_for(session, source_id, lock=True)
        if asset.deleted: raise NotFound()
        if asset.current_source_id != source_id or asset.version != payload.version:
            raise Conflict("资料已有新版本，请刷新后再修订")
        if not payload.confirmed:
            raise ValueError("请明确确认修订")
        current = session.get(MaterialVersion, source_id)
        rows = session.scalars(select(MaterialUnit).where(MaterialUnit.source_id == source_id).order_by(MaterialUnit.ordinal)).all()
        edits = {e.unit_id: e for e in payload.edits}
        if len(edits) != len(payload.edits) or set(edits) - {r.id for r in rows}:
            raise ValueError("修订包含重复或未知片段")
        labels = {json.loads(r.locator_json).get("speaker") for r in rows} - {None}
        if set(payload.speaker_names) - labels or set(payload.self_speakers) - labels:
            raise ValueError("只能映射已有的发言人标记")
        if any(not n.strip() or len(n) > 80 for n in payload.speaker_names.values()):
            raise ValueError("发言人姓名须为 1 至 80 字")
        units = []
        for r in rows:
            loc = json.loads(r.locator_json)
            text = r.text
            if r.id in edits:
                edit = edits[r.id]
                if edit.speaker and edit.speaker not in labels:
                    raise ValueError("请从已有发言人中选择归属")
                text = edit.text
                if text != r.text and "rows" in loc:
                    loc.pop("rows")
                    loc["table_structure_corrected"] = True
                if "speaker" in loc:
                    loc["speaker"] = edit.speaker or "unknown"
            units.append({"text": text, "locator": loc})
        new_id = _new_version(session, asset, units, json.loads(current.coverage_json), partial=current.partial,
            speakers=payload.speaker_names, self_speakers=payload.self_speakers)
        return {"source_id": new_id, "version": asset.version, "old_source_id": source_id}


def delete_material(source_id):
    """Erase original/derived media, keep confirmed tasks with tombstone evidence."""
    from app.data_access.arrangements import ArrangementChange
    with db.memory_transaction() as session:
        asset = asset_for(session, source_id, lock=True)
        if asset.deleted:
            return {"root_id": asset.id, "cleanup": json.loads(asset.cleanup_json)}
        versions = session.scalars(select(MaterialVersion).where(MaterialVersion.root_id == asset.id)).all()
        ids = {asset.id} | {v.id for v in versions}
        from app.data_access.followup import purge_sources
        purge_sources(session, ids)
        from app.data_access.library import purge as purge_library
        purge_library(session, ids)
        for job in session.scalars(select(MaterialJob).where(MaterialJob.root_id == asset.id)):
            job.state, job.claim_token = "cancelled", ""
            job.message = "来源已删除"
            for part in session.scalars(select(MaterialPart).where(MaterialPart.job_id == job.id)):
                session.delete(part)
        for model in (MaterialUnit, MaterialArtifact, MaterialCandidateRef):
            for row in session.scalars(select(model).where(model.source_id.in_(ids))): session.delete(row)
        for row in session.scalars(select(ArrangementCandidate).where(ArrangementCandidate.source_id.in_(ids))):
            if not row.task_id: session.delete(row)
            else:
                row.title = "[来源已删除]"
                row.quote, row.quote_start, row.quote_end, row.deadline_text, row.decision_json = "[来源已删除]", 0, 0, "", ""
        def scrub(value):
            if isinstance(value, dict):
                if value.get("source_id") in ids:
                    value["quote"], value["deleted"] = "[来源已删除]", True
                    value.pop("evidence", None)
                    if value.get("action") == "source_change":
                        value["reason"] = "[来源已删除]"
                for v in value.values(): scrub(v)
            elif isinstance(value, list):
                for v in value: scrub(v)
        for change in session.scalars(select(ArrangementChange)):
            before, after = json.loads(change.before_json), json.loads(change.after_json)
            scrub(before); scrub(after)
            change.before_json, change.after_json = dump(before), dump(after)
        for row in session.scalars(select(ArrangementSource).where(ArrangementSource.id.in_(ids))):
            row.title, row.content, row.summary, row.original_at = "已删除资料", "", "", None
            row.fingerprint = "deleted:" + uuid.uuid4().hex
        for v in versions:
            v.coverage_json, v.speakers_json, v.self_speakers_json = "{}", "{}", "[]"
        cleanup = [asset.storage_key] if asset.storage_key else []
        asset.deleted, asset.cleanup_json = True, dump(cleanup)
        asset.storage_key, asset.filename, asset.url, asset.size = "", "", "", 0
        return {"root_id": asset.id, "cleanup": cleanup}


def cleanup_done(root_id):
    with db.memory_transaction() as session:
        asset = owned(session, MaterialAsset, root_id, lock=True)
        asset.cleanup_json = "[]"


def save_artifact(source_id, request_key, kind, value, *, job_id=None, token=None):
    with db.memory_transaction() as session:
        owned(session, ArrangementSource, source_id)
        if job_id:
            _leased(session, job_id, token)
        row = session.scalar(select(MaterialArtifact).where(MaterialArtifact.source_id == source_id, MaterialArtifact.request_key == request_key))
        if row is None:
            row = MaterialArtifact(user_id=current_user(), source_id=source_id, request_key=request_key, kind=kind, data_json=dump(value))
            session.add(row); session.flush()
        return {"id": row.id, "data": json.loads(row.data_json)}


def artifact(source_id, request_key):
    with memory_session() as session:
        owned(session, ArrangementSource, source_id)
        row = session.scalar(select(MaterialArtifact).where(MaterialArtifact.source_id == source_id, MaterialArtifact.request_key == request_key))
        return json.loads(row.data_json) if row else None


def publish_analysis(job_id, token, findings, overview, coverage, *, partial=False):
    with db.memory_transaction() as session:
        job = _leased(session, job_id, token)
        source = owned(session, ArrangementSource, job.source_id)
        all_units = {u.id: u for u in session.scalars(select(MaterialUnit).where(MaterialUnit.source_id == source.id))}
        # Candidate creation and report publication share a transaction.
        for result in findings:
            for action in result["actions"]:
                ev = action["evidence"][0]
                unit = all_units[ev["unit_id"]]
                exact_start = unit.start_char + unit.text.index(ev["quote"])
                existing = session.scalar(select(ArrangementCandidate).where(ArrangementCandidate.source_id == source.id,
                    ArrangementCandidate.title == action["title"], ArrangementCandidate.quote_start == exact_start))
                if not existing:
                    candidate = _new_candidate(session, source, {"title": action["title"], "quote": ev["quote"],
                        "owner": action["owner"], "deadline_text": action["deadline_text"]})
                    candidate.quote_start, candidate.quote_end = exact_start, exact_start + len(ev["quote"])
                    session.add(MaterialCandidateRef(id=candidate.id, user_id=current_user(), source_id=source.id,
                        evidence_json=dump(action["evidence"]), assignee=action["assignee"]))
        report = {"findings": findings, "overview": overview, "coverage": coverage, "partial": partial}
        existing = session.scalar(select(MaterialArtifact).where(MaterialArtifact.source_id == source.id, MaterialArtifact.request_key == "reading"))
        if existing:
            existing.data_json = dump(report)
        else:
            session.add(MaterialArtifact(user_id=current_user(), source_id=source.id, request_key="reading", kind="reading", data_json=dump(report)))
        source.summary = "\n".join(item["text"] for item in overview)
        source.analysis_status = "partial" if partial else "ready"
        job.state, job.stage = ("partial", "部分内容已整理") if partial else ("completed", "整理完成")
        job.done, job.total = coverage["completed_batches"], coverage["total_batches"]
        job.updated_at = now()
        return report
