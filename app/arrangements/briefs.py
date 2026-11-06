"""Opt-in briefs reuse the existing scheduler and only write to the in-app inbox."""
import logging
import time

from sqlalchemy import select

from app.data_access import current_user, user_scope
from app.data_access import followup
from app.data_access.arrangements import now
from app.data_access.session import memory_session

logger = logging.getLogger("app.arrangements.briefs")


def run_due(cfg, *, started):
    if current_user() is not None:
        raise PermissionError("Brief dispatch requires the trusted system scheduler")
    clock = now()
    with memory_session() as session:
        owners = list(session.scalars(select(followup.BriefPreference.user_id).where(
            followup.BriefPreference.enabled.is_(True), followup.BriefPreference.next_run_at <= clock)
            .order_by(followup.BriefPreference.next_run_at).limit(cfg.max_users_per_tick)))
    generated, errors = 0, 0
    for owner in owners:
        if time.perf_counter() - started >= cfg.soft_budget_seconds:
            break
        try:
            with user_scope(owner):
                generated += int(followup.scheduled_brief(clock))
        except Exception as exc:
            errors += 1
            logger.warning("in-app brief failed (%s); no external delivery attempted", type(exc).__name__)
    return {"generated": generated, "errors": errors}
