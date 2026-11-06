"""Synthetic files, real PDF/audio decoding and storage; cloud outputs are test doubles."""
import io
import json
import threading
import uuid
import wave
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject

from app.auth import mint_user_token
from app.main import app
from app.data_access import user_scope, NotFound
from app.data_access.session import memory_session
from app.data_access import materials as store
from app.data_access.arrangements import now, Conflict
from app.materials import intelligence, parsers, providers, worker
from app.materials.schemas import OCRResult
from app.gateway import llm_gateway as gateway
from tests.conftest import RawTestClient

BASE = "/api/arrangements"
TEXT = "The final decision is to prepare the review outline before Friday. The team may consider interviews later."


def pdf_bytes(text=TEXT, *, image=False, pages=1, table=False):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        commands = f"BT /F1 10 Tf 30 750 Td ({text}) Tj ET".encode()
        if table:
            commands += b"\n30 600 m 330 600 l 330 680 l 30 680 l h S 30 640 m 330 640 l S 180 600 m 180 680 l S"
            commands += b"\nBT /F1 12 Tf 40 655 Td (Metric) Tj 150 0 Td (Value) Tj ET BT /F1 12 Tf 40 615 Td (Revenue) Tj 150 0 Td (120 USD) Tj ET"
        if image:
            bitmap = DecodedStreamObject(); bitmap.set_data(b"\xff\xff\xff" * 20 * 20)
            bitmap.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(20), NameObject("/Height"): NumberObject(20),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): writer._add_object(bitmap)})
            commands = b"q 550 0 0 700 20 20 cm /Im0 Do Q"
        page[NameObject("/Resources")] = resources
        stream = DecodedStreamObject(); stream.set_data(commands)
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO(); writer.write(out); return out.getvalue()


def wav_bytes(seconds=1):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * seconds)
    return out.getvalue()


@pytest.fixture
def owner(): return "media-" + uuid.uuid4().hex[:12]


@pytest.fixture
def raw(owner):
    with RawTestClient(app, headers={"X-User-Token": mint_user_token(owner)}) as c:
        yield c


def data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def upload(raw, content=None, name="report.pdf", allow=False):
    return data(raw.post(BASE + "/uploads", files={"file": (name, content if content is not None else pdf_bytes(), "application/octet-stream")},
                         data={"allow_cloud": str(allow).lower()}))


def detail(raw, sid): return data(raw.get(f"{BASE}/sources/{sid}"))


def run(root): assert worker.run_one(root)


def test_pdf_upload_real_text_tables_preview_and_dedup(raw):
    result = upload(raw, pdf_bytes(table=True))
    assert detail(raw, result["source_id"])["material"]["jobs"][0]["state"] == "queued"
    assert upload(raw, pdf_bytes(table=True))["reused"]
    run(result["root_id"])
    source = detail(raw, result["source_id"])
    m = source["material"]
    assert m["version"] == 1 and not m["partial"]
    assert m["jobs"][0]["done"] == m["jobs"][0]["total"] == 1
    assert TEXT in source["content"] and any(u["locator"].get("table") for u in m["units"])
    assert any("120 USD" in u["text"] for u in m["units"])
    for u in m["units"]: assert source["content"][u["start_char"]:u["end_char"]] == u["text"]
    image = raw.get(f"{BASE}/sources/{source['id']}/pages/1")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert raw.get(f"{BASE}/sources/{source['id']}/pages/200").status_code == 422
    assert raw.get(f"{BASE}/sources/{source['id']}/file").content == pdf_bytes(table=True)


def test_scanned_pdf_missing_configuration_preserves_and_resumes(raw, monkeypatch):
    monkeypatch.setattr(providers, "configured", lambda p: False)
    result = upload(raw, pdf_bytes(image=True), allow=True)
    run(result["root_id"])
    m = detail(raw, result["source_id"])["material"]
    job = m["jobs"][0]
    assert job["state"] == "awaiting_configuration" and m["units"] == []
    monkeypatch.setattr(providers, "configured", lambda p: True)
    monkeypatch.setattr(providers, "ocr", lambda *a: {"text": "Scanned final decision. Prepare the outline.", "tables": [], "uncertain": []})
    data(raw.post(f"{BASE}/jobs/{job['id']}/retry", json={"allow_cloud": True}))
    run(result["root_id"])
    m = detail(raw, result["source_id"])["material"]
    assert m["units"][0]["locator"]["method"] == "ocr"
    assert "Scanned final" in m["units"][0]["text"]


def test_cloud_consent_is_not_inferred_from_upload(raw, monkeypatch):
    monkeypatch.setattr(providers, "configured", lambda p: True)
    calls = []
    monkeypatch.setattr(providers, "ocr", lambda *a: calls.append(a))
    result = upload(raw, pdf_bytes(image=True))
    run(result["root_id"])
    assert detail(raw, result["source_id"])["material"]["jobs"][0]["state"] == "awaiting_consent"
    assert calls == []


def test_uncertain_cloud_request_needs_acknowledgement_before_retry(raw, monkeypatch):
    monkeypatch.setattr(providers, "configured", lambda p: True)
    calls = []
    def timeout(*a):
        calls.append(1); raise providers.UncertainRequest("合成超时，可能已计费")
    monkeypatch.setattr(providers, "ocr", timeout)
    result = upload(raw, pdf_bytes(image=True), allow=True)
    run(result["root_id"])
    job = detail(raw, result["source_id"])["material"]["jobs"][0]
    assert job["state"] == "awaiting_review" and len(calls) == 1
    assert raw.post(f"{BASE}/jobs/{job['id']}/retry", json={"allow_cloud": True}).status_code == 409
    monkeypatch.setattr(providers, "ocr", lambda *a: {"text": "Synthetic OCR succeeds after explicit retry.", "tables": [], "uncertain": []})
    data(raw.post(f"{BASE}/jobs/{job['id']}/retry", json={"allow_cloud": True, "acknowledge_uncertain": True}))
    run(result["root_id"])
    assert detail(raw, result["source_id"])["material"]["version"] == 1


def test_completed_pages_survive_later_ocr_failure(raw, monkeypatch):
    # First native page and second scanned page, in one real PDF.
    from pypdf import PdfReader
    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(pdf_bytes())).pages[0])
    writer.add_page(PdfReader(io.BytesIO(pdf_bytes(image=True))).pages[0])
    stream = io.BytesIO(); writer.write(stream)
    monkeypatch.setattr(providers, "configured", lambda p: False)
    result = upload(raw, stream.getvalue(), allow=True); run(result["root_id"])
    source = detail(raw, result["source_id"])
    assert TEXT in source["content"] and source["material"]["partial"]
    assert source["material"]["coverage"]["completed_pages"] == [1]
    assert source["material"]["coverage"]["issues"][0]["page"] == 2


def fake_audio(monkeypatch):
    monkeypatch.setattr(providers, "configured", lambda p: True)
    monkeypatch.setattr(providers, "transcribe", lambda *a: {"units": [
        {"text": "We decided to prepare the outline.", "locator": {"start": 0.0, "end": 0.5, "speaker": "part1_A", "precision": "segment", "part": 1}},
        {"text": "Lin will provide the data.", "locator": {"start": 0.5, "end": 1.0, "speaker": "part1_B", "precision": "segment", "part": 1}},
    ], "warnings": []})


def test_audio_real_decode_cloud_segments_range_and_revision(raw, monkeypatch):
    fake_audio(monkeypatch)
    result = upload(raw, wav_bytes(), name="meeting.wav", allow=True); run(result["root_id"])
    original = detail(raw, result["source_id"])
    assert original["material"]["coverage"]["duration_seconds"] == 1
    assert original["material"]["units"][0]["locator"]["speaker"] == "part1_A"
    audio = raw.get(f"{BASE}/sources/{original['id']}/file", headers={"Range": "bytes=0-99"})
    assert audio.status_code == 206 and len(audio.content) == 100
    first = original["material"]["units"][0]
    changed = data(raw.post(f"{BASE}/sources/{original['id']}/revisions", json={"version": 1, "confirmed": True,
        "edits": [{"unit_id": first["id"], "text": "We decided to prepare a short outline.", "speaker": "part1_A"}],
        "speaker_names": {"part1_A": "我", "part1_B": "小林"}, "self_speakers": ["part1_A"]}))
    assert changed["source_id"] != original["id"] and changed["version"] == 2
    new = detail(raw, changed["source_id"])
    assert "short outline" in new["content"] and "short outline" not in detail(raw, original["id"])["content"]
    assert new["material"]["self_speakers"] == ["part1_A"]
    assert len(data(raw.get(BASE + "/sources"))) == 1
    assert raw.post(f"{BASE}/sources/{original['id']}/revisions", json={"version": 1, "confirmed": True}).status_code == 409


def test_media_isolation_includes_files_images_jobs_and_units(raw, monkeypatch):
    result = upload(raw); run(result["root_id"])
    source = detail(raw, result["source_id"])
    headers = {"X-User-Token": mint_user_token("other-" + uuid.uuid4().hex)}
    for suffix in ["", "/file", "/pages/1"]:
        assert raw.get(f"{BASE}/sources/{source['id']}{suffix}", headers=headers).status_code == 404
    job = source["material"]["jobs"][0]
    assert raw.post(f"{BASE}/jobs/{job['id']}/retry", json={"allow_cloud": True}, headers=headers).status_code == 404
    assert raw.post(f"{BASE}/sources/{source['id']}/revisions", json={"version": 1, "confirmed": True}, headers=headers).status_code == 404


def test_stale_worker_is_fenced_and_cloud_request_not_auto_replayed(raw, owner):
    result = upload(raw)
    job = store.claim_next(result["root_id"])
    with user_scope(owner), memory_session() as session:
        row = session.get(store.MaterialJob, job["id"]); row.heartbeat_at = now() - timedelta(seconds=120)
    assert store.claim_next(result["root_id"]) is None
    with user_scope(owner):
        with pytest.raises(Conflict): store.progress(job["id"], job["claim_token"], stage="过期写入")
    assert detail(raw, result["source_id"])["material"]["jobs"][0]["state"] == "awaiting_review"


def mock_reading(monkeypatch):
    def override(kind, system, user, tier, fallback):
        payload = json.loads(user)
        if "self_speakers" in payload:
            unit = payload["units"][0]
            ev = [{"unit_id": unit["id"], "quote": unit["text"]}]
            return {"conclusions": [{"text": "准备评审提纲。", "evidence": ev, "origin": "source"}],
                "risks": [], "key_data": [], "topics": [], "decisions": [], "open_questions": [],
                "actions": [{"title": "准备提纲", "owner": "self", "assignee": "", "deadline_text": "", "evidence": ev}]}
        if "findings" in payload:
            return {"overview": payload["findings"][:1]}
        unit = payload["units"][0]
        return {"answer": "资料要求准备评审提纲。", "evidence": [{"unit_id": unit["id"], "quote": unit["text"]}], "missing": ""}
    monkeypatch.setattr(gateway, "_override", override)


def test_reading_questions_and_confirmed_task_keep_old_revision_evidence(raw, monkeypatch):
    result = upload(raw); run(result["root_id"])
    mock_reading(monkeypatch)
    data(raw.post(f"{BASE}/sources/{result['source_id']}/reading", json={"allow_cloud": True})); run(result["root_id"])
    source = detail(raw, result["source_id"])
    report = source["material"]["artifacts"][0]["data"]
    assert report["overview"] and report["coverage"]["completed_batches"] == report["coverage"]["total_batches"]
    c = source["candidates"][0]
    assert c["owner"] == "unknown" and c["evidence"][0]["unit_id"]
    task = data(raw.post(f"{BASE}/candidates/{c['id']}/decide", json={"title": c["title"], "action": "accept", "take_ownership": True, "confirmed": True}))["task"]
    request = {"question": "What is the final decision?", "allow_cloud": True, "request_id": "question-unique-001"}
    answer = data(raw.post(f"{BASE}/sources/{source['id']}/questions", json=request))
    assert answer["evidence"] and answer["searched_units"] == len(source["material"]["units"])
    assert data(raw.post(f"{BASE}/sources/{source['id']}/questions", json=request)) == answer
    edited = data(raw.post(f"{BASE}/sources/{source['id']}/revisions", json={"version": 1, "confirmed": True,
        "edits": [{"unit_id": source["material"]["units"][0]["id"], "text": "The deadline was corrected.", "speaker": ""}]}))
    assert edited["source_id"] != source["id"]
    task_after = data(raw.get(f"{BASE}/tasks/{task['id']}"))["task"]
    assert task_after["sources"][0]["source_id"] == source["id"]
    assert detail(raw, source["id"])["material"]["artifacts"][0]["data"] == report


def test_invalid_citation_cannot_publish_report_or_tasks(raw, monkeypatch):
    result = upload(raw); run(result["root_id"])
    def invalid(*a, **k):
        return {"conclusions": [{"text": "虚构", "evidence": [{"unit_id": "x" * 32, "quote": "不存在"}], "origin": "source"}],
            "risks": [], "key_data": [], "topics": [], "decisions": [], "open_questions": [], "actions": []}
    monkeypatch.setattr(gateway, "_override", invalid)
    data(raw.post(f"{BASE}/sources/{result['source_id']}/reading", json={"allow_cloud": True})); run(result["root_id"])
    source = detail(raw, result["source_id"])
    assert not source["candidates"] and not source["material"]["artifacts"]
    assert source["material"]["jobs"][0]["state"] == "failed"


def test_invented_assignee_cannot_be_published_as_a_fact(raw, monkeypatch):
    result = upload(raw); run(result["root_id"])
    mock_reading(monkeypatch)
    original = gateway.get_llm_override()
    def invent(kind, system, user, tier, fallback):
        value = original(kind, system, user, tier, fallback)
        if "actions" in value: value["actions"][0]["assignee"] = "未出现的人名"
        return value
    monkeypatch.setattr(gateway, "_override", invent)
    data(raw.post(f"{BASE}/sources/{result['source_id']}/reading", json={"allow_cloud": True})); run(result["root_id"])
    source = detail(raw, result["source_id"])
    assert not source["candidates"] and not source["material"]["artifacts"]
    assert "负责人缺少" in source["material"]["jobs"][0]["message"]


def test_invalid_formats_size_and_private_web_targets_are_rejected(raw, monkeypatch):
    assert raw.post(BASE + "/uploads", files={"file": ("fake.pdf", b"not a pdf")}).status_code == 422
    assert raw.post(BASE + "/uploads", files={"file": ("run.exe", b"content")}).status_code == 422
    monkeypatch.setenv("MATERIAL_MAX_PDF_MB", "0")
    assert raw.post(BASE + "/uploads", files={"file": ("too-big.pdf", pdf_bytes())}).status_code == 413
    assert raw.post(BASE + "/web-sources", json={"title": "private", "url": "http://127.0.0.1/secret"}).status_code == 422


def test_cancelled_worker_cannot_publish_late_results(raw, owner):
    result = upload(raw); job = store.claim_next(result["root_id"])
    data(raw.post(f"{BASE}/jobs/{job['id']}/cancel"))
    with user_scope(owner):
        with pytest.raises(Conflict): store.publish_parse(job["id"], job["claim_token"], [], {})


def test_web_snapshot_uses_same_version_and_evidence_flow(raw, monkeypatch):
    monkeypatch.setattr(parsers, "fetch_web", lambda *a: [{"text": "A synthetic public research page.", "locator": {"url": "https://example.org/paper"}}])
    result = data(raw.post(BASE + "/web-sources", json={"title": "网页", "url": "https://example.org/paper"}))
    run(result["root_id"])
    assert "synthetic public" in detail(raw, result["source_id"])["content"]


def test_delete_removes_all_versions_files_and_derived_text_but_keeps_task(raw, monkeypatch, owner):
    result = upload(raw); run(result["root_id"]); mock_reading(monkeypatch)
    data(raw.post(f"{BASE}/sources/{result['source_id']}/reading", json={"allow_cloud": True})); run(result["root_id"])
    source = detail(raw, result["source_id"])
    c = source["candidates"][0]
    task = data(raw.post(f"{BASE}/candidates/{c['id']}/decide", json={"title": "我的提纲任务", "action": "accept", "confirmed": True, "take_ownership": True}))["task"]
    with user_scope(owner):
        path = worker.stored_path(store.get_asset(source["id"])["storage_key"])
    assert path.exists()
    deleted = data(raw.request("DELETE", f"{BASE}/sources/{source['id']}/material", json={"confirmed": True}))
    assert deleted["deleted"] and not deleted["file_cleanup_pending"] and not path.exists()
    assert data(raw.get(BASE + "/sources")) == []
    assert raw.get(f"{BASE}/sources/{source['id']}/file").status_code == 404
    assert raw.get(f"{BASE}/sources/{source['id']}").status_code == 404
    history = data(raw.get(f"{BASE}/tasks/{task['id']}"))
    assert history["task"]["title"] == "我的提纲任务"
    assert history["task"]["sources"][0]["quote"] == "[来源已删除]"
    assert TEXT not in json.dumps(history, ensure_ascii=False)
    assert data(raw.request("DELETE", f"{BASE}/sources/{source['id']}/material", json={"confirmed": True}))["deleted"]


def test_upload_stream_is_limited_before_multipart_spooling(raw, monkeypatch):
    monkeypatch.setenv("MATERIAL_MAX_AUDIO_MB", "0"); monkeypatch.setenv("MATERIAL_MAX_PDF_MB", "0")
    response = raw.post(BASE + "/uploads", files={"file": ("big.pdf", b"%PDF-" + b"x" * 80000)})
    assert response.status_code == 413


def test_page_splitting_covers_every_character_and_preserves_locator():
    value = ("A decision and its evidence. " * 1000).strip()
    chunks = parsers.split_text(value, {"page": 7})
    assert "".join(c["text"] for c in chunks) == value
    assert all(len(c["text"]) <= 4000 and c["locator"]["page"] == 7 for c in chunks)


def test_actual_sdk_cloud_wire_contracts_against_local_test_server(tmp_path, monkeypatch):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, self.headers.get("Content-Type"), body))
            if self.path.endswith("/audio/transcriptions"):
                output = {"text": "Synthetic transcript.", "segments": [{"id": "0", "type": "transcript.text.segment", "speaker": "A", "start": 0, "end": 1, "text": "Synthetic transcript."}]}
            else:
                output = {"id": "synthetic", "object": "chat.completion", "created": 0, "model": "synthetic-vision",
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps({"text": "Synthetic scan.", "tables": [], "uncertain": []})}}]}
            encoded = json.dumps(output).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        for prefix in ("ASR", "OCR"):
            monkeypatch.setenv(prefix + "_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
            monkeypatch.setenv(prefix + "_API_KEY", "synthetic-test-token")
            monkeypatch.setenv(prefix + "_MODEL", "synthetic-" + prefix.lower())
        audio = tmp_path / "part.wav"; audio.write_bytes(wav_bytes())
        image = tmp_path / "page.png"
        from PIL import Image
        Image.new("RGB", (10, 10), "white").save(image)
        transcribed = providers.transcribe(audio, 1, 300, 2, "test-audio-request")
        recognized = providers.ocr(image, "test-image-request")
        assert transcribed["units"][0]["locator"] == {"start": 300, "end": 301, "speaker": "part2_A", "precision": "segment", "part": 2}
        assert recognized["text"] == "Synthetic scan."
        multipart = requests[0][2]
        assert b"diarized_json" in multipart and b"chunking_strategy" in multipart and b"auto" in multipart
        chat = json.loads(requests[1][2])
        assert chat["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=1)


@pytest.mark.parametrize('name,body', [('notes.md', '# Notes\n\nSynthetic text'.encode()), ('notes.txt', '合成正文'.encode('utf-16')), ('notes.csv', b'name,value\na,12')])
def test_text_upload_pipeline(raw, name, body):
    result = upload(raw, body, name=name)
    run(result['root_id'])
    source = detail(raw, result['source_id'])
    assert source['material']['kind'] == 'text'
    assert source['material']['units']
    assert source['material']['jobs'][0]['state'] == 'completed'


def test_docx_upload_pipeline(raw):
    from docx import Document
    document = Document()
    document.add_heading('Synthetic report', 1)
    document.add_paragraph('Original document text')
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = 'Revenue'
    table.cell(0, 1).text = '120'
    stream = io.BytesIO()
    document.save(stream)
    result = upload(raw, stream.getvalue(), name='report.docx')
    run(result['root_id'])
    source = detail(raw, result['source_id'])
    assert 'Original document text' in source['content']
    assert 'Revenue\t120' in source['content']
    assert source['material']['jobs'][0]['state'] == 'completed'
