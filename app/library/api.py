from datetime import date
from typing import Literal

from fastapi import APIRouter, Query

from app.arrangements.api import call
from app.arrangements.service import validate_private_text
from app.data_access import library as store
from app.gateway.config import ConfigError, get_config
from app.library.schemas import TagsInput, WorkspaceAction

router = APIRouter(prefix="/api/arrangements/library", tags=["personal-library"])


@router.get("/search")
def search(q: str = Query("", max_length=200), kind: Literal["all", "message", "document_text", "meeting_transcript", "pdf", "audio", "web"] = "all",
           status: Literal["all", "pending", "processing", "ready", "attention"] = "all", tag: str = Query("", max_length=30),
           scope: Literal["latest", "history", "all"] = "latest", start_date: date | None = None, end_date: date | None = None,
           offset: int = Query(0, ge=0, le=10000), limit: int = Query(20, ge=1, le=50),
           folder_id: str = Query("all", max_length=32), starred: bool = False, sort: Literal["recent", "oldest", "title"] = "recent"):
    return call(lambda: store.browse(query=q, kind=kind, status=status, tag=tag, scope=scope,
                                    start_date=start_date, end_date=end_date, offset=offset, limit=limit, folder_id=folder_id, starred=starred, sort=sort))


@router.get("/tags")
def tags():
    return call(store.catalog)


@router.get("/sources/{source_id}/metadata")
def metadata(source_id: str):
    return call(store.metadata, source_id)


@router.put("/sources/{source_id}/tags")
def save_tags(source_id: str, body: TagsInput):
    def save():
        validate_private_text(*body.tags)
        return store.save_tags(source_id, body)
    return call(save)


@router.get("/sources/{source_id}/chunks")
def chunks(source_id: str, offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=100)):
    return call(lambda: store.chunk_manifest(source_id, offset=offset, limit=limit))


def vector_configuration():
    try:
        spec = get_config().embedding_model("knowledge_embed")
    except ConfigError:
        return {"task": "knowledge_embed", "provider_configured": False, "model": "", "index_ready": False,
                "stage": "awaiting_configuration", "manifest_ready": True}
    return {"task": "knowledge_embed", "provider_configured": spec.has_key(), "model": spec.model,
            "index_ready": False, "stage": "manifest_ready", "manifest_ready": True}


@router.get("/vector-status")
def vector_status():
    return call(vector_configuration)


@router.get("/workspace")
def workspace():
    return call(store.workspace)


@router.post("/workspace")
def update_workspace(body: WorkspaceAction):
    def save():
        validate_private_text(body.name)
        return store.update_workspace(body)
    return call(save)
