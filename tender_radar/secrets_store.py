from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .db import get_setting, set_setting


ENV_KEYS = {
    "public_data_api_key": "DATA_GO_KR_SERVICE_KEY",
    "law_api_oc": "LAW_API_OC",
    "pexels_api_key": "PEXELS_API_KEY",
}


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def protect_secret(value: str) -> str:
    """Encrypt a secret for the current Windows user with DPAPI."""
    if not value:
        return ""
    if os.name != "nt":
        raise RuntimeError("API 인증값 암호화는 Windows DPAPI 환경에서만 지원됩니다.")
    source, source_buffer = _blob(value.encode("utf-8"))
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del source_buffer
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("dpapi:"):
        return value  # allows one-time migration from the previous plaintext setting
    encrypted = base64.b64decode(value[6:])
    source, source_buffer = _blob(encrypted)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        clear = ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
        del source_buffer
    return clear


def set_secret(db_path: Path, key: str, value: str) -> None:
    set_setting(db_path, key, protect_secret(value))


def get_secret(db_path: Path, key: str, default: str = "") -> str:
    env_value = os.getenv(ENV_KEYS.get(key, ""), "").strip() if key in ENV_KEYS else ""
    if env_value:
        return env_value
    stored = get_setting(db_path, key, "")
    return unprotect_secret(stored) if stored else default


def migrate_secret(db_path: Path, key: str, fallback: str = "") -> None:
    if os.name != "nt":
        return
    stored = get_setting(db_path, key, "")
    if stored and not stored.startswith("dpapi:"):
        set_secret(db_path, key, stored)
    elif not stored and fallback:
        set_secret(db_path, key, fallback)
