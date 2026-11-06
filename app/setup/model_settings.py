"""Local operator model settings. Credentials never enter public responses."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit

from dotenv import set_key
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.llm import ModelTier, resolve_tier

ENV_PATH = Path(__file__).resolve().parents[2] / '.env'
LOCK = threading.Lock()
SLOTS = ('fast', 'strong', 'ocr', 'asr')
PREFIXES = {'fast': 'LLM_FAST', 'strong': 'LLM_STRONG', 'ocr': 'OCR', 'asr': 'ASR'}


class ModelSetting(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = Field(max_length=200)
    base_url: str = Field(max_length=500)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(''))
    protocol: str = 'openai'


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str | None = None
    version: str
    slots: dict[str, ModelSetting]


def advanced():
    return any(value and (name in {'LLM_CONFIG_FILE', 'LLM_CONFIG_JSON', 'LLM_STRONG_FALLBACK_MODEL'}
                         or name.startswith('LLM_') and name.endswith('_CHAIN'))
               for name, value in os.environ.items())


def snapshot():
    from app.materials.providers import api_key
    slots = {}
    for slot in SLOTS:
        prefix = PREFIXES[slot]
        values = resolve_tier(ModelTier(slot)) if slot in ('fast', 'strong') else {
            'model': os.getenv(prefix + '_MODEL', ''), 'base_url': os.getenv(prefix + '_BASE_URL', ''),
            'api_key': api_key(prefix),
        }
        slots[slot] = {name: str(values.get(name) or '') for name in ('model', 'base_url')}
        slots[slot]['protocol'] = os.getenv('ASR_PROVIDER', 'openai') if slot == 'asr' else 'openai'
        slots[slot]['has_key'] = bool(values['api_key'])
    # Include keys in the revision digest so concurrent credential edits conflict too.
    revision = sorted((k, v) for k, v in os.environ.items() if k.startswith(('LLM_', 'OCR_', 'ASR_', 'DASHSCOPE_', 'KIMI_', 'OPENAI_', 'DEEPSEEK_', 'MOONSHOT_', 'MODEL_')))
    version = hashlib.sha256(json.dumps(revision).encode()).hexdigest()
    return {'slots': slots, 'version': version, 'editable': not advanced()}


def save(body):
    from fastapi import HTTPException
    with LOCK:
        current = snapshot()
        if advanced():
            raise ValueError('已有高级模型路由，请先在本机配置中核对 LLM_CONFIG、CHAIN 或 fallback 配置')
        if body.version != current['version']:
            raise HTTPException(409, detail={'message': '配置已更新，请重新读取后再编辑'})
        if set(body.slots) != set(SLOTS):
            raise ValueError('请提交全部四项服务配置')
        values = {}
        for slot, setting in body.slots.items():
            prefix = PREFIXES[slot]
            if not setting.model.strip() and not setting.base_url.strip() and not setting.api_key.get_secret_value().strip():
                continue
            base = setting.base_url.strip().rstrip('/')
            try:
                url = urlsplit(base)
                valid = url.scheme == 'https' or url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'}
                valid = valid and url.hostname and not (url.username or url.password or url.query or url.fragment)
                url.port
            except ValueError:
                valid = False
            if not valid:
                raise ValueError('服务地址需为 HTTPS，或本机 HTTP 地址')
            key = setting.api_key.get_secret_value().strip()
            model = setting.model.strip()
            if not model or any(ord(c) < 32 or ord(c) == 127 for c in model + base + key) or any(c.isspace() for c in key):
                raise ValueError('模型、地址或密钥格式无效')
            old = current['slots'][slot]
            if not key and old['has_key'] and base != old['base_url'].rstrip('/'):
                raise ValueError('更换服务地址时请填写对应的新密钥')
            if setting.protocol not in ({'openai', 'dashscope'} if slot == 'asr' else {'openai'}):
                raise ValueError('所选服务协议不受支持')
            values[prefix + '_MODEL'] = model
            values[prefix + '_BASE_URL'] = base
            if key:
                values[prefix + '_API_KEY'] = key
            if slot == 'asr':
                values['ASR_PROVIDER'] = setting.protocol
            if slot in ('fast', 'strong') and (model != old['model'] or base != old['base_url'].rstrip('/')):
                values[prefix + '_PARAMS'] = '{}'
        if ENV_PATH.is_symlink():
            raise ValueError('本机配置文件不能是符号链接')
        fd, temporary = tempfile.mkstemp(prefix='.model-settings-', dir=ENV_PATH.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(ENV_PATH.read_text() if ENV_PATH.exists() else '')
            for name, value in values.items():
                set_key(temporary, name, value, quote_mode='always')
            os.chmod(temporary, 0o600)
            os.replace(temporary, ENV_PATH)
        finally:
            Path(temporary).unlink(missing_ok=True)
        os.environ.update(values)
        return snapshot()
