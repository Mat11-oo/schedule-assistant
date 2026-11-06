"""Owned arrangement records and transactional mutations through the existing DAL."""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from app import db
from app.data_access import NotFound, current_user
from app.data_access.session import memory_session


def now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value and value.tzinfo is None else value.isoformat() if value else None


class ArrangementSource(db.Base):
    __tablename__ = "arrangement_sources"
    __table_args__ = (UniqueConstraint("user_id", "fingerprint", name="uq_arrangement_source"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))
    original_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tz: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text, default="")
    analysis_status: Mapped[str] = mapped_column(String(32), default="manual")
    analysis_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ArrangementCandidate(db.Base):
    __tablename__ = "arrangement_candidates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200))
    quote: Mapped[str] = mapped_column(Text)
    quote_start: Mapped[int] = mapped_column(Integer)
    quote_end: Mapped[int] = mapped_column(Integer)
    owner: Mapped[str] = mapped_column(String(32))
    deadline_text: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ArrangementTask(db.Base):
    __tablename__ = "arrangement_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(200))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="todo")
    version: Mapped[int] = mapped_column(Integer, default=1)
    reminder_schedule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ArrangementChange(db.Base):
    __tablename__ = "arrangement_changes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    before_json: Mapped[str] = mapped_column(Text)
    after_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Conflict(ValueError):
    pass


def owned(session, model, key, *, lock=False):
    stmt = select(model).where(model.id == key, model.user_id == current_user())
    row = session.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound()
    return row


def serialize(row):
    return {col.name: iso(value) if isinstance(value := getattr(row, col.name), datetime) else value
            for col in row.__table__.columns if col.name not in {"user_id", "fingerprint", "decision_json"}}


def create_source(payload):
    uid = current_user()
    data = payload.model_dump(mode="json", exclude={"user_id"})
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with db.memory_transaction() as session:
        existing = session.scalar(select(ArrangementSource).where(
            ArrangementSource.user_id == uid, ArrangementSource.fingerprint == digest))
        if existing:
            return {**serialize(existing), "reused": True}
        row = ArrangementSource(user_id=uid, fingerprint=digest, **data)
        session.add(row)
        session.flush()
        return {**serialize(row), "reused": False}


def source_detail(key):
    db.init_db()
    with memory_session() as session:
        row = owned(session, ArrangementSource, key)
        candidates = session.scalars(select(ArrangementCandidate).where(
            ArrangementCandidate.source_id == key).order_by(ArrangementCandidate.created_at)).all()
        from app.data_access import materials
        result = {**serialize(row), "candidates": materials.candidate_refs(session, [
            {**serialize(c), "source_title": row.title, "source_original_at": row.original_at, "source_tz": row.tz}
            for c in candidates])}
        material = materials.detail(session, key)
        if material:
            result["material"] = material
        return result


def list_sources():
    db.init_db()
    with memory_session() as session:
        from app.data_access.materials import MaterialAsset, MaterialVersion, detail
        old_versions = select(MaterialVersion.id).join(MaterialAsset, MaterialAsset.id == MaterialVersion.root_id).where(
            (MaterialAsset.current_source_id != MaterialVersion.id) | MaterialAsset.deleted.is_(True))
        rows = session.scalars(select(ArrangementSource).where(ArrangementSource.id.not_in(old_versions))
                               .where(ArrangementSource.id.not_in(select(MaterialAsset.id).where(MaterialAsset.deleted.is_(True))))
                               .order_by(ArrangementSource.created_at.desc()).limit(200))
        return [{**{k: v for k, v in serialize(r).items() if k != "content"}, "material": detail(session, r.id, full=False)} for r in rows]


def _new_candidate(session, source, data):
    start = source.content.find(data["quote"])
    if start < 0:
        raise ValueError("引用必须是原文中连续的一段文字")
    row = ArrangementCandidate(user_id=current_user(), source_id=source.id,
                               quote_start=start, quote_end=start + len(data["quote"]), **data)
    session.add(row)
    session.flush()
    return row


def add_candidate(key, payload):
    with db.memory_transaction() as session:
        source = owned(session, ArrangementSource, key, lock=True)
        data = payload.model_dump(exclude={"user_id"})
        existing = session.scalar(select(ArrangementCandidate).where(
            ArrangementCandidate.source_id == key, ArrangementCandidate.title == data["title"],
            ArrangementCandidate.quote == data["quote"], ArrangementCandidate.owner == data["owner"],
            ArrangementCandidate.deadline_text == data["deadline_text"]))
        return serialize(existing or _new_candidate(session, source, data))


def claim_analysis(key):
    with db.memory_transaction() as session:
        row = owned(session, ArrangementSource, key, lock=True)
        if (row.analysis_status == "processing" and row.analysis_started_at
                and (now() - datetime.fromisoformat(iso(row.analysis_started_at))).total_seconds() < 180):
            raise Conflict("资料正在整理，请稍后刷新；本轮不会重复发起")
        if row.analysis_status == "ready":
            return False
        row.analysis_status = "processing"
        row.analysis_started_at = now()
        return True


def finish_analysis(key, result=None):
    with db.memory_transaction() as session:
        source = owned(session, ArrangementSource, key, lock=True)
        if result is None:
            source.analysis_status = "failed"
            return
        # All candidates are validated before any write, and commit together.
        for candidate in result.candidates:
            if candidate.quote not in source.content:
                raise ValueError("模型引用无法在原文中定位，请手动整理")
        for candidate in result.candidates:
            data = candidate.model_dump()
            existing = session.scalar(select(ArrangementCandidate).where(
                ArrangementCandidate.source_id == key, ArrangementCandidate.quote == data["quote"],
                ArrangementCandidate.title == data["title"], ArrangementCandidate.owner == data["owner"]))
            if not existing:
                _new_candidate(session, source, data)
        source.summary, source.analysis_status = result.summary, "ready"


def list_candidates():
    db.init_db()
    with memory_session() as session:
        rows = session.scalars(select(ArrangementCandidate).where(
            ArrangementCandidate.status == "pending").order_by(ArrangementCandidate.created_at.desc()).limit(300))
        result = []
        for row in rows:
            source = owned(session, ArrangementSource, row.source_id)
            result.append({**serialize(row), "source_title": source.title, "source_tz": source.tz,
                           "source_original_at": source.original_at})
        from app.data_access.materials import candidate_refs
        return candidate_refs(session, result)


def task_dict(session, row):
    from app.agent.schedules.models import Schedule
    out = serialize(row)
    due = row.due_at
    out["overdue"] = bool(due and datetime.fromisoformat(iso(due)) < now() and row.status not in {"done", "cancelled"})
    candidates = session.scalars(select(ArrangementCandidate).where(ArrangementCandidate.task_id == row.id))
    out["sources"] = [{"source_id": c.source_id, "quote": c.quote, "candidate_id": c.id} for c in candidates]
    for item in out["sources"]:
        if item["quote"] == "[来源已删除]": item["deleted"] = True
    reminder = session.get(Schedule, row.reminder_schedule_id) if row.reminder_schedule_id else None
    out["reminder_at"] = iso(reminder.next_run_at) if reminder and reminder.status == "active" else None
    out["reminder_status"] = reminder.status if reminder else None
    return out


def list_tasks():
    db.init_db()
    with memory_session() as session:
        return [task_dict(session, row) for row in session.scalars(
            select(ArrangementTask).order_by(ArrangementTask.created_at.desc()).limit(500))]


def _cancel_reminder(session, task):
    from app.agent.schedules.models import Schedule
    if task.reminder_schedule_id:
        row = session.get(Schedule, task.reminder_schedule_id)
        if row and row.user_id == current_user():
            row.status, row.enabled, row.next_run_at = "deleted", False, None
        task.reminder_schedule_id = None


def _set_reminder(session, task, reminder_at):
    from app.agent.schedules.models import Schedule
    from app.agent.schedules.service import _apply
    existing = session.get(Schedule, task.reminder_schedule_id) if task.reminder_schedule_id else None
    if reminder_at and existing and existing.status != "deleted":
        original = json.loads(existing.trigger_json).get("run_at")
        if original and datetime.fromisoformat(original) == reminder_at:
            # Saving an unrelated edit must not replay a delivered reminder or
            # reject an elapsed reminder that was already explicitly configured.
            existing.name = task.title
            existing.prompt_text = f"个人任务提醒：{task.title}。提醒送达不代表任务已完成。"
            return
    _cancel_reminder(session, task)
    if reminder_at:
        result = _apply(session, current_user(), "create", {
            "name": task.title, "job_kind": "one_shot_reminder",
            "prompt_text": f"个人任务提醒：{task.title}。提醒送达不代表任务已完成。",
            "trigger": {"type": "once", "run_at": reminder_at.isoformat()},
            "tz": "Asia/Shanghai", "delivery": "in_app", "max_runs": 1,
        }, created_by="arrangement_confirmed")
        task.reminder_schedule_id = result["id"]


def decide(key, payload):
    uid = current_user()
    decision_json = payload.model_dump_json(exclude={"user_id"})
    with db.memory_transaction() as session:
        from app.data_access import planning
        calendar = planning.lock_calendar(session)
        candidate = owned(session, ArrangementCandidate, key, lock=True)
        if candidate.status != "pending":
            if candidate.decision_json != decision_json:
                raise Conflict("这条事项已处理，请刷新后查看任务")
            task = owned(session, ArrangementTask, candidate.task_id) if candidate.task_id else None
            return {"candidate": serialize(candidate), "task": task_dict(session, task) if task else None}
        task = None
        if payload.action == "accept":
            if candidate.owner != "self" and not payload.take_ownership:
                raise ValueError("这项工作未明确属于你，请先确认是否由你承担")
            task = ArrangementTask(user_id=uid, **payload.model_dump(exclude={
                "user_id", "action", "confirmed", "link_task_id", "reminder_at", "take_ownership"}))
            session.add(task)
            session.flush()
            _set_reminder(session, task, payload.reminder_at)
            planning.task_changed(session, calendar, task)
            candidate.task_id = task.id
        elif payload.action == "link":
            if not payload.link_task_id:
                raise ValueError("请选择要关联的已有任务")
            task = owned(session, ArrangementTask, payload.link_task_id, lock=True)
            candidate.task_id = task.id
        candidate.status, candidate.decision_json = payload.action, decision_json
        session.flush()
        if task:
            session.add(ArrangementChange(user_id=uid, task_id=task.id, before_json="{}",
                after_json=json.dumps({"action": payload.action, "candidate_id": candidate.id,
                                      "task": task_dict(session, task)}, ensure_ascii=False)))
        return {"candidate": serialize(candidate), "task": task_dict(session, task) if task else None}


def update_task(key, payload):
    with db.memory_transaction() as session:
        from app.data_access import planning
        calendar = planning.lock_calendar(session)
        task = owned(session, ArrangementTask, key, lock=True)
        if task.version != payload.version:
            raise Conflict("任务已在其他页面更新，请刷新后再修改")
        before = task_dict(session, task)
        for field, value in payload.model_dump(exclude={"user_id", "confirmed", "version", "reminder_at"}).items():
            setattr(task, field, value)
        if task.status in {"done", "cancelled"}:
            _cancel_reminder(session, task)
        else:
            _set_reminder(session, task, payload.reminder_at)
        task.version += 1
        task.updated_at = now()
        planning.task_changed(session, calendar, task)
        session.flush()
        result = task_dict(session, task)
        session.add(ArrangementChange(user_id=current_user(), task_id=task.id,
            before_json=json.dumps(before, ensure_ascii=False), after_json=json.dumps(result, ensure_ascii=False)))
        return result


def task_history(key):
    db.init_db()
    with memory_session() as session:
        task = owned(session, ArrangementTask, key)
        changes = session.scalars(select(ArrangementChange).where(
            ArrangementChange.task_id == key).order_by(ArrangementChange.created_at))
        return {"task": task_dict(session, task), "changes": [{"id": c.id, "created_at": iso(c.created_at),
                "before": json.loads(c.before_json), "after": json.loads(c.after_json)} for c in changes]}
