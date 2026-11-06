"""Real API + isolated database coverage; model replies are explicit test doubles."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.auth import mint_user_token
from app.main import app
from app.data_access import NotFound, user_scope
from app.data_access import arrangements as store
from app.data_access.session import memory_session
from app.gateway import llm_gateway as gateway
from tests.conftest import RawTestClient

PREFIX = "/api/arrangements"


@pytest.fixture
def owner():
    return "arr-" + uuid.uuid4().hex[:12]


@pytest.fixture
def raw(owner):
    with RawTestClient(app, headers={"X-User-Token": mint_user_token(owner)}) as client:
        yield client


def data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def source(raw, **patch):
    return data(raw.post(PREFIX + "/sources", json={"title": "评审安排", "content": "我负责整理提纲。建议团队开展访谈。", **patch}))


def candidate(raw, sid, **patch):
    return data(raw.post(f"{PREFIX}/sources/{sid}/candidates", json={
        "title": "整理提纲", "quote": "我负责整理提纲。", "owner": "self", **patch}))


def decision(**patch):
    return {"title": "整理提纲", "action": "accept", "confirmed": True, **patch}


def task(raw, **patch):
    s = source(raw)
    c = candidate(raw, s["id"])
    return data(raw.post(f"{PREFIX}/candidates/{c['id']}/decide", json=decision(**patch)))["task"]


def update_payload(task, **patch):
    return {k: task[k] for k in ("title", "due_at", "estimated_minutes", "priority", "note", "reminder_at", "status", "version")} | {"confirmed": True} | patch


def test_source_and_decision_survive_reload_without_duplicate_effects(raw):
    s = source(raw)
    again = source(raw)
    assert again["id"] == s["id"] and again["reused"]
    c = candidate(raw, s["id"])
    assert candidate(raw, s["id"])["id"] == c["id"]
    assert data(raw.get(PREFIX + "/tasks")) == []
    result = data(raw.post(f"{PREFIX}/candidates/{c['id']}/decide", json=decision()))
    repeated = data(raw.post(f"{PREFIX}/candidates/{c['id']}/decide", json=decision()))
    assert result["task"]["id"] == repeated["task"]["id"]
    assert len(data(raw.get(PREFIX + "/tasks"))) == 1
    assert data(raw.get(PREFIX + "/candidates")) == []
    loaded = data(raw.get(f"{PREFIX}/sources/{s['id']}"))
    assert loaded["content"] == s["content"] and loaded["candidates"][0]["status"] == "accept"
    assert loaded["content"][c["quote_start"]:c["quote_end"]] == c["quote"]
    assert raw.post(f"{PREFIX}/candidates/{c['id']}/decide", json=decision(action="ignore")).status_code == 409


@pytest.mark.parametrize("owner_type", ["other", "unknown", "reference"])
def test_unassigned_work_requires_explicit_personal_ownership(raw, owner_type):
    s = source(raw)
    c = candidate(raw, s["id"], owner=owner_type)
    assert raw.post(f"{PREFIX}/candidates/{c['id']}/decide", json=decision()).status_code == 422
    assert data(raw.get(PREFIX + "/tasks")) == []
    result = data(raw.post(f"{PREFIX}/candidates/{c['id']}/decide", json=decision(take_ownership=True)))
    assert result["task"]["title"] == "整理提纲"


@pytest.mark.parametrize("action", ["reference", "ignore"])
def test_reference_and_ignore_do_not_create_tasks(raw, action):
    s = source(raw)
    c = candidate(raw, s["id"], owner="reference")
    data(raw.post(f"{PREFIX}/candidates/{c['id']}/decide", json=decision(action=action)))
    assert data(raw.get(PREFIX + "/tasks")) == []
    assert data(raw.get(PREFIX + "/candidates")) == []


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_confirmation_must_be_explicit_boolean(raw, confirmation):
    c = candidate(raw, source(raw)["id"])
    assert raw.post(f"{PREFIX}/candidates/{c['id']}/decide", json=decision(confirmed=confirmation)).status_code == 422
    assert data(raw.get(PREFIX + "/tasks")) == []


def test_evidence_must_be_contiguous_source_text(raw):
    s = source(raw)
    assert raw.post(f"{PREFIX}/sources/{s['id']}/candidates", json={"title": "虚构任务", "quote": "原文不存在"}).status_code == 422
    assert data(raw.get(PREFIX + "/candidates")) == []


def test_linking_adds_provenance_instead_of_duplicate_task(raw):
    t = task(raw)
    s = source(raw, title="会议决定", content="由我整理提纲，其他人补数据。")
    c = candidate(raw, s["id"], quote="由我整理提纲")
    data(raw.post(f"{PREFIX}/candidates/{c['id']}/decide", json=decision(action="link", link_task_id=t["id"])))
    tasks = data(raw.get(PREFIX + "/tasks"))
    assert len(tasks) == 1 and len(tasks[0]["sources"]) == 2


def test_update_keeps_history_checks_version_and_derives_overdue(raw):
    t = task(raw, due_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    assert t["overdue"]
    updated = data(raw.patch(f"{PREFIX}/tasks/{t['id']}", json=update_payload(t, status="done")))
    assert updated["version"] == 2 and not updated["overdue"]
    assert raw.patch(f"{PREFIX}/tasks/{t['id']}", json=update_payload(t, status="waiting")).status_code == 409
    loaded = data(raw.get(f"{PREFIX}/tasks/{t['id']}"))
    assert len(loaded["changes"]) == 2
    assert loaded["changes"][-1]["before"]["status"] == "todo"
    assert loaded["changes"][-1]["after"]["status"] == "done"


def test_reminder_is_atomic_and_cancelled_on_completion(raw, owner):
    reminder = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    t = task(raw, reminder_at=reminder)
    sid = t["reminder_schedule_id"]
    assert sid and t["reminder_at"] and t["status"] == "todo"
    schedule = data(raw.get(f"/api/schedules/{sid}"))
    assert schedule["delivery"] == "in_app" and schedule["status"] == "active"
    # Running a reminder produces an inbox item but must not complete the task.
    data(raw.post(f"/api/schedules/{sid}/run_now"))
    assert data(raw.get(PREFIX + "/tasks"))[0]["status"] == "todo"
    finished = data(raw.patch(f"{PREFIX}/tasks/{t['id']}", json=update_payload(t, status="done")))
    assert finished["reminder_schedule_id"] is None
    from app.agent.schedules.models import Schedule
    with user_scope(owner), memory_session() as session:
        row = session.get(Schedule, sid)
        assert row.status == "deleted" and not row.enabled


def test_invalid_reminder_rolls_back_task_and_decision(raw):
    s = source(raw)
    c = candidate(raw, s["id"])
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert raw.post(f"{PREFIX}/candidates/{c['id']}/decide", json=decision(reminder_at=past)).status_code == 422
    assert data(raw.get(PREFIX + "/tasks")) == []
    assert data(raw.get(PREFIX + "/candidates"))[0]["status"] == "pending"


def test_unrelated_edit_keeps_same_reminder_and_run_ledger(raw):
    reminder = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    t = task(raw, reminder_at=reminder)
    sid = t["reminder_schedule_id"]
    updated = data(raw.patch(f"{PREFIX}/tasks/{t['id']}", json=update_payload(t, note="等待补充数据")))
    assert updated["reminder_schedule_id"] == sid
    data(raw.post(f"/api/schedules/{sid}/run_now"))
    updated_again = data(raw.patch(f"{PREFIX}/tasks/{t['id']}", json=update_payload(updated, status="doing")))
    assert updated_again["reminder_schedule_id"] == sid and updated_again["reminder_status"] == "completed"
    assert len(data(raw.get(f"/api/schedules/{sid}/runs"))) == 1


def test_isolation_at_api_and_session_levels(raw, owner):
    t = task(raw)
    sid = t["sources"][0]["source_id"]
    cid = t["sources"][0]["candidate_id"]
    other = "arr-" + uuid.uuid4().hex[:12]
    headers = {"X-User-Token": mint_user_token(other)}
    assert data(raw.get(PREFIX + "/sources", headers=headers)) == []
    assert data(raw.get(PREFIX + "/tasks", headers=headers)) == []
    for path in [f"/sources/{sid}", f"/tasks/{t['id']}"]:
        assert raw.get(PREFIX + path, headers=headers).status_code == 404
    assert raw.post(f"{PREFIX}/sources/{sid}/analyze", headers=headers).status_code == 404
    assert raw.post(f"{PREFIX}/candidates/{cid}/decide", json=decision(), headers=headers).status_code == 404
    assert raw.patch(f"{PREFIX}/tasks/{t['id']}", json=update_payload(t), headers=headers).status_code == 404
    assert raw.post(PREFIX + "/sources", json={"title": "x", "content": "x", "user_id": owner}, headers=headers).status_code == 404
    with user_scope(other):
        assert store.list_tasks() == []
        with pytest.raises(NotFound):
            store.task_history(t["id"])
        with memory_session() as session:
            assert session.get(store.ArrangementTask, t["id"]) is None


def test_unauthenticated_endpoints_fail_closed(monkeypatch):
    with RawTestClient(app) as c:
        assert c.get(PREFIX + "/tasks").status_code == 401
        monkeypatch.delenv("AUTH_SECRET")
        assert c.get(PREFIX + "/sources").status_code == 503


@pytest.mark.parametrize("private", ["我的密码是huntersecret123", "手机号13911112222"])
def test_sensitive_source_is_rejected_before_storage(raw, private):
    assert raw.post(PREFIX + "/sources", json={"title": "信息", "content": private}).status_code == 422
    assert data(raw.get(PREFIX + "/sources")) == []


def test_no_model_is_explicit_and_manual_flow_remains_available(raw, monkeypatch):
    monkeypatch.setattr(gateway, "has_llm", lambda **k: False)
    monkeypatch.setattr(gateway, "get_llm_override", lambda: None)
    s = source(raw)
    response = raw.post(f"{PREFIX}/sources/{s['id']}/analyze")
    assert response.status_code == 503 and response.json()["detail"]["code"] == "model_unavailable"
    assert data(raw.get(f"{PREFIX}/sources/{s['id']}"))["analysis_status"] == "manual"
    assert candidate(raw, s["id"])["status"] == "pending"


def test_model_budget_blocks_before_processing(raw, monkeypatch):
    from types import SimpleNamespace
    from app.arrangements import service
    monkeypatch.setattr(service, "model_ready", lambda: True)
    monkeypatch.setattr(service.cost_ledger, "check", lambda uid: SimpleNamespace(ok=False))
    s = source(raw)
    assert raw.post(f"{PREFIX}/sources/{s['id']}/analyze").status_code == 429
    assert data(raw.get(f"{PREFIX}/sources/{s['id']}"))["analysis_status"] == "manual"


def test_profile_sandbox_values_never_enter_source_store(raw, monkeypatch):
    monkeypatch.setenv("PRIVACY_SANDBOX_VALUES", "opaque-private-sample")
    assert raw.post(PREFIX + "/sources", json={"title": "信息", "content": "这里包含opaque-private-sample"}).status_code == 422
    assert data(raw.get(PREFIX + "/sources")) == []


def test_model_extracts_only_pending_items_and_does_not_repeat(raw, monkeypatch):
    calls = []
    def override(*args):
        calls.append(args)
        return {"summary": "用户负责提纲，访谈只是建议。", "candidates": [
            {"title": "整理提纲", "quote": "我负责整理提纲。", "owner": "self", "deadline_text": ""},
            {"title": "访谈建议", "quote": "建议团队开展访谈。", "owner": "reference", "deadline_text": ""},
        ]}
    monkeypatch.setattr(gateway, "_override", override)
    s = source(raw)
    result = data(raw.post(f"{PREFIX}/sources/{s['id']}/analyze"))
    assert result["analysis_status"] == "ready" and len(result["candidates"]) == 2
    assert len(calls) == 1 and data(raw.get(PREFIX + "/tasks")) == []
    data(raw.post(f"{PREFIX}/sources/{s['id']}/analyze"))
    assert len(calls) == 1


def test_invalid_model_evidence_persists_no_partial_analysis(raw, monkeypatch):
    monkeypatch.setattr(gateway, "_override", lambda *a: {"summary": "摘要", "candidates": [
        {"title": "整理提纲", "quote": "我负责整理提纲。", "owner": "self", "deadline_text": ""},
        {"title": "虚构", "quote": "不存在的引文", "owner": "self", "deadline_text": ""},
    ]})
    s = source(raw)
    assert raw.post(f"{PREFIX}/sources/{s['id']}/analyze").status_code == 502
    loaded = data(raw.get(f"{PREFIX}/sources/{s['id']}"))
    assert loaded["analysis_status"] == "failed" and loaded["candidates"] == [] and not loaded["summary"]


def test_concurrent_confirm_creates_one_task(raw, owner):
    c = candidate(raw, source(raw)["id"])
    from app.arrangements.schemas import Decision
    def confirm():
        with user_scope(owner):
            return store.decide(c["id"], Decision(**decision()))["task"]["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: confirm(), range(2)))
    assert ids[0] == ids[1] and len(data(raw.get(PREFIX + "/tasks"))) == 1
