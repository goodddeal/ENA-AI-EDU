"""시드 캐시 → 런타임 .cache 부트스트랩 (Cloud 콜드스타트 대비)."""

from __future__ import annotations

import shutil
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
SEED_DIR = _ROOT / "seed_cache"
RUNTIME_DIR = _ROOT / ".cache"
_SEED_FILES = ("otts.json", "posters.json", "ratings.json")
_bootstrapped = False


def ensure_runtime_caches() -> None:
    """런타임 캐시가 비어 있으면 seed_cache 를 복사해 즉시 표시 가능하게 한다."""
    global _bootstrapped
    if _bootstrapped:
        return
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    for name in _SEED_FILES:
        dest = RUNTIME_DIR / name
        src = SEED_DIR / name
        if dest.is_file() and dest.stat().st_size > 64:
            continue
        if src.is_file():
            try:
                shutil.copy2(src, dest)
            except OSError:
                pass
    _bootstrapped = True
