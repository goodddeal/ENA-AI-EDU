"""환경 변수 로드 — 네이버 검색 API, OpenAI, Supabase 등."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"

_ENV_KEYS = (
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "OPENAI_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
)


def _manual_load_env(path: Path) -> None:
    """dotenv 실패 시 간단한 KEY=VALUE 파서 (utf-8 / utf-8-sig)."""
    if not path.is_file():
        return
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            lines = path.read_text(encoding=encoding).splitlines()
            break
        except UnicodeDecodeError:
            continue
    else:
        return

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _force_apply_keys(keys: tuple[str, ...] = _ENV_KEYS) -> None:
    """비어 있는 키만 .env 값으로 강제 채움."""
    if not _ENV_PATH.is_file():
        return
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            lines = _ENV_PATH.read_text(encoding=encoding).splitlines()
            break
        except UnicodeDecodeError:
            continue
    else:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key in keys and value and not os.getenv(key, "").strip():
            os.environ[key] = value


def reload_env() -> None:
    """프로젝트 .env 및 Streamlit secrets 를 다시 로드."""
    load_dotenv(_ENV_PATH, encoding="utf-8-sig", override=True)
    # Streamlit Cloud: Settings → Secrets 의 값을 환경변수로 반영
    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if secrets is not None:
            for key in _ENV_KEYS:
                try:
                    val = secrets.get(key)  # type: ignore[attr-defined]
                except Exception:
                    val = None
                if val is None:
                    continue
                text = str(val).strip()
                if text:
                    os.environ[key] = text
    except Exception:
        pass

    missing = [k for k in _ENV_KEYS if not os.getenv(k, "").strip()]
    if missing:
        _manual_load_env(_ENV_PATH)
        _force_apply_keys(tuple(missing))


reload_env()


def _get_env(name: str) -> str:
    """Streamlit 재실행 시 .env 수정 반영."""
    if not os.getenv(name, "").strip():
        reload_env()
    return os.getenv(name, "").strip()


def get_naver_credentials() -> tuple[str, str]:
    """(client_id, client_secret) 반환. 미설정 시 빈 문자열."""
    return _get_env("NAVER_CLIENT_ID"), _get_env("NAVER_CLIENT_SECRET")


def naver_credentials_configured() -> bool:
    client_id, client_secret = get_naver_credentials()
    return bool(client_id and client_secret)


def get_openai_api_key() -> str:
    return _get_env("OPENAI_API_KEY")


def openai_configured() -> bool:
    return bool(get_openai_api_key())


def get_supabase_credentials() -> tuple[str, str]:
    """(url, anon_key) 반환. 미설정 시 빈 문자열."""
    return _get_env("SUPABASE_URL"), _get_env("SUPABASE_ANON_KEY")


def supabase_configured() -> bool:
    url, anon_key = get_supabase_credentials()
    return bool(url and anon_key)
