"""시드 캐시 → 런타임 .cache 부트스트랩 (Cloud 콜드스타트 대비)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
SEED_DIR = _ROOT / "seed_cache"
RUNTIME_DIR = _ROOT / ".cache"
_SEED_FILES = ("otts.json", "posters.json", "ratings.json")
# seed_cache 내용이 바뀌면 숫자를 올려 Cloud 의 낡은 .cache 를 덮어쓴다.
SEED_VERSION = 6
_VERSION_PATH = RUNTIME_DIR / "seed_version.txt"
_bootstrapped = False


def _read_runtime_version() -> int:
    if not _VERSION_PATH.is_file():
        return 0
    try:
        return int(_VERSION_PATH.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def _write_runtime_version(version: int) -> None:
    try:
        _VERSION_PATH.write_text(str(version), encoding="utf-8")
    except OSError:
        pass


def _rating_coverage(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return 0
    return sum(
        1
        for v in items.values()
        if isinstance(v, dict) and v.get("view_rate") is not None
    )


def ensure_runtime_caches(*, force: bool = False) -> None:
    """
    seed_cache → .cache 동기화.
    - 런타임 파일이 없거나 너무 작으면 복사
    - SEED_VERSION 이 올라가면 강제 복사 (Cloud 낡은 캐시 고착 방지)
    - ratings 는 시드 커버리지가 더 높으면 덮어씀
    """
    global _bootstrapped
    if _bootstrapped and not force:
        return

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    runtime_ver = _read_runtime_version()
    version_bump = runtime_ver < SEED_VERSION

    for name in _SEED_FILES:
        dest = RUNTIME_DIR / name
        src = SEED_DIR / name
        if not src.is_file():
            continue

        need_copy = force or version_bump
        if not need_copy:
            if not dest.is_file() or dest.stat().st_size <= 64:
                need_copy = True
            elif name == "ratings.json":
                if _rating_coverage(src) > _rating_coverage(dest):
                    need_copy = True

        if not need_copy:
            continue
        try:
            shutil.copy2(src, dest)
        except OSError:
            pass

    if version_bump or force:
        _write_runtime_version(SEED_VERSION)
    _bootstrapped = True
