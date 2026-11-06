#!/usr/bin/env python3
"""Local Bailian setup. Never calls a cloud API or prints a credential."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import re
import sys
import tempfile

from dotenv import dotenv_values, load_dotenv, set_key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def preset(workspace=""):
    if workspace and not re.fullmatch(r"[a-zA-Z0-9-]+", workspace):
        raise ValueError("业务空间 ID 只能包含字母、数字和连字符")
    host = f"{workspace}.cn-beijing.maas.aliyuncs.com" if workspace else "dashscope.aliyuncs.com"
    base = "https://" + host
    return {
        "LLM_FAST_BASE_URL": base + "/compatible-mode/v1",
        "LLM_FAST_MODEL": "qwen-plus",
        "LLM_FAST_PARAMS": '{"enable_thinking":false}',
        "LLM_STRONG_BASE_URL": base + "/compatible-mode/v1",
        "LLM_STRONG_MODEL": "qwen-plus",
        "LLM_STRONG_PARAMS": '{"enable_thinking":false}',
        "OCR_BASE_URL": base + "/compatible-mode/v1",
        "OCR_MODEL": "qwen3-vl-plus",
        "OCR_ENABLE_THINKING": "false",
        "ASR_PROVIDER": "dashscope",
        "ASR_BASE_URL": base + "/api/v1",
        "ASR_MODEL": "qwen-audio-3.1-asr-flash",
    }


def save_config(path, *, workspace="", key=None):
    """Atomically update selected fields; retain auth, data and unrelated settings."""
    if path.is_symlink():
        raise ValueError("请直接配置普通 .env 文件，不使用符号链接")
    existing = dotenv_values(path, interpolate=False) if path.exists() else {}
    # Advanced routes and dedicated keys take priority over the shared preset.
    # Refuse an ambiguous configuration instead of silently changing its owner.
    blockers = [k for k, v in existing.items() if v and (
        k in {"LLM_CONFIG_FILE", "LLM_CONFIG_JSON", "LLM_STRONG_FALLBACK_MODEL",
              "LLM_FAST_API_KEY", "LLM_STRONG_API_KEY", "OCR_API_KEY", "ASR_API_KEY"}
        or (k.startswith("LLM_") and k.endswith("_CHAIN")))]
    if blockers:
        raise ValueError("已有优先配置，请先核对这些字段再使用统一预设：" + "、".join(sorted(blockers)))
    values = preset(workspace)
    if key is not None:
        key = key.strip()
        if not key or re.search(r"\s|[\x00-\x1f\x7f]", key):
            raise ValueError("API Key 不能为空，也不能包含空白或控制字符")
        values["DASHSCOPE_API_KEY"] = key
    elif not existing.get("DASHSCOPE_API_KEY"):
        values["DASHSCOPE_API_KEY"] = ""
    fd, temporary = tempfile.mkstemp(prefix=".bailian-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(path.read_text(encoding="utf-8") if path.exists() else "")
        for name, value in values.items():
            set_key(temporary, name, value, quote_mode="always")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def check_config(path):
    """Inspect effective local config, without generating text or uploading data."""
    load_dotenv(path, override=False)
    from app.gateway.config import get_config
    from app.materials import providers
    cfg = get_config()
    rows = []
    for tier, label in (("fast", "文字理解"), ("strong", "复杂任务")):
        chain = cfg.chain(tier)
        selected = next((m for m in chain if m.has_key()), chain[0] if chain else None)
        ready = bool(selected and selected.has_key() and selected.model == "qwen-plus"
                     and providers._dashscope_host(selected.base_url)
                     and selected.params.get("enable_thinking") is False)
        rows.append((label, ready))
    rows.extend([
        ("扫描件识别", providers.configured("OCR")
         and os.getenv("OCR_MODEL") == "qwen3-vl-plus"
         and os.getenv("OCR_ENABLE_THINKING", "").lower() == "false"
         and providers._dashscope_host(os.getenv("OCR_BASE_URL", ""))),
        ("会议录音", providers.configured("ASR") and os.getenv("ASR_PROVIDER") == "dashscope"
         and os.getenv("ASR_MODEL") == "qwen-audio-3.1-asr-flash"
         and providers._dashscope_host(os.getenv("ASR_BASE_URL", ""))),
    ])
    for label, ready in rows:
        print(f"{label}：{'本机配置齐全' if ready else '待补充或核对配置'}")
    print("本检查没有调用云服务；密钥有效性、模型权限和识别质量需真实联调。")
    return all(ready for _, ready in rows)


def main():
    parser = argparse.ArgumentParser(description="配置北京地域百炼；密钥只写入本机 .env")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--prepare", action="store_true", help="只准备模型及接口地址，不询问密钥")
    action.add_argument("--check", action="store_true", help="仅检查本机配置，不发起云端调用")
    parser.add_argument("--workspace", default="", help="可选业务空间 ID，使用专属接入域名")
    args = parser.parse_args()
    path = ROOT / ".env"
    try:
        if args.check:
            return 0 if check_config(path) else 1
        key = None
        if not args.prepare:
            if not sys.stdin.isatty():
                raise ValueError("请在本机终端运行，以隐藏输入 API Key；不要把密钥放在命令参数中")
            print("请使用百炼北京地域的 API Key，输入时不会显示。")
            key = getpass.getpass("API Key：")
        save_config(path, workspace=args.workspace.strip(), key=key)
        print("已保存本机百炼配置（.env 权限为 600）；没有发起云端调用。")
        if args.prepare:
            print("创建 API Key 后，再运行同一命令并去掉 --prepare，在隐藏输入中粘贴密钥。")
        print("运行 --check 检查配置，随后重启本地应用。")
        return 0
    except (ValueError, OSError):
        # Errors can originate in third-party parsers: never echo raw credentials.
        print("配置未完成：请核对 .env 是否可写、有无独立密钥或 LLM_CONFIG／CHAIN 等覆盖配置，以及输入格式。", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("已取消，未完成的输入不会保存。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
