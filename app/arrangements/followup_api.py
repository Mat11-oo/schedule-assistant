"""Session-bound brief and source-change endpoints."""
from fastapi import APIRouter

from app.arrangements.api import call
from app.arrangements.followup_schemas import BriefPreferences, Distinct, SourceChange
from app.arrangements.service import validate_private_text
from app.data_access import followup as store
from app.planning.schemas import Confirmed

router = APIRouter(prefix="/api/arrangements/followup", tags=["arrangement-followup"])


@router.get("/brief")
def brief():
    return call(store.brief_state)


@router.put("/brief-preferences")
def preferences(body: BriefPreferences):
    return call(store.save_preferences, body)


@router.post("/briefs")
def save_brief(body: Confirmed):
    return call(store.save_brief)


@router.get("/briefs/{brief_id}")
def saved_brief(brief_id: str):
    return call(store.saved_brief, brief_id)


@router.get("/candidates/{candidate_id}/matches")
def matches(candidate_id: str):
    return call(store.matches, candidate_id)


@router.post("/candidates/{candidate_id}/distinct")
def distinct(candidate_id: str, body: Distinct):
    return call(store.distinct, candidate_id, body)


@router.post("/candidates/{candidate_id}/updates")
def propose_update(candidate_id: str, body: SourceChange):
    def propose():
        validate_private_text(body.title, body.reason)
        return store.propose_update(candidate_id, body)
    return call(propose)


@router.get("/updates")
def updates():
    return call(store.updates)


@router.post("/updates/{proposal_id}/confirm")
def confirm(proposal_id: str, body: Confirmed):
    return call(store.confirm_update, proposal_id)


@router.post("/updates/{proposal_id}/dismiss")
def dismiss(proposal_id: str, body: Confirmed):
    return call(store.dismiss_update, proposal_id)
