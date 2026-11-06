"""Owned calendar records, revision fences and atomic plan confirmation."""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select, text
from sqlalchemy.orm import Mapped, mapped_column

from app import db
from app.data_access import current_user
from app.data_access.arrangements import ArrangementTask, Conflict, iso, now, owned, serialize
from app.data_access.session import memory_session
from app.planning import engine


def uid():
    return uuid.uuid4().hex


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PlanningCalendar(db.Base):
    __tablename__ = "planning_calendars"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    configured: Mapped[bool] = mapped_column(Boolean, default=False)
    tz: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    weekly_json: Mapped[str] = mapped_column(Text, default="[]")
    daily_limit_minutes: Mapped[int] = mapped_column(Integer, default=240)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=10)


class PlanningEvent(db.Base):
    __tablename__ = "planning_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(200))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)


class PlanningTiming(db.Base):
    __tablename__ = "planning_timings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # Same id as the owned task.
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    remaining_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    splittable: Mapped[bool] = mapped_column(Boolean, default=False)
    min_block_minutes: Mapped[int] = mapped_column(Integer, default=30)
    dependencies_json: Mapped[str] = mapped_column(Text, default="[]")


class PlanningBlock(db.Base):
    __tablename__ = "planning_blocks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    plan_id: Mapped[str] = mapped_column(String(32))


class PlanningProposal(db.Base):
    __tablename__ = "planning_proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[str] = mapped_column(Text)
    preview_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="preview")
    result_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlanningChange(db.Base):
    __tablename__ = "planning_changes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str] = mapped_column(String(300))
    before_json: Mapped[str] = mapped_column(Text)
    after_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def log(session, kind, key, before, after, reason):
    session.add(PlanningChange(user_id=current_user(), kind=kind, entity_id=key,
        before_json=dumps(before), after_json=dumps(after), reason=reason))


def lock_calendar(session):
    owner = current_user()
    if session.bind.dialect.name == "postgresql":
        # Serialise first creation as well as later changes. Never use an
        # unscoped SQL statement against a private business table.
        key = int.from_bytes(hashlib.sha256(("planning:" + owner).encode()).digest()[:8], "big", signed=True)
        session.connection().execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    row = session.scalar(select(PlanningCalendar).where(PlanningCalendar.user_id == owner).with_for_update())
    if row is None:
        row = PlanningCalendar(user_id=owner)
        session.add(row)
        session.flush()
    return row


def preferences(row=None):
    if row is None:
        return {"version": 0, "configured": False, "tz": "Asia/Shanghai", "weekly": [],
                "daily_limit_minutes": 240, "buffer_minutes": 10}
    return {**{k: v for k, v in serialize(row).items() if k not in {"id", "weekly_json"}}, "weekly": json.loads(row.weekly_json)}


def timing_dict(row):
    return {**{k: v for k, v in serialize(row).items() if k != "dependencies_json"}, "dependency_ids": json.loads(row.dependencies_json)}


def bounded_rows(session, statement, limit=5000):
    rows = session.scalars(statement.limit(limit + 1)).all()
    if len(rows) > limit:
        raise ValueError("本轮待处理记录过多，请先清理已结束的安排或缩小任务范围")
    return rows


def snapshot(session, start, end, calendar=None):
    if calendar is None:
        calendar = session.scalar(select(PlanningCalendar))
    tasks = bounded_rows(session, select(ArrangementTask).order_by(ArrangementTask.id), 1000)
    titles = {t.id: t.title for t in tasks}
    gap = timedelta(minutes=(calendar.buffer_minutes if calendar else 10))
    events = bounded_rows(session, select(PlanningEvent).where(PlanningEvent.status == "active",
        PlanningEvent.end_at > start - gap, PlanningEvent.start_at < end + gap).order_by(PlanningEvent.id))
    today_start = engine.bounds(now().astimezone(engine.ZONE).date(), now().astimezone(engine.ZONE).date())[0]
    blocks = bounded_rows(session, select(PlanningBlock).where(PlanningBlock.status == "active", PlanningBlock.end_at > min(start, today_start)).order_by(PlanningBlock.id))
    timings = bounded_rows(session, select(PlanningTiming).order_by(PlanningTiming.id), 1000)
    return {"preferences": preferences(calendar), "tasks": [serialize(t) for t in tasks],
            "events": [serialize(e) for e in events], "timings": [timing_dict(t) for t in timings],
            "blocks": [{**serialize(b), "title": titles.get(b.task_id, "任务已不可用")} for b in blocks]}


def fingerprint(data):
    return hashlib.sha256(dumps(data).encode()).hexdigest()


def state(start_date=None, end_date=None):
    db.init_db()
    today = now().astimezone(engine.ZONE).date()
    start_date = start_date or today
    end_date = end_date or (start_date + timedelta(days=6))
    if not 0 <= (end_date - start_date).days <= 30:
        raise ValueError("每次可查看 1 至 31 天")
    start, end = engine.bounds(start_date, end_date)
    with memory_session() as session:
        data = snapshot(session, start, end)
        data["issues"] = engine.issues(data, start, end)
        data["blocks"] = [b for b in data["blocks"] if engine.overlap(engine.dt(b["start_at"]), engine.dt(b["end_at"]), start, end)]
        proposals = session.scalars(select(PlanningProposal).where(PlanningProposal.status == "preview",
            PlanningProposal.expires_at > now()).order_by(PlanningProposal.created_at.desc()).limit(10))
        data.update(start_date=start_date.isoformat(), end_date=end_date.isoformat(),
                    conflicts=engine.conflicts(data["events"], data["blocks"], data["preferences"]),
                    proposals=[proposal_dict(p) for p in proposals], calendar_connected=False,
                    coverage="仅包含本应用内手动固定安排与已确认时间块；外部日历未连接。")
        return data


def save_preferences(body):
    with db.memory_transaction() as session:
        row = lock_calendar(session)
        if row.version != body.version:
            raise Conflict("日程已变化，请刷新后重新保存设置")
        before = preferences(row)
        row.weekly_json = dumps([w.model_dump(exclude={"user_id"}) for w in body.weekly])
        row.daily_limit_minutes, row.buffer_minutes, row.tz = body.daily_limit_minutes, body.buffer_minutes, body.tz
        row.configured, row.version = True, row.version + 1
        log(session, "preferences", row.id, before, preferences(row), "用户确认可用时段与容量")
        return preferences(row)


def save_event(body, key=None):
    with db.memory_transaction() as session:
        calendar = lock_calendar(session)
        if key:
            row = owned(session, PlanningEvent, key, lock=True)
            if row.version != body.version or row.status != "active":
                raise Conflict("固定安排已变化，请刷新后再修改")
            before = serialize(row)
            row.version += 1
        else:
            event_id = hashlib.sha256((current_user() + "\0" + body.request_id).encode()).hexdigest()[:32]
            previous = session.get(PlanningEvent, event_id)
            if previous:
                same = (previous.title == body.title and engine.dt(previous.start_at) == body.start_at
                        and engine.dt(previous.end_at) == body.end_at and previous.note == body.note and previous.status == "active")
                if not same:
                    raise Conflict("这次提交已处理，内容已有变化；请刷新后查看固定安排")
                data = snapshot(session, previous.start_at, previous.end_at, calendar)
                return {"event": serialize(previous), "conflicts": engine.conflicts(data["events"], data["blocks"], data["preferences"]), "reused": True}
            row = PlanningEvent(id=event_id, user_id=current_user())
            session.add(row)
            before = {}
        for field in ("title", "start_at", "end_at", "note"):
            setattr(row, field, getattr(body, field))
        calendar.version += 1
        session.flush()
        result = serialize(row)
        log(session, "event", row.id, before, result, "用户确认固定安排；不自动移动已有任务")
        data = snapshot(session, row.start_at, row.end_at, calendar)
        return {"event": result, "conflicts": engine.conflicts(data["events"], data["blocks"], data["preferences"])}


def cancel_event(key, body):
    with db.memory_transaction() as session:
        calendar = lock_calendar(session)
        row = owned(session, PlanningEvent, key, lock=True)
        if row.status == "cancelled" and row.version == body.version + 1:
            return serialize(row)
        if row.version != body.version:
            raise Conflict("固定安排已变化，请刷新后再取消")
        before = serialize(row)
        row.status, row.version = "cancelled", row.version + 1
        calendar.version += 1
        log(session, "event", key, before, serialize(row), body.reason or "用户取消固定安排")
        return serialize(row)


def save_timing(key, body):
    with db.memory_transaction() as session:
        calendar = lock_calendar(session)
        task = owned(session, ArrangementTask, key, lock=True)
        row = session.get(PlanningTiming, key)
        if task.version != body.task_version or (row.version if row else 0) != body.version:
            raise Conflict("任务或排期设置已变化，请刷新后重试")
        for dep in body.dependency_ids:
            dependency = owned(session, ArrangementTask, dep)
            if dep == key or dependency.status == "cancelled":
                raise ValueError("不能以前置任务自身或已取消任务作为依赖")
        edges = {r.id: json.loads(r.dependencies_json) for r in bounded_rows(session, select(PlanningTiming), 1000)}
        edges[key] = body.dependency_ids
        pending = set(edges) | {d for deps in edges.values() for d in deps}
        depths = {}
        while pending:
            ready = [k for k in pending if all(dep in depths for dep in edges.get(k, []))]
            if not ready:
                raise ValueError("前置关系形成循环，请调整任务顺序")
            for k in ready:
                depths[k] = 1 + max((depths[d] for d in edges.get(k, [])), default=0)
                if depths[k] > 100:
                    raise ValueError("前置层级最多 100 层，请简化任务依赖")
                pending.remove(k)
        before = timing_dict(row) if row else {}
        if row is None:
            row = PlanningTiming(id=key, user_id=current_user(), version=0)
            session.add(row)
        row.remaining_minutes, row.splittable, row.min_block_minutes = body.remaining_minutes, body.splittable, body.min_block_minutes
        row.dependencies_json, row.version = dumps(body.dependency_ids), row.version + 1
        calendar.version += 1
        session.flush()
        result = timing_dict(row)
        log(session, "timing", key, before, result, "用户确认剩余工作量与前置关系；不自动标记完成")
        return result


def proposal_dict(row):
    return {"id": row.id, "status": row.status, "created_at": iso(row.created_at), "expires_at": iso(row.expires_at),
            "preview": json.loads(row.preview_json), "result": json.loads(row.result_json) if row.result_json else None}


def _proposal(session, calendar, data, request, preview):
    if len(preview["additions"]) > 2000:
        raise ValueError("单次安排产生过多时间块，请缩小安排范围")
    row = PlanningProposal(user_id=current_user(), revision=calendar.version, fingerprint=fingerprint(data),
        request_json=dumps(request), preview_json=dumps(preview), expires_at=now() + timedelta(minutes=30))
    session.add(row)
    session.flush()
    return proposal_dict(row)


def preview(body):
    with db.memory_transaction() as session:
        calendar = lock_calendar(session)
        start, end = engine.bounds(body.start_date, body.end_date)
        data = snapshot(session, start, end, calendar)
        plan = engine.generate(data, body, now())
        return _proposal(session, calendar, data, {"start": start.isoformat(), "end": end.isoformat()}, plan)


def move_preview(key, body):
    with db.memory_transaction() as session:
        calendar = lock_calendar(session)
        row = owned(session, PlanningBlock, key, lock=True)
        if row.version != body.version or row.status != "active":
            raise Conflict("时间块已变化，请刷新后重新改期")
        if row.locked and not body.unlock_confirmed:
            raise ValueError("该时间块已锁定，请明确确认移动并解锁")
        if engine.dt(row.start_at) <= now() or body.start_at <= now():
            raise ValueError("已开始的时间块不改写；请更新任务剩余耗时，再安排未来时间")
        if body.end_at - body.start_at != engine.dt(row.end_at) - engine.dt(row.start_at):
            raise ValueError("改期需保持原时长；工作量变化请取消未来时间块并更新剩余耗时")
        if body.start_at.astimezone(engine.ZONE).date() != (body.end_at - timedelta(microseconds=1)).astimezone(engine.ZONE).date():
            raise ValueError("单个任务时间块不能跨午夜")
        task = owned(session, ArrangementTask, row.task_id)
        if task.status not in {"todo", "doing"}:
            raise ValueError("任务当前不适合排期，请先调整任务状态")
        if task.due_at and body.end_at > engine.dt(task.due_at):
            raise ValueError("改期晚于任务截止时间，请先明确调整截止时间")
        start, end = engine.bounds(body.start_at.astimezone(engine.ZONE).date(), body.start_at.astimezone(engine.ZONE).date())
        data = snapshot(session, start, end, calendar)
        if not calendar.configured:
            raise ValueError("请先保存可用时段")
        free = engine.working(data["preferences"], start, end, now())
        if not any(a <= body.start_at and body.end_at <= b for a, b in free):
            raise ValueError("改期超出已确认的可用时段")
        kept = [b for b in data["blocks"] if b["id"] != key]
        added = {"task_id": row.task_id, "title": task.title, "start_at": body.start_at.isoformat(),
                 "end_at": body.end_at.isoformat(), "locked": False, "minutes": engine.minutes(body.start_at, body.end_at), "reason": body.reason}
        checks = engine.conflicts(data["events"], kept + [{**added, "id": "new"}], data["preferences"])
        if any(c["first"]["id"] == "new" or c["second"]["id"] == "new" for c in checks):
            raise ValueError("该时段存在固定安排、其他任务或缓冲冲突，请更换时间")
        used = sum(engine.minutes(max(start, engine.dt(b["start_at"])), min(end, engine.dt(b["end_at"]))) for b in kept)
        if used + added["minutes"] > calendar.daily_limit_minutes:
            raise ValueError("改期将超过当天任务投入上限")
        timing = session.get(PlanningTiming, task.id)
        future_blocks = [serialize(b) for b in bounded_rows(session, select(PlanningBlock).where(
            PlanningBlock.status == "active", PlanningBlock.end_at > now())) if b.id != key]
        for dep in json.loads(timing.dependencies_json) if timing else []:
            dep_task = owned(session, ArrangementTask, dep)
            if dep_task.status == "done":
                continue
            dep_timing = session.get(PlanningTiming, dep)
            required = dep_timing.remaining_minutes if dep_timing and dep_timing.remaining_minutes is not None else dep_task.estimated_minutes
            booked = [b for b in future_blocks if b["task_id"] == dep]
            if (required is None or not booked or sum(engine.minutes(max(now(), engine.dt(b["start_at"])), engine.dt(b["end_at"])) for b in booked) < required
                    or max(engine.dt(b["end_at"]) for b in booked) + timedelta(minutes=calendar.buffer_minutes) > body.start_at):
                raise ValueError("改期不满足前置任务顺序，请先完整安排或完成前置任务")
        current_end = max([body.end_at] + [engine.dt(b["end_at"]) for b in future_blocks if b["task_id"] == task.id])
        for other in bounded_rows(session, select(PlanningTiming), 1000):
            if task.id in json.loads(other.dependencies_json):
                if any(engine.dt(b["start_at"]) < current_end + timedelta(minutes=calendar.buffer_minutes)
                       for b in future_blocks if b["task_id"] == other.id):
                    raise ValueError("改期将越过已安排的后续任务，请先调整后续安排")
        plan = {"kind": "manual_move", "additions": [added], "removed": [serialize(row)], "kept": [],
                "unallocated": [], "days": [], "conflicts": [], "warnings": ["仅保存到本应用；已锁定的原时间块在本次明确确认后解锁移动。"],
                "coverage": "仅核对本应用内固定安排及任务时间块。"}
        return _proposal(session, calendar, data, {"start": start.isoformat(), "end": end.isoformat()}, plan)


def confirm(key):
    with db.memory_transaction() as session:
        calendar = lock_calendar(session)
        row = owned(session, PlanningProposal, key, lock=True)
        if row.status == "confirmed":
            return json.loads(row.result_json)
        if row.status != "preview" or engine.dt(row.expires_at) < now():
            raise Conflict("排期预览已过期或已取消，请重新生成")
        request = json.loads(row.request_json)
        data = snapshot(session, engine.dt(request["start"]), engine.dt(request["end"]), calendar)
        if row.revision != calendar.version or row.fingerprint != fingerprint(data):
            raise Conflict("任务、固定安排或设置已变化，请重新生成方案；本次没有覆盖新安排")
        plan = json.loads(row.preview_json)
        if any(engine.dt(b["start_at"]) <= now() for b in plan["additions"]):
            raise Conflict("建议开始时间已经过去，请重新生成排期")
        if not plan["additions"] and not plan["removed"]:
            raise ValueError("该方案没有需要保存的时间块，请先处理未安排原因")
        for old in plan["removed"]:
            block = owned(session, PlanningBlock, old["id"], lock=True)
            before = serialize(block)
            block.status, block.version = "cancelled", block.version + 1
            log(session, "block", block.id, before, serialize(block), "确认改期，保留原时间块历史")
        new_ids = []
        for new in plan["additions"]:
            task = owned(session, ArrangementTask, new["task_id"])
            if task.status not in {"todo", "doing"}:
                raise Conflict("任务状态已变化，请重新安排")
            block = PlanningBlock(user_id=current_user(), task_id=task.id, start_at=engine.dt(new["start_at"]),
                end_at=engine.dt(new["end_at"]), locked=False, plan_id=key)
            session.add(block)
            session.flush()
            log(session, "block", block.id, {}, serialize(block), new["reason"])
            new_ids.append(block.id)
        calendar.version += 1
        result = {"plan_id": key, "block_ids": new_ids, "cancelled_ids": [b["id"] for b in plan["removed"]],
                  "calendar_version": calendar.version, "saved_in_app": True, "external_calendar_synced": False}
        row.status, row.result_json = "confirmed", dumps(result)
        log(session, "plan", key, {"status": "preview"}, result, "用户明确确认排期；任务状态与截止时间保持原值")
        return result


def proposal(key):
    db.init_db()
    with memory_session() as session:
        return proposal_dict(owned(session, PlanningProposal, key))


def dismiss(key):
    with db.memory_transaction() as session:
        row = owned(session, PlanningProposal, key, lock=True)
        if row.status == "confirmed":
            raise Conflict("方案已保存；请从时间线调整具体时间块")
        row.status = "dismissed"
        return {"id": key, "status": row.status}


def block_action(key, body, *, cancel=False):
    with db.memory_transaction() as session:
        calendar = lock_calendar(session)
        row = owned(session, PlanningBlock, key, lock=True)
        if row.version != body.version or row.status != "active":
            raise Conflict("时间块已变化，请刷新后再操作")
        before = serialize(row)
        if cancel:
            if engine.dt(row.start_at) <= now():
                raise ValueError("已开始的时间块保留历史；请更新任务剩余耗时以安排后续工作")
            row.status = "cancelled"
        else:
            row.locked = body.locked
        row.version += 1
        calendar.version += 1
        log(session, "block", key, before, serialize(row), body.reason or ("用户取消未来时间块" if cancel else "用户调整时间块锁定"))
        return serialize(row)


def task_changed(session, calendar, task):
    calendar.version += 1
    if task.status not in {"done", "cancelled", "waiting"}:
        return
    for b in session.scalars(select(PlanningBlock).where(PlanningBlock.task_id == task.id,
            PlanningBlock.status == "active", PlanningBlock.end_at > now())):
        before = serialize(b)
        b.status, b.version = "cancelled", b.version + 1
        log(session, "block", b.id, before, serialize(b), "任务完成、取消或等待，撤销尚未结束的安排；不推断实际耗时")


def history():
    db.init_db()
    with memory_session() as session:
        return [{"id": r.id, "kind": r.kind, "entity_id": r.entity_id, "reason": r.reason,
                 "before": json.loads(r.before_json), "after": json.loads(r.after_json), "created_at": iso(r.created_at)}
                for r in session.scalars(select(PlanningChange).order_by(PlanningChange.created_at.desc(), PlanningChange.id.desc()).limit(100))]
