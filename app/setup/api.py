import os
from pathlib import Path
import shlex
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select

from app import __version__, db
from app.arrangements.api import call
from app.data_access import followup, library, planning
from app.data_access.arrangements import ArrangementSource, ArrangementTask, now
from app.data_access.session import memory_session
from app.gateway.config import get_config
from app.materials import providers, worker
from app.setup.model_settings import SettingsInput

router = APIRouter(prefix="/api/arrangements/setup", tags=["setup"])


def endpoint_host(base):
    try:
        parsed = urlsplit(base)
        valid = (parsed.scheme in {"https", "http"} and bool(parsed.hostname)
                 and not (parsed.username or parsed.password or parsed.query or parsed.fragment))
        parsed.port  # Validate an explicitly supplied port.
        return parsed.hostname or "", valid
    except ValueError:
        return "", False


def text_configuration():
    try:
        config = get_config()
        routes = [
            ("对话", config.task_chain("chat", "fast")),
            ("资料整理", config.task_chain("summary", "fast")),
            ("复杂任务", config.chain("strong")),
        ]
        roles = []
        for label, chain in routes:
            models = []
            for model in chain:
                host, valid = endpoint_host(model.base_url)
                models.append({"model": model.model, "host": host,
                               "configured": bool(model.model and valid and model.has_key())})
            roles.append({"label": label, "configured": any(m["configured"] for m in models), "models": models})
        return {"status": "configured" if all(r["configured"] for r in roles) else "incomplete", "roles": roles}
    except Exception:
        return {"status": "review", "roles": []}


def media_configuration(prefix):
    base = os.getenv(f"{prefix}_BASE_URL", "").strip()
    model = os.getenv(f"{prefix}_MODEL", "").strip()
    host, valid = endpoint_host(base)
    protocol_valid = prefix != "ASR" or os.getenv("ASR_PROVIDER", "openai").strip() in {"openai", "dashscope"}
    if (base and not valid) or not protocol_valid:
        status = "review"
    elif valid and model and providers.configured(prefix):
        status = "configured"
    else:
        status = "incomplete"
    return {"status": status, "host": host if valid else "", "model": model}


def overview():
    from app.agent.proactive.scheduler import status as scheduler_status
    from app.library.api import vector_configuration

    db.init_db()
    try:
        vector = vector_configuration()
    except Exception:
        vector = {"provider_configured": False, "index_ready": False, "model": "", "stage": "review"}
    with memory_session() as session:
        calendar = session.scalar(select(planning.PlanningCalendar))
        source_count = session.scalar(select(func.count(ArrangementSource.id)).where(*library.source_filters("latest"))) or 0
        task_count = session.scalar(select(func.count(ArrangementTask.id))) or 0
        block_count = session.scalar(select(func.count(planning.PlanningBlock.id))) or 0
        brief_preferences = followup.preference(session)
    return {
        "generated_at": now().isoformat(), "version": __version__,
        "services": {"text": text_configuration(), "ocr": media_configuration("OCR"),
                     "asr": media_configuration("ASR"), "vector": vector},
        "background": {"reminders": scheduler_status(), "materials": worker.status()},
        "personal": {"work_hours_configured": bool(calendar and calendar.configured),
                     "brief_enabled": brief_preferences["enabled"], "brief_at": brief_preferences["at"],
                     "brief_next_run_at": brief_preferences["next_run_at"]},
        "progress": {"source_saved": source_count > 0, "task_confirmed": task_count > 0,
                     "work_hours_saved": bool(calendar and calendar.configured), "plan_saved": block_count > 0},
        "storage": {"backend": db.ENGINE.dialect.name, "data_dir": str(db.DATA_DIR)},
        "configure_command": "cd " + shlex.quote(str(Path(__file__).resolve().parents[2])) + "\n.venv/bin/python scripts/configure_bailian.py",
        "calendar_connected": False,
    }


@router.get("")
def setup(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return call(overview)


def local_operator(request):
    from fastapi import HTTPException
    from app.data_access import current_user
    current_user()
    host = request.url.hostname
    origin = request.headers.get('origin')
    expected = f'{request.url.scheme}://{request.url.netloc}'
    if (request.client is None or request.client.host not in {'127.0.0.1', '::1'}
            or host not in {'localhost', '127.0.0.1', '::1'}
            or origin and origin != expected
            or request.headers.get('sec-fetch-site') == 'cross-site'):
        raise HTTPException(403, detail={'message': '模型配置仅允许本机应用访问'})


@router.get('/models')
def model_settings(request: Request, response: Response):
    from app.setup.model_settings import snapshot
    local_operator(request)
    response.headers['Cache-Control'] = 'no-store'
    return call(snapshot)


@router.put('/models')
def save_model_settings(body: SettingsInput, request: Request, response: Response):
    from app.setup.model_settings import save
    local_operator(request)
    response.headers['Cache-Control'] = 'no-store'
    try:
        return call(save, body)
    except OSError:
        from fastapi import HTTPException
        raise HTTPException(500, detail={'message': '本机配置保存失败，请检查文件权限'}) from None
