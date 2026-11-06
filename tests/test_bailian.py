"""Bailian wire contracts and local setup; synthetic data, no paid requests."""
import base64
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from dotenv import dotenv_values
import httpx
import pytest

from app.materials import providers
from scripts import configure_bailian as setup


@pytest.fixture
def upstream(monkeypatch):
    state = {"status": 200, "response": {}, "requests": []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            raw_body = self.rfile.read(int(self.headers["Content-Length"]))
            body = json.loads(raw_body) if self.headers.get("Content-Type", "").startswith("application/json") else raw_body
            state["requests"].append((self.path, dict(self.headers), body))
            raw = json.dumps(state["response"]).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    for prefix in ("OCR", "ASR"):
        monkeypatch.setenv(prefix + "_BASE_URL", f"http://127.0.0.1:{server.server_port}/api/v1")
        monkeypatch.setenv(prefix + "_API_KEY", "synthetic-bailian-key")
    monkeypatch.setenv("ASR_PROVIDER", "dashscope")
    monkeypatch.setenv("ASR_MODEL", "qwen-audio-3.1-asr-flash")
    try:
        yield state
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=1)


def transcript():
    return {"output": {"text": "先写提纲。明天确认。", "sentences": [
        {"begin_time": 100, "end_time": 900, "sentence_end": True, "speaker_id": 0, "text": "先写提纲。"},
        {"begin_time": 1000, "end_time": 1800, "sentence_end": True, "speaker_id": 1, "text": "明天确认。"},
    ]}, "request_id": "synthetic-request", "usage": {"duration": 2}}


def test_native_asr_real_http_contract_and_speaker_zero(upstream, tmp_path):
    upstream["response"] = transcript()
    path = tmp_path / "chunk.wav"; path.write_bytes(b"synthetic wav fixture")
    result = providers.transcribe(path, 2, 300, 2, "local-request")
    assert result["warnings"] == []
    assert [u["locator"]["speaker"] for u in result["units"]] == ["part2_0", "part2_1"]
    assert result["units"][0]["locator"]["start"] == 300.1
    assert result["units"][1]["locator"]["end"] == 301.8
    url, headers, body = upstream["requests"][0]
    assert url == "/api/v1/services/aigc/multimodal-generation/generation"
    assert headers["Authorization"] == "Bearer synthetic-bailian-key"
    assert body["parameters"] == {"speaker_diarization_enabled": True, "format": "wav", "sample_rate": "16000"}
    assert base64.b64decode(body["input"]["messages"][0]["content"][0]["input_audio"]["data"].split(",")[1]) == path.read_bytes()
    assert len(upstream["requests"]) == 1


@pytest.mark.parametrize("problem", ["unfinished", "invalid_time", "invalid_speaker", "missing_sentence", "late_start", "invalid_text"])
def test_native_asr_rejects_incomplete_or_invalid_segments(problem, upstream, tmp_path):
    payload = transcript(); sentences = payload["output"]["sentences"]
    if problem == "unfinished": sentences[0]["sentence_end"] = False
    if problem == "invalid_time": sentences[0]["end_time"] = 9000
    if problem == "invalid_speaker": sentences[0]["speaker_id"] = True
    if problem == "missing_sentence": sentences.pop(0)
    if problem == "late_start": sentences[1].update(begin_time=2200, end_time=2800)
    if problem == "invalid_text": payload["output"]["text"] = {"unexpected": "object"}
    upstream["response"] = payload
    path = tmp_path / "chunk.wav"; path.write_bytes(b"synthetic")
    with pytest.raises(providers.ProviderFailure): providers.transcribe(path, 2, 0, 1, "local-request")
    assert len(upstream["requests"]) == 1


def test_native_asr_missing_diarization_keeps_full_text_and_warns(upstream, tmp_path):
    upstream["response"] = {"output": {"text": "第一句。最后一句。", "sentence": {"text": "最后一句。"}}}
    path = tmp_path / "chunk.wav"; path.write_bytes(b"synthetic")
    result = providers.transcribe(path, 2, 300, 2, "local-request")
    assert result["units"][0]["text"] == "第一句。最后一句。"
    assert result["units"][0]["locator"]["precision"] == "chunk"
    assert result["units"][0]["locator"]["speaker"] == "unknown"
    assert result["warnings"]


@pytest.mark.parametrize("status,exception", [(401, providers.NeedsConfiguration), (403, providers.NeedsConfiguration),
    (429, providers.ProviderFailure), (500, providers.UncertainRequest)])
@pytest.mark.parametrize("protocol", ["openai", "dashscope"])
def test_asr_errors_do_not_retry_or_expose_provider_body(status, exception, protocol, upstream, tmp_path, monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", protocol)
    upstream.update(status=status, response={"message": "synthetic-bailian-key"})
    path = tmp_path / "chunk.wav"; path.write_bytes(b"synthetic")
    with pytest.raises(exception) as err: providers.transcribe(path, 2, 0, 1, "local-request")
    assert "synthetic-bailian-key" not in str(err.value)
    assert len(upstream["requests"]) == 1


def test_native_transport_error_is_uncertain():
    with pytest.raises(providers.UncertainRequest): providers._safe_failure(httpx.ReadTimeout("synthetic"))


def test_ocr_disables_thinking_on_the_actual_wire(upstream, tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_MODEL", "qwen3-vl-plus")
    monkeypatch.setenv("OCR_ENABLE_THINKING", "false")
    upstream["response"] = {"id": "test", "created": 0, "model": "qwen3-vl-plus", "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant",
            "content": json.dumps({"text": "扫描内容", "tables": [], "uncertain": []})}}]}
    path = tmp_path / "scan.png"; path.write_bytes(b"synthetic")
    assert providers.ocr(path, "local-request")["text"] == "扫描内容"
    assert upstream["requests"][0][2]["enable_thinking"] is False
    assert upstream["requests"][0][2]["response_format"] == {"type": "json_object"}


def test_shared_key_is_restricted_to_bailian_domains(monkeypatch):
    monkeypatch.delenv("OCR_API_KEY", raising=False)
    monkeypatch.setenv("OCR_MODEL", "qwen3-vl-plus")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-shared-key")
    for host in ("dashscope.aliyuncs.com", "abc.cn-beijing.maas.aliyuncs.com", "abc.ap-southeast-1.maas.aliyuncs.com"):
        monkeypatch.setenv("OCR_BASE_URL", "https://" + host + "/compatible-mode/v1")
        assert providers.configured("OCR") and providers.api_key("OCR") == "synthetic-shared-key"
    for base in ("http://dashscope.aliyuncs.com/v1", "https://dashscope.aliyuncs.com.evil.invalid/v1",
                 "https://other.aliyuncs.com/v1", "https://dashscope.aliyuncs.com:8443/v1",
                 "https://user@dashscope.aliyuncs.com/v1", "https://evil.invalid/v1"):
        monkeypatch.setenv("OCR_BASE_URL", base)
        assert not providers.configured("OCR") and not providers.api_key("OCR")
    monkeypatch.setenv("OCR_API_KEY", "explicit-other-provider-key")
    assert providers.api_key("OCR") == "explicit-other-provider-key"


def test_local_setup_preserves_auth_and_data_and_effective_shared_key(tmp_path, monkeypatch, capsys):
    path = tmp_path / ".env"
    path.write_text("# retain this comment\nAUTH_SECRET='synthetic-auth'\nDATA_DIR=./my-data\n", encoding="utf-8")
    setup.save_config(path)
    assert dotenv_values(path)["DASHSCOPE_API_KEY"] == ""
    setup.save_config(path, key="synthetic-shared-key")
    env = dotenv_values(path)
    assert env["AUTH_SECRET"] == "synthetic-auth" and env["DATA_DIR"] == "./my-data"
    assert path.read_text().startswith("# retain this comment")
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".bailian-*.tmp"))
    for name in list(os.environ):
        if name.startswith(("LLM_", "OCR_", "ASR_", "DASHSCOPE_", "MODEL_")): monkeypatch.delenv(name, raising=False)
    # check_config normally loads dotenv. Test with the same effective env even
    # though the isolated test harness deliberately disables dotenv loading.
    for name, value in env.items(): monkeypatch.setenv(name, value)
    assert setup.check_config(path)
    assert "synthetic-shared-key" not in capsys.readouterr().out
    setup.save_config(path)  # preparing again must not erase an existing key
    assert dotenv_values(path)["DASHSCOPE_API_KEY"] == "synthetic-shared-key"


def test_local_setup_refuses_priority_overrides_and_invalid_key_without_changing_file(tmp_path):
    path = tmp_path / ".env"; path.write_text("AUTH_SECRET=test\nLLM_FAST_API_KEY=existing\n")
    original = path.read_bytes()
    with pytest.raises(ValueError): setup.save_config(path, key="synthetic-new-key")
    assert path.read_bytes() == original
    path.write_text("AUTH_SECRET=test\n")
    original = path.read_bytes()
    with pytest.raises(ValueError): setup.save_config(path, key="synthetic\nnew-key")
    assert path.read_bytes() == original
    assert setup.preset("workspace123")["ASR_BASE_URL"] == "https://workspace123.cn-beijing.maas.aliyuncs.com/api/v1"
