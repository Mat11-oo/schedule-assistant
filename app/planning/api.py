"""Local calendar API. Never writes to an external calendar or calls a model."""
from datetime import date, timezone

from fastapi import APIRouter
from fastapi.responses import Response

from app.arrangements.api import call
from app.arrangements.service import validate_private_text
from app.data_access import planning as store
from app.planning.schemas import Confirmed, EventCreate, EventUpdate, LockInput, MoveInput, PlanInput, Preferences, Timing, VersionAction

router = APIRouter(prefix="/api/arrangements/planning", tags=["planning"])


@router.get("/state")
def state(start_date: date | None = None, end_date: date | None = None):
    return call(store.state, start_date, end_date)


@router.put("/preferences")
def preferences(body: Preferences):
    return call(store.save_preferences, body)


@router.post("/events")
def create_event(body: EventCreate):
    def save():
        validate_private_text(body.title, body.note)
        return store.save_event(body)
    return call(save)


@router.patch("/events/{event_id}")
def update_event(event_id: str, body: EventUpdate):
    def save():
        validate_private_text(body.title, body.note)
        return store.save_event(body, event_id)
    return call(save)


@router.post("/events/{event_id}/cancel")
def cancel_event(event_id: str, body: VersionAction):
    return call(store.cancel_event, event_id, body)


@router.put("/tasks/{task_id}/timing")
def timing(task_id: str, body: Timing):
    return call(store.save_timing, task_id, body)


@router.post("/proposals")
def preview(body: PlanInput):
    return call(store.preview, body)


@router.get("/proposals/{proposal_id}")
def proposal(proposal_id: str):
    return call(store.proposal, proposal_id)


@router.post("/proposals/{proposal_id}/confirm")
def confirm(proposal_id: str, body: Confirmed):
    return call(store.confirm, proposal_id)


@router.post("/proposals/{proposal_id}/dismiss")
def dismiss(proposal_id: str, body: Confirmed):
    return call(store.dismiss, proposal_id)


@router.post("/blocks/{block_id}/move-preview")
def move(block_id: str, body: MoveInput):
    def propose():
        validate_private_text(body.reason)
        return store.move_preview(block_id, body)
    return call(propose)


@router.patch("/blocks/{block_id}/lock")
def lock(block_id: str, body: LockInput):
    return call(store.block_action, block_id, body)


@router.post("/blocks/{block_id}/cancel")
def cancel(block_id: str, body: VersionAction):
    return call(lambda: store.block_action(block_id, body, cancel=True))


@router.get("/history")
def history():
    return call(store.history)


def _escape(value):
    return str(value).replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def _fold(line):
    result, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            result.append(current)
            current = " "
        current += char
    return "\r\n".join(result + [current])


@router.get("/export.ics")
def export(start_date: date | None = None, end_date: date | None = None):
    data = call(store.state, start_date, end_date)["data"]
    def stamp(value):
        return store.engine.dt(value).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Personal Agent//Local Planning//ZH", "CALSCALE:GREGORIAN"]
    start, end = store.engine.bounds(date.fromisoformat(data["start_date"]), date.fromisoformat(data["end_date"]))
    for kind, rows in (("event", data["events"]), ("block", data["blocks"])):
        for row in rows:
            if not store.engine.overlap(store.engine.dt(row["start_at"]), store.engine.dt(row["end_at"]), start, end):
                continue
            lines += ["BEGIN:VEVENT", f"UID:{kind}-{row['id']}@personal-agent.local",
                      "DTSTAMP:" + stamp(store.now()), "DTSTART:" + stamp(row["start_at"]), "DTEND:" + stamp(row["end_at"]),
                      "SUMMARY:" + _escape(row["title"]), f"SEQUENCE:{row['version']}",
                      "DESCRIPTION:" + _escape("从 Personal Agent 手动导出；不代表已与外部日历同步。"), "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(_fold(line) for line in lines) + "\r\n", media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="personal-agent.ics"', "Cache-Control": "no-store"})
