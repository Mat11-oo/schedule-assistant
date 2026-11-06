"""Owned SQL text search, root-level tags and reproducible evidence chunks."""
import hashlib
import json
import re
import uuid

from sqlalchemy import Integer, String, Text, UniqueConstraint, case, func, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from app import db
from app.data_access import NotFound, current_user
from app.data_access.arrangements import ArrangementSource, Conflict, iso, now, owned
from app.data_access.materials import MaterialAsset, MaterialJob, MaterialUnit, MaterialVersion
from app.data_access.session import memory_session
from app.library.schemas import tag_key
from app.planning import engine


class LibraryMetadata(db.Base):
    __tablename__ = "library_metadata"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # Root source id.
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class LibraryTag(db.Base):
    __tablename__ = "library_tags"
    __table_args__ = (UniqueConstraint("root_id", "tag_key", name="uq_library_root_tag"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    root_id: Mapped[str] = mapped_column(String(32), index=True)
    tag_key: Mapped[str] = mapped_column(String(90), index=True)
    label: Mapped[str] = mapped_column(String(30))


class LibraryWorkspace(db.Base):
    __tablename__ = "library_workspaces"
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(Text)


def workspace_in_session(session, *, lock=False):
    statement = select(LibraryWorkspace).where(LibraryWorkspace.user_id == current_user())
    if lock:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    data = json.loads(row.payload_json) if row else {"folders": [], "entries": {}}
    if data["entries"]:
        visible = set(session.scalars(select(ArrangementSource.id).where(
            ArrangementSource.user_id == current_user(), ArrangementSource.id.in_(data["entries"]), *source_filters("all"))))
        data["entries"] = {root: entry for root, entry in data["entries"].items() if root in visible}
    return row, {**data, "version": row.version if row else 0}


def workspace():
    db.init_db()
    with memory_session() as session:
        return workspace_in_session(session)[1]


def update_workspace(body):
    from sqlalchemy.exc import IntegrityError
    try:
        with db.memory_transaction() as session:
            row, state = workspace_in_session(session, lock=True)
            if state["version"] != body.version:
                raise Conflict("工作空间已在其他页面更新，请刷新后重试")
            folders = state["folders"]
            indexed = {item["id"]: item for item in folders}
            folder = indexed.get(body.folder_id)
            if body.action in {"rename_folder", "move_folder", "remove_folder"} and folder is None:
                raise NotFound()
            if body.parent_id and body.parent_id not in indexed:
                raise NotFound()
            if body.action in {"create_folder", "rename_folder"}:
                if not body.name:
                    raise ValueError("请填写文件夹名称")
                parent = body.parent_id
                if any(item["id"] != (body.folder_id if body.action == "rename_folder" else None) and item["parent_id"] == parent and tag_key(item["name"]) == tag_key(body.name) for item in folders):
                    raise ValueError("此位置已有同名文件夹")
            if body.action == "create_folder":
                if len(folders) >= 200:
                    raise ValueError("当前工作空间最多支持 200 个文件夹")
                folders.append({"id": uuid.uuid4().hex, "name": body.name, "parent_id": body.parent_id})
            elif body.action == "rename_folder":
                parent = body.parent_id
                while parent:
                    if parent == body.folder_id:
                        raise ValueError("文件夹不能移入自己或自己的子文件夹")
                    parent = indexed[parent]["parent_id"]
                folder["name"] = body.name
                folder["parent_id"] = body.parent_id
            elif body.action == "move_folder":
                parent = body.parent_id
                while parent:
                    if parent == body.folder_id:
                        raise ValueError("文件夹不能移入自己或自己的子文件夹")
                    parent = indexed[parent]["parent_id"]
                if any(item["id"] != body.folder_id and item["parent_id"] == body.parent_id and tag_key(item["name"]) == tag_key(folder["name"]) for item in folders):
                    raise ValueError("目标位置已有同名文件夹")
                folder["parent_id"] = body.parent_id
            elif body.action == "remove_folder":
                root_names = {tag_key(item["name"]) for item in folders if not item["parent_id"] and item["id"] != body.folder_id}
                if any(tag_key(item["name"]) in root_names for item in folders if item["parent_id"] == body.folder_id):
                    raise ValueError("根目录有同名子文件夹，请先重命名或移走子文件夹")
                # Contents return to Unsorted. Child folders retain their identity.
                for item in folders:
                    if item["parent_id"] == body.folder_id:
                        item["parent_id"] = ""
                for entry in state["entries"].values():
                    if entry.get("folder_id") == body.folder_id:
                        entry["folder_id"] = ""
                folders.remove(folder)
            else:
                _, root, _, _ = resolve(session, body.source_id)
                needs_entry = bool(body.folder_id) if body.action == "move_source" else body.starred
                if needs_entry and root not in state["entries"] and len(state["entries"]) >= 10000:
                    raise ValueError("已达到当前工作空间的整理数量上限")
                entry = state["entries"].setdefault(root, {"folder_id": "", "starred": False})
                if body.action == "move_source":
                    if body.folder_id and folder is None:
                        raise NotFound()
                    entry["folder_id"] = body.folder_id
                else:
                    entry["starred"] = body.starred
                if not entry["folder_id"] and not entry["starred"]:
                    del state["entries"][root]
            state["version"] += 1
            if row is None:
                row = LibraryWorkspace(user_id=current_user())
                session.add(row)
            row.version = state["version"]
            row.payload_json = json.dumps({"folders": folders, "entries": state["entries"]}, ensure_ascii=False)
            return state
    except IntegrityError:
        raise Conflict("工作空间已在其他页面创建，请刷新后重试") from None


def resolve(session, source_id, *, lock=False):
    source = owned(session, ArrangementSource, source_id)
    version = session.get(MaterialVersion, source_id)
    root_id = version.root_id if version else source_id
    asset = session.get(MaterialAsset, root_id)
    if asset:
        asset = owned(session, MaterialAsset, root_id, lock=lock)
        if asset.deleted:
            raise NotFound()
    if lock:
        owned(session, ArrangementSource, root_id, lock=True)
    return source, root_id, version, asset


def metadata_in_session(session, source_id):
    source, root, version, asset = resolve(session, source_id)
    row = session.get(LibraryMetadata, root)
    tags = session.scalars(select(LibraryTag).where(LibraryTag.root_id == root).order_by(LibraryTag.tag_key))
    return {"source_id": source.id, "root_id": root, "version": row.version if row else 0,
            "tags": [t.label for t in tags], "source_version": version.number if version else 0,
            "is_history": bool(asset and asset.current_source_id != source.id)}


def metadata(source_id):
    db.init_db()
    with memory_session() as session:
        return metadata_in_session(session, source_id)


def save_tags(source_id, body):
    with db.memory_transaction() as session:
        _, root, _, _ = resolve(session, source_id, lock=True)
        row = session.get(LibraryMetadata, root)
        if (row.version if row else 0) != body.version:
            raise Conflict("资料标签已在其他页面更新，请刷新后再保存")
        if row is None:
            row = LibraryMetadata(id=root, user_id=current_user(), version=0)
            session.add(row)
        previous = {t.tag_key: t for t in session.scalars(select(LibraryTag).where(LibraryTag.root_id == root))}
        desired = {tag_key(label): label for label in body.tags}
        for key, tag in previous.items():
            if key not in desired:
                session.delete(tag)
            else:
                tag.label = desired[key]
        for key, label in desired.items():
            if key not in previous:
                identity = hashlib.sha256((root + "\0" + key).encode()).hexdigest()
                session.add(LibraryTag(id=identity, user_id=current_user(), root_id=root, tag_key=key, label=label))
        row.version += 1
        session.flush()
        return metadata_in_session(session, source_id)


def deleted_versions():
    return select(MaterialVersion.id).join(MaterialAsset, MaterialAsset.id == MaterialVersion.root_id).where(MaterialAsset.deleted.is_(True))


def deleted_roots():
    return select(MaterialAsset.id).where(MaterialAsset.deleted.is_(True))


def source_filters(scope):
    predicates = [ArrangementSource.id.not_in(deleted_roots()), ArrangementSource.id.not_in(deleted_versions())]
    old = select(MaterialVersion.id).join(MaterialAsset, MaterialAsset.id == MaterialVersion.root_id).where(
        MaterialAsset.current_source_id != MaterialVersion.id, MaterialAsset.deleted.is_(False))
    if scope == "latest":
        predicates.append(ArrangementSource.id.not_in(old))
    elif scope == "history":
        predicates.append(ArrangementSource.id.in_(old))
    return predicates


def escaped(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def contains(column, value):
    return column.ilike("%" + escaped(value) + "%", escape="\\")


def excerpt(value, terms, absolute=0, *, unit_id=None, locator=None, kind="content"):
    found = []
    for term in terms:
        match = re.search(re.escape(term), value, re.IGNORECASE)
        if match:
            found.append(match)
    position = min((m.start() for m in found), default=0)
    start = max(0, position - 60)
    end = min(len(value), max(start + 260, position + max((len(t) for t in terms), default=0)))
    return {"kind": kind, "quote": value[start:end], "matched": bool(found),
            "offset_unit": "unicode_codepoint" if kind == "content" else None,
            "start_char": absolute + start if kind == "content" else None,
            "end_char": absolute + end if kind == "content" else None,
            "unit_id": unit_id, "locator": locator or {}, "has_before": start > 0, "has_after": end < len(value)}


def hits(session, source, terms):
    units = select(MaterialUnit).where(MaterialUnit.source_id == source.id)
    if terms:
        units = units.where(or_(*(contains(MaterialUnit.text, term) for term in terms)))
    rows = session.scalars(units.order_by(MaterialUnit.ordinal).limit(3 if terms else 1)).all()
    result = [excerpt(u.text, terms, u.start_char, unit_id=u.id, locator=json.loads(u.locator_json)) for u in rows]
    if not result and source.content:
        item = excerpt(source.content, terms)
        if item["matched"] or not terms:
            result.append(item)
    if terms and any(re.search(re.escape(t), source.summary, re.IGNORECASE) for t in terms):
        result.append(excerpt(source.summary, terms, kind="summary"))
    if terms and not result and any(re.search(re.escape(t), source.title, re.IGNORECASE) for t in terms):
        result.append(excerpt(source.title, terms, kind="title"))
    return result[:3]


def browse(*, query="", kind="all", status="all", tag="", scope="latest", start_date=None, end_date=None, offset=0, limit=20, folder_id="all", starred=False, sort="recent"):
    db.init_db()
    query = query.strip()
    if "\0" in query:
        raise ValueError("请使用可见文字检索")
    terms = list(dict.fromkeys(query.split()))
    if len(terms) > 8:
        raise ValueError("每次最多输入 8 个检索词，请缩短搜索内容")
    predicates = source_filters(scope)
    for term in terms:
        predicates.append(or_(contains(ArrangementSource.title, term), contains(ArrangementSource.content, term), contains(ArrangementSource.summary, term)))
    if kind in {"pdf", "audio", "web"}:
        versions = select(MaterialVersion.id).join(MaterialAsset, MaterialAsset.id == MaterialVersion.root_id).where(MaterialAsset.kind == kind)
        roots = select(MaterialAsset.id).where(MaterialAsset.kind == kind)
        predicates.append(or_(ArrangementSource.id.in_(versions), ArrangementSource.id.in_(roots)))
    elif kind != "all":
        predicates.extend([ArrangementSource.kind == kind, ArrangementSource.id.not_in(select(MaterialVersion.id)),
                           ArrangementSource.id.not_in(select(MaterialAsset.id))])
    job_state = select(MaterialJob.state).where(MaterialJob.source_id == ArrangementSource.id).order_by(
        MaterialJob.created_at.desc(), MaterialJob.id.desc()).limit(1).scalar_subquery()
    running = or_(job_state.in_(["queued", "running"]), ArrangementSource.analysis_status == "processing")
    if status == "processing":
        predicates.append(running)
    elif status == "attention":
        predicates.append(or_(job_state.in_(["failed", "partial", "cancelled", "awaiting_configuration", "awaiting_consent", "awaiting_review"]),
                              ArrangementSource.analysis_status.in_(["failed", "partial"])))
    elif status == "ready":
        predicates.append(ArrangementSource.analysis_status == "ready")
    elif status == "pending":
        predicates.extend([ArrangementSource.analysis_status.in_(["manual", "extracted"]), or_(job_state.is_(None), job_state.not_in(["queued", "running"]))])
    if tag:
        roots = select(LibraryTag.root_id).where(LibraryTag.tag_key == tag_key(tag))
        versions = select(MaterialVersion.id).where(MaterialVersion.root_id.in_(roots))
        predicates.append(or_(ArrangementSource.id.in_(roots), ArrangementSource.id.in_(versions)))
    if start_date and end_date and start_date > end_date:
        raise ValueError("保存日期的结束时间应晚于开始时间")
    try:
        if start_date:
            predicates.append(ArrangementSource.created_at >= engine.bounds(start_date, start_date)[0])
        if end_date:
            predicates.append(ArrangementSource.created_at < engine.bounds(end_date, end_date)[1])
    except (OverflowError, ValueError):
        raise ValueError("请调整保存日期范围") from None
    with memory_session() as session:
        state = workspace_in_session(session)[1]
        if folder_id not in {"all", "unsorted"} and not any(item["id"] == folder_id for item in state["folders"]):
            raise NotFound()
        def related_sources(roots):
            versions = select(MaterialVersion.id).where(MaterialVersion.root_id.in_(roots))
            return or_(ArrangementSource.id.in_(roots), ArrangementSource.id.in_(versions))
        if folder_id == "unsorted":
            assigned = [root for root, entry in state["entries"].items() if entry.get("folder_id")]
            predicates.append(~related_sources(assigned))
        elif folder_id != "all":
            predicates.append(related_sources([root for root, entry in state["entries"].items() if entry.get("folder_id") == folder_id]))
        if starred:
            predicates.append(related_sources([root for root, entry in state["entries"].items() if entry.get("starred")]))
        total = session.scalar(select(func.count(ArrangementSource.id)).where(*predicates)) or 0
        statement = select(ArrangementSource).where(*predicates)
        if query:
            rank = case((contains(ArrangementSource.title, query), 0), (contains(ArrangementSource.summary, query), 1), else_=2)
            statement = statement.order_by(rank)
        order = ArrangementSource.title.asc() if sort == "title" else ArrangementSource.created_at.asc() if sort == "oldest" else ArrangementSource.created_at.desc()
        rows = session.scalars(statement.order_by(order, ArrangementSource.id.desc()).offset(offset).limit(limit)).all()
        result = []
        for source in rows:
            _, root, version, asset = resolve(session, source.id)
            meta = metadata_in_session(session, source.id)
            latest_job = session.scalar(select(MaterialJob).where(MaterialJob.source_id == source.id).order_by(MaterialJob.created_at.desc(), MaterialJob.id.desc()).limit(1))
            result.append({"id": source.id, "root_id": root, "title": source.title, "kind": asset.kind if asset else source.kind,
                           "analysis_status": source.analysis_status, "job_state": latest_job.state if latest_job else None,
                           "job_stage": latest_job.stage if latest_job else "", "created_at": iso(source.created_at),
                           "original_at": source.original_at, "tags": meta["tags"], "source_version": version.number if version else 0,
                           "is_history": meta["is_history"], "workspace": state["entries"].get(root, {"folder_id": "", "starred": False}), "hits": hits(session, source, terms), "partial": bool(version and version.partial)})
        next_offset = offset + len(result)
        return {"items": result, "total": total, "offset": offset, "limit": limit,
                "next_offset": next_offset if next_offset < total and next_offset <= 10000 else None,
                "query": query, "terms": terms, "scope": scope, "generated_at": now().isoformat(),
                "coverage": "检索已保存的标题、摘要和已提取原文，按空格分词并同时匹配各词。"}


def catalog():
    db.init_db()
    with memory_session() as session:
        statement = select(LibraryTag.tag_key, func.min(LibraryTag.label), func.count(LibraryTag.id)).where(
            LibraryTag.root_id.not_in(deleted_roots())).group_by(LibraryTag.tag_key).order_by(LibraryTag.tag_key).limit(501)
        rows = session.execute(statement).all()
        return {"tags": [{"key": key, "label": label, "count": count} for key, label, count in rows[:500]], "truncated": len(rows) > 500}


def chunk_manifest(source_id, *, offset=0, limit=50):
    db.init_db()
    with memory_session() as session:
        source, root, version, asset = resolve(session, source_id)
        units = session.scalars(select(MaterialUnit).where(MaterialUnit.source_id == source_id).order_by(MaterialUnit.ordinal)).all()
        segments = [(u.id, u.text, u.start_char, json.loads(u.locator_json)) for u in units]
        if not segments and source.content:
            segments = [(None, source.content, 0, {})]
        items = []
        ordinal = 0
        for unit_id, content, absolute, locator in segments:
            for position in range(0, len(content), 1800):
                if ordinal >= offset and len(items) <= limit:
                    value = content[position:position + 2000]
                    start = absolute + position
                    identity = hashlib.sha256(f"{source_id}:{start}:{value}".encode()).hexdigest()
                    items.append({"id": identity, "source_id": source_id, "unit_id": unit_id, "text": value,
                                  "start_char": start, "end_char": start + len(value), "locator": locator})
                ordinal += 1
                if position + 2000 >= len(content):
                    break
        more = len(items) > limit
        return {"source_id": source.id, "root_id": root, "source_version": version.number if version else 0,
                "source_digest": hashlib.sha256(source.content.encode()).hexdigest(), "is_history": bool(asset and asset.current_source_id != source.id),
                "offset_unit": "unicode_codepoint",
                "chunk_size": 2000, "overlap": 200, "items": items[:limit], "total": ordinal,
                "next_offset": offset + limit if more else None, "stage": "chunks_ready" if ordinal else "awaiting_text"}


def purge(session, source_ids):
    for model, column in ((LibraryTag, LibraryTag.root_id), (LibraryMetadata, LibraryMetadata.id)):
        for row in session.scalars(select(model).where(column.in_(source_ids))):
            session.delete(row)
