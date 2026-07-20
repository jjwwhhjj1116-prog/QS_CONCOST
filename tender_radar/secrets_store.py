from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import os
import secrets
from ctypes import wintypes
from pathlib import Path

from .db import get_setting, set_setting


ENV_KEYS = {
    "public_data_api_key": "DATA_GO_KR_SERVICE_KEY",
    "law_api_oc": "LAW_API_OC",
    "pexels_api_key": "PEXELS_API_KEY",
    "resend_api_key": "RESEND_API_KEY",
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
        master = os.getenv("APP_SECRET_KEY", "").encode("utf-8")
        if len(master) < 24:
            raise RuntimeError("서버 APP_SECRET_KEY 환경변수를 먼저 설정하세요.")
        salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16)
        key = hashlib.pbkdf2_hmac("sha256", master, salt, 200_000, dklen=32)
        clear = value.encode("utf-8")
        stream = bytearray()
        counter = 0
        while len(stream) < len(clear):
            stream.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
            counter += 1
        cipher = bytes(a ^ b for a, b in zip(clear, stream))
        tag = hmac.new(key, b"tag" + nonce + cipher, hashlib.sha256).digest()[:16]
        return "portable:" + base64.b64encode(salt + nonce + tag + cipher).decode("ascii")
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
    if value.startswith("portable:"):
        master = os.getenv("APP_SECRET_KEY", "").encode("utf-8")
        if len(master) < 24:
            raise RuntimeError("서버 APP_SECRET_KEY 환경변수가 필요합니다.")
        payload = base64.b64decode(value[9:])
        salt, nonce, tag, cipher = payload[:16], payload[16:32], payload[32:48], payload[48:]
        key = hashlib.pbkdf2_hmac("sha256", master, salt, 200_000, dklen=32)
        expected = hmac.new(key, b"tag" + nonce + cipher, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, expected):
            raise RuntimeError("저장된 인증값의 무결성 검증에 실패했습니다.")
        stream = bytearray()
        counter = 0
        while len(stream) < len(cipher):
            stream.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
            counter += 1
        return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")
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
    if not stored:
        return default
    try:
        return unprotect_secret(stored)
    except (OSError, RuntimeError, ValueError, UnicodeError):
        # A secret encrypted by another Windows account or with an old
        # APP_SECRET_KEY must not abort otherwise successful bid collection.
        # Environment variables still take precedence, and the admin can replace
        # an unreadable stored value explicitly.
        return default


def migrate_secret(db_path: Path, key: str, fallback: str = "") -> None:
    if os.name != "nt":
        return
    stored = get_setting(db_path, key, "")
    if stored and not stored.startswith("dpapi:"):
        set_secret(db_path, key, stored)
    elif not stored and fallback:
        set_secret(db_path, key, fallback)
