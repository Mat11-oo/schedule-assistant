"""Owned follow-up data. No model calls, external delivery or silent task merges."""
import hashlib
import json
import unicodedata
import uuid
from datetime import datetime, time, timedelta, timezone
from difflib import SequenceMatcher

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app import db
from app.arrangements.schemas import TaskUpdate
from app.data_access import current_user
from app.data_access import arrangements as tasks, planning
from app.data_access.arrangements import ArrangementCandidate, ArrangementChange, ArrangementSource, ArrangementTask, Conflict, iso, now, owned, serialize
from app.data_access.session import memory_session
from app.planning import engine

SECTIONS = ["schedule", "due", "overdue", "waiting", "conflicts", "decisions"]


class BriefPreference(db.Base):
    __tablename__ = "arrangement_brief_preferences"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    at: Mapped[str] = mapped_column(String(5), default="08:30")
    lookahead_days: Mapped[int] = mapped_column(Integer, default=3)
    sections_json: Mapped[str] = mapped_column(Text)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_day: Mapped[str] = mapped_column(String(10), default="")


class BriefSnapshot(db.Base):
    __tablename__ = "arrangement_briefs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    origin: Mapped[str] = mapped_column(String(16))
    data_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MatchDecision(db.Base):
    __tablename__ = "arrangement_match_decisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    candidate_id: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SourceChangeProposal(db.Base):
    __tablename__ = "arrangement_update_proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    candidate_id: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    task_version: Mapped[int] = mapped_column(Integer)
    before_json: Mapped[str] = mapped_column(Text)
    changes_json: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="preview")
    result_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def preference(session):
    row = session.scalar(select(BriefPreference))
    if row is None:
        return {"version": 0, "enabled": False, "at": "08:30", "lookahead_days": 3,
                "sections": SECTIONS, "next_run_at": None, "tz": "Asia/Shanghai"}
    return {**{k: v for k, v in serialize(row).items() if k not in {"id", "sections_json", "last_day"}},
            "sections": json.loads(row.sections_json), "tz": "Asia/Shanghai"}


def next_run(at, clock, last_day=""):
    local = clock.astimezone(engine.ZONE)
    h, m = map(int, at.split(":"))
    target = datetime.combine(local.date(), time(h, m), engine.ZONE)
    if last_day == local.date().isoformat():
        target += timedelta(days=1)
    return max(target.astimezone(timezone.utc), clock)


def save_preferences(body):
    with db.memory_transaction() as session:
        planning.lock_calendar(session)
        row = session.scalar(select(BriefPreference))
        if (row.version if row else 0) != body.version:
            raise Conflict("简报设置已变化，请刷新后重试")
        if row is None:
            row = BriefPreference(id=hashlib.sha256(current_user().encode()).hexdigest()[:32], user_id=current_user(), version=0, last_day="")
            session.add(row)
        row.enabled, row.at, row.lookahead_days = body.enabled, body.at, body.lookahead_days
        row.sections_json, row.version = planning.dumps(body.sections), row.version + 1
        row.next_run_at = next_run(row.at, now(), row.last_day) if row.enabled else None
        session.flush()
        return preference(session)


def build_brief(session, clock, prefs):
    day = clock.astimezone(engine.ZONE).date()
    start, end = engine.bounds(day, day)
    data = planning.snapshot(session, start, end)
    active = [t for t in data["tasks"] if t["status"] not in {"done", "cancelled"}]
    def task_view(t):
        return {k: t[k] for k in ("id", "title", "status", "due_at", "priority", "updated_at")}
    due_end = end + timedelta(days=prefs["lookahead_days"] - 1)
    due = sorted([t for t in active if t["due_at"] and clock <= engine.dt(t["due_at"]) < due_end], key=lambda t: t["due_at"])
    overdue = sorted([t for t in active if t["due_at"] and engine.dt(t["due_at"]) < clock], key=lambda t: t["due_at"])
    waiting = [t for t in active if t["status"] == "waiting"]
    blocks = [b for b in data["blocks"] if engine.overlap(engine.dt(b["start_at"]), engine.dt(b["end_at"]), start, end)]
    events = [e for e in data["events"] if engine.overlap(engine.dt(e["start_at"]), engine.dt(e["end_at"]), start, end)]
    schedule = sorted([{k: b[k] for k in ("id", "title", "task_id", "start_at", "end_at", "locked")} | {"kind": "block"} for b in blocks] +
                      [{k: e[k] for k in ("id", "title", "start_at", "end_at")} | {"kind": "event"} for e in events], key=lambda r: r["start_at"])
    conflicts = engine.conflicts(events, blocks, data["preferences"])
    issues = engine.issues(data, start, end)
    candidate_count = len(planning.bounded_rows(session, select(ArrangementCandidate).where(ArrangementCandidate.status == "pending"), 5000))
    plan_count = len(planning.bounded_rows(session, select(planning.PlanningProposal).where(
        planning.PlanningProposal.status == "preview", planning.PlanningProposal.expires_at > clock), 5000))
    update_count = len(planning.bounded_rows(session, select(SourceChangeProposal).where(
        SourceChangeProposal.status == "preview", SourceChangeProposal.expires_at > clock), 5000))
    sections = prefs["sections"]
    groups = {"schedule": schedule[:100], "due": [task_view(t) for t in due[:50]], "overdue": [task_view(t) for t in overdue[:50]],
              "waiting": [task_view(t) for t in waiting[:50]], "conflicts": conflicts, "issues": issues}
    visible = {k: v for k, v in groups.items() if k in sections or (k == "issues" and "conflicts" in sections)}
    return {"day": day.isoformat(), "generated_at": clock.isoformat(), "tz": "Asia/Shanghai", "lookahead_days": prefs["lookahead_days"],
            "counts": {"active": len(active), "schedule": len(schedule), "due": len(due), "overdue": len(overdue), "waiting": len(waiting),
                       "conflicts": len(conflicts), "issues": len(issues), "decisions": candidate_count + plan_count + update_count},
            "decisions": {"candidates": candidate_count, "plans": plan_count, "updates": update_count} if "decisions" in sections else None,
            "groups": visible, "sections": sections, "calendar_connected": False,
            "coverage": "仅依据本应用已保存的任务与安排；分组可能重叠，简报不是实际完成情况或外部日历同步结果。",
            "limits": "每组任务最多列出 50 项、日程 100 项；冲突与问题各最多 100 项。"}


def brief_state():
    db.init_db()
    with memory_session() as session:
        prefs = preference(session)
        saved = session.scalars(select(BriefSnapshot).order_by(BriefSnapshot.created_at.desc()).limit(10))
        return {"preferences": prefs, "brief": build_brief(session, now(), prefs),
                "saved": [{"id": r.id, "day": r.day, "origin": r.origin, "created_at": iso(r.created_at)} for r in saved]}


def save_brief():
    with db.memory_transaction() as session:
        planning.lock_calendar(session)
        data = build_brief(session, now(), preference(session))
        row = BriefSnapshot(user_id=current_user(), day=data["day"], origin="manual", data_json=planning.dumps(data))
        session.add(row)
        session.flush()
        return {"id": row.id, "day": row.day, "created_at": iso(row.created_at), "brief": data}


def saved_brief(key):
    db.init_db()
    with memory_session() as session:
        row = owned(session, BriefSnapshot, key)
        return {"id": key, "created_at": iso(row.created_at), "brief": json.loads(row.data_json)}


def scheduled_brief(clock):
    """Called inside the target user's scope; receipt and inbox item commit together."""
    from app.agent.proactive.store import ProactiveItem
    with db.memory_transaction() as session:
        planning.lock_calendar(session)
        row = session.scalar(select(BriefPreference).with_for_update())
        if not row or not row.enabled or not row.next_run_at or engine.dt(row.next_run_at) > clock:
            return False
        day = clock.astimezone(engine.ZONE).date().isoformat()
        if row.last_day == day:
            row.next_run_at = next_run(row.at, clock, day)
            return False
        data = build_brief(session, clock, preference(session))
        saved = BriefSnapshot(user_id=current_user(), day=day, origin="scheduled", data_json=planning.dumps(data))
        session.add(saved)
        session.flush()
        c = data["counts"]
        labels = {"schedule": "今日安排", "due": "临期任务", "overdue": "逾期任务", "waiting": "等待事项", "conflicts": "安排冲突", "decisions": "待决定"}
        summary = "；".join(f"{labels[k]} {c[k]} 项" for k in data["sections"])
        session.add(ProactiveItem(user_id=current_user(), kind="arrangement_brief", title=f"{day} 个人安排简报",
            body=summary + "。点击查看保存时的详情；任务状态未改变。", payload_json=planning.dumps({"brief_id": saved.id, "day": day}),
            period_key="arrangement-brief:" + day, delivered_via="in_app", status="pending"))
        row.last_day, row.next_run_at = day, next_run(row.at, clock, day)
        return True


def normalize(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).lower() if c.isalnum())


def matches(key):
    db.init_db()
    from app.data_access.materials import MaterialVersion
    with memory_session() as session:
        candidate = owned(session, ArrangementCandidate, key)
        old = session.get(MaterialVersion, candidate.source_id)
        rejected = set(session.scalars(select(MatchDecision.task_id).where(MatchDecision.candidate_id == key)))
        linked = planning.bounded_rows(session, select(ArrangementCandidate).where(ArrangementCandidate.task_id.is_not(None)))
        source_versions = {v.id: v.root_id for v in planning.bounded_rows(session, select(MaterialVersion))}
        by_task = {}
        for link in linked:
            by_task.setdefault(link.task_id, []).append(link)
        result = []
        title = normalize(candidate.title)
        for task in planning.bounded_rows(session, select(ArrangementTask), 1000):
            if task.id in rejected or task.status == "cancelled":
                continue
            other = normalize(task.title)
            score = SequenceMatcher(None, title, other, autojunk=False).ratio() if title and other else 0
            grams = lambda s: {s[i:i + 2] for i in range(len(s) - 1)}
            a, b = grams(title), grams(other)
            common = len(a & b) / max(1, len(a | b))
            same_version = bool(old and any(source_versions.get(c.source_id) == old.root_id and c.source_id != candidate.source_id for c in by_task.get(task.id, [])))
            exact = title == other and bool(title)
            if not exact and not (len(title) >= 4 and len(other) >= 4 and score >= 0.78 and common >= 0.4):
                continue
            reasons = ["标题相同" if exact else "标题文字相近"]
            if same_version:
                reasons.append("同一资料的不同版本")
            if candidate.owner in {"other", "unknown"}:
                reasons.append("新来源未明确由你承担，请核对责任")
            old_dates = [c.deadline_text for c in by_task.get(task.id, []) if c.deadline_text]
            if candidate.deadline_text and old_dates and candidate.deadline_text not in old_dates:
                reasons.append("来源中的时间表述不同，需核对是否变更")
            result.append({"_task": task, "reasons": reasons, "similarity": round(score, 3),
                           "label": "可能是同一事项；不同日期或不同轮次也可能是独立任务"})
        top = sorted(result, key=lambda r: (-r["similarity"], r["_task"].id))[:8]
        return [{"task": tasks.task_dict(session, r["_task"]), **{k: v for k, v in r.items() if k != "_task"}} for r in top]


def distinct(key, body):
    with db.memory_transaction() as session:
        planning.lock_calendar(session)
        owned(session, ArrangementCandidate, key)
        owned(session, ArrangementTask, body.task_id)
        identity = hashlib.sha256((current_user() + ":" + key + ":" + body.task_id).encode()).hexdigest()
        if session.get(MatchDecision, identity) is None:
            session.add(MatchDecision(id=identity, user_id=current_user(), candidate_id=key, task_id=body.task_id))
        return {"candidate_id": key, "task_id": body.task_id, "distinct": True}


def update_dict(session, row):
    candidate = owned(session, ArrangementCandidate, row.candidate_id)
    source = owned(session, ArrangementSource, candidate.source_id)
    task = owned(session, ArrangementTask, row.task_id)
    changes = json.loads(row.changes_json)
    before = json.loads(row.before_json)
    return {"id": row.id, "candidate_id": candidate.id, "task_id": task.id, "task_title": task.title,
            "source_id": source.id, "source_title": source.title, "quote": candidate.quote, "source_original_at": source.original_at,
            "source_tz": source.tz,
            "deadline_text": candidate.deadline_text, "before": before, "changes": changes, "reason": row.reason,
            "status": row.status, "created_at": iso(row.created_at), "expires_at": iso(row.expires_at),
            "stale": task.version != row.task_version or candidate.status != "pending",
            "result": json.loads(row.result_json) if row.result_json else None}


def propose_update(key, body):
    with db.memory_transaction() as session:
        planning.lock_calendar(session)
        candidate = owned(session, ArrangementCandidate, key, lock=True)
        source = owned(session, ArrangementSource, candidate.source_id)
        task = owned(session, ArrangementTask, body.task_id, lock=True)
        if candidate.status != "pending" or task.version != body.task_version:
            raise Conflict("候选事项或任务已变化，请刷新后核对")
        if task.status in {"done", "cancelled"}:
            raise ValueError("已结束任务只关联来源，不在变更流程中重开；如需恢复，请先从任务详情明确操作")
        changes = {}
        if body.change_title and body.title != task.title:
            changes["title"] = body.title
        if body.change_due and body.due_at != (engine.dt(task.due_at) if task.due_at else None):
            changes["due_at"] = iso(body.due_at)
        status = {"keep_self": task.status, "wait_other": "waiting", "cancel_self": "cancelled"}[body.responsibility]
        if status != task.status:
            changes["status"] = status
        if not changes:
            raise ValueError("没有实际变更；只增加出处可直接关联已有任务")
        before = {k: serialize(task)[k] for k in changes}
        row = SourceChangeProposal(user_id=current_user(), candidate_id=key, task_id=task.id, task_version=task.version,
            before_json=planning.dumps(before), changes_json=planning.dumps(changes), reason=body.reason, expires_at=now() + timedelta(minutes=30))
        session.add(row)
        session.flush()
        return update_dict(session, row)


def updates():
    db.init_db()
    with memory_session() as session:
        return [update_dict(session, r) for r in session.scalars(select(SourceChangeProposal).where(
            SourceChangeProposal.status == "preview", SourceChangeProposal.expires_at > now()).order_by(SourceChangeProposal.created_at.desc()).limit(50))]


def confirm_update(key):
    with db.memory_transaction() as session:
        planning.lock_calendar(session)
        row = owned(session, SourceChangeProposal, key, lock=True)
        if row.status == "confirmed":
            return {**json.loads(row.result_json), "task": tasks.task_dict(session, owned(session, ArrangementTask, row.task_id))}
        if row.status != "preview" or engine.dt(row.expires_at) < now():
            raise Conflict("变更预览已过期或已放弃，请重新核对")
        candidate = owned(session, ArrangementCandidate, row.candidate_id, lock=True)
        task = owned(session, ArrangementTask, row.task_id, lock=True)
        if task.version != row.task_version or candidate.status != "pending":
            raise Conflict("任务或候选事项已变化；本次没有覆盖，请重新预览")
        current = tasks.task_dict(session, task)
        changed = json.loads(row.changes_json)
        payload = {k: current.get(k) for k in ("title", "due_at", "estimated_minutes", "priority", "note", "reminder_at", "status")}
        payload.update(changed, version=task.version, confirmed=True)
        tasks.update_task(task.id, TaskUpdate.model_validate(payload))
        candidate.status, candidate.task_id = "link", task.id
        candidate.decision_json = planning.dumps({"source_change_proposal": key})
        session.flush()
        result = tasks.task_dict(session, task)
        remaining = session.scalars(select(planning.PlanningBlock).where(planning.PlanningBlock.task_id == task.id,
            planning.PlanningBlock.status == "active", planning.PlanningBlock.end_at > now())).all()
        replan = bool(remaining and "due_at" in changed)
        response = {"task": result, "proposal_id": key, "replan_recommended": replan,
                    "message": "变更已保存；请核对未来时间块并按需重新排期" if replan else "变更与新出处已保存"}
        # Reconstruct current source links on retries; do not retain a second
        # cached copy of quotes from unrelated, potentially deleted materials.
        row.status, row.result_json = "confirmed", planning.dumps({k: v for k, v in response.items() if k != "task"})
        session.add(ArrangementChange(user_id=current_user(), task_id=task.id, before_json=planning.dumps(current),
            after_json=planning.dumps({"action": "source_change", "source_id": candidate.source_id, "quote": candidate.quote,
                                      "reason": row.reason, "changes": changed, "task": result})))
        return response


def dismiss_update(key):
    with db.memory_transaction() as session:
        row = owned(session, SourceChangeProposal, key, lock=True)
        if row.status == "confirmed":
            raise Conflict("变更已保存，请从任务详情调整")
        row.status = "dismissed"
        return {"id": key, "status": "dismissed"}


def purge_sources(session, source_ids):
    """No deleted attachment quotes retained in derived update previews."""
    ids = list(session.scalars(select(ArrangementCandidate.id).where(ArrangementCandidate.source_id.in_(source_ids))))
    if not ids:
        return
    for model in (SourceChangeProposal, MatchDecision):
        for row in session.scalars(select(model).where(model.candidate_id.in_(ids))):
            session.delete(row)
