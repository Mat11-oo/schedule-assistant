import json
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi import HTTPException
from starlette.requests import Request

from app.setup import model_settings as settings
from app.setup.api import local_operator


@pytest.fixture
def config(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(('LLM_', 'OCR_', 'ASR_', 'DASHSCOPE_', 'KIMI_', 'MOONSHOT_', 'DEEPSEEK_', 'OPENAI_', 'MODEL_')):
            monkeypatch.delenv(name)
    path = tmp_path / '.env'
    path.write_text('AUTH_SECRET=keep-auth\nOTHER=value\n')
    monkeypatch.setattr(settings, 'ENV_PATH', path)
    original = dict(os.environ)
    yield path
    for name in list(os.environ):
        if name not in original:
            os.environ.pop(name)
    os.environ.update(original)


def body():
    slots = {slot: {'model': 'synthetic-model', 'base_url': 'https://example.test/v1', 'api_key': 'synthetic-key'} for slot in settings.SLOTS}
    return settings.SettingsInput(version=settings.snapshot()['version'], slots=slots)


def test_save_reload_and_no_secret_response(config):
    result = settings.save(body())
    assert 'synthetic-key' not in json.dumps(result)
    assert all(slot['has_key'] for slot in result['slots'].values())
    saved = dotenv_values(config)
    assert saved['AUTH_SECRET'] == 'keep-auth'
    assert saved['LLM_FAST_API_KEY'] == 'synthetic-key'
    assert config.stat().st_mode & 0o777 == 0o600
    from app.gateway.config import get_config
    assert get_config().chain('fast')[0].api_key() == 'synthetic-key'
    assert get_config().chain('fast')[0].model == 'synthetic-model'
    second = body()
    for slot in second.slots.values():
        slot.api_key = settings.SecretStr('')
    settings.save(second)
    assert dotenv_values(config)['LLM_FAST_API_KEY'] == 'synthetic-key'


def test_stale_and_destination_change(config):
    stale = body()
    settings.save(body())
    with pytest.raises(HTTPException) as error:
        settings.save(stale)
    assert error.value.status_code == 409
    changed = body()
    changed.slots['fast'].base_url = 'https://another.test/v1'
    changed.slots['fast'].api_key = settings.SecretStr('')
    before = config.read_text()
    with pytest.raises(ValueError):
        settings.save(changed)
    assert config.read_text() == before


def test_invalid_config_is_atomic(config, monkeypatch):
    invalid = body()
    invalid.slots['ocr'].base_url = 'http://remote.test/v1'
    before = config.read_text()
    with pytest.raises(ValueError):
        settings.save(invalid)
    assert config.read_text() == before
    monkeypatch.setenv('LLM_CONFIG_JSON', '{}')
    assert not settings.snapshot()['editable']
    with pytest.raises(ValueError):
        settings.save(body())


def test_optional_unconfigured_services(config):
    value = body()
    for slot in ('ocr', 'asr'):
        value.slots[slot] = settings.ModelSetting(model='', base_url='')
    result = settings.save(value)
    assert not result['slots']['ocr']['has_key']


@pytest.mark.parametrize('client,host,origin', [
    ('192.168.1.1', 'localhost:8787', None),
    ('127.0.0.1', 'attacker.test', None),
    ('127.0.0.1', 'localhost:8787', 'https://attacker.test'),
])
def test_remote_operator_rejected(monkeypatch, client, host, origin):
    import app.data_access
    monkeypatch.setattr(app.data_access, 'current_user', lambda: 'test-owner')
    headers = [(b'host', host.encode())]
    if origin:
        headers.append((b'origin', origin.encode()))
    request = Request({'type': 'http', 'method': 'PUT', 'scheme': 'http', 'path': '/', 'headers': headers, 'client': (client, 1234), 'server': ('localhost', 8787), 'query_string': b''})
    with pytest.raises(HTTPException) as error:
        local_operator(request)
    assert error.value.status_code == 403


def test_authenticated_api_persists_and_masks(config):
    from app.auth import mint_user_token
    from app.main import app
    from tests.conftest import RawTestClient
    with RawTestClient(app, base_url='http://localhost', client=('127.0.0.1', 1234),
                       headers={'X-User-Token': mint_user_token('model-settings-review')}) as client:
        response = client.get('/api/arrangements/setup/models')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        payload = body().model_dump(mode='json')
        for slot in payload['slots'].values():
            slot['api_key'] = 'synthetic-key'
        response = client.put('/api/arrangements/setup/models', json=payload,
                              headers={'Origin': 'http://localhost'})
        assert response.status_code == 200, response.text
        assert 'synthetic-key' not in response.text
        assert dotenv_values(config)['ASR_API_KEY'] == 'synthetic-key'
