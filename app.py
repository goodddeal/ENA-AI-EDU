"""
방송 프로그램 편성 OTT 연결 — Streamlit 모바일 웹 MVP
PRD: broadcast_ott_service_planning.md
"""

from __future__ import annotations

import html
from datetime import date
from urllib.parse import quote, urlsplit, urlunsplit

import streamlit as st

import config  # noqa: F401 — .env 로드
from cache_bootstrap import ensure_runtime_caches
from data import OTT_META, get_content_by_id, get_sorted_contents, palette_for
from naver_api import resolve_otts_for_title
from otts import (
    ensure_ott_cache,
    get_cached_ott,
    load_ott_cache,
    ott_cache_stats,
    set_cached_ott,
)
from posters import (
    ensure_poster_cache,
    is_safe_poster_url,
    load_poster_cache,
    resolve_poster_url,
)
from ratings import (
    cache_meta_label,
    format_rate_percent,
    format_rating_label,
    get_episode_ratings,
    get_rating,
    load_cache,
)

# ---------------------------------------------------------------------------
# 페이지 / 세션
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="방송→OTT",
    page_icon="📺",
    layout="centered",
    initial_sidebar_state="collapsed",
)

TEST_USER = {"id": "demo", "name": "테스트시청자"}

# 보러가기/포스터 캐시 버전 (올리면 세션 캐시 전체 폐기)
OTT_LOGIC_VERSION = 16
LIST_PAGE_SIZE = 12  # 위젯 수↓ → 첫 화면 즉시 표시


@st.cache_data(show_spinner=False, ttl=300)
def _load_ratings_cached() -> dict:
    from cache_bootstrap import ensure_runtime_caches

    ensure_runtime_caches()
    cache = load_cache()
    items = cache.get("items") if isinstance(cache, dict) else None
    has = 0
    if isinstance(items, dict):
        has = sum(
            1
            for v in items.values()
            if isinstance(v, dict) and v.get("view_rate") is not None
        )
    # Cloud 에 낡은 빈 캐시가 남은 경우 seed 강제 재적용
    if has < 40:
        ensure_runtime_caches(force=True)
        cache = load_cache()
    _ = "ratings_seed_v7"
    return cache


@st.cache_data(show_spinner=False, ttl=300)
def _load_posters_cached() -> dict:
    ensure_runtime_caches()
    _ = "posters_seed_v7"
    return load_poster_cache()


@st.cache_data(show_spinner=False, ttl=300)
def _load_otts_cached() -> dict:
    ensure_runtime_caches()
    _ = "otts_seed_v7"
    return load_ott_cache()


def init_session() -> None:
    ensure_runtime_caches()
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = True
        st.session_state.user = TEST_USER
    if "view" not in st.session_state:
        st.session_state.view = "home"
    if "selected_id" not in st.session_state:
        st.session_state.selected_id = None
    if "genre" not in st.session_state:
        st.session_state.genre = "전체"
    if "program_search" not in st.session_state:
        st.session_state.program_search = ""
    if "search_query" not in st.session_state:
        st.session_state.search_query = ""
    if "list_page" not in st.session_state:
        st.session_state.list_page = 0

    # 구버전 OTT 캐시 제거
    if st.session_state.get("_ott_logic_version") != OTT_LOGIC_VERSION:
        for key in list(st.session_state.keys()):
            if str(key).startswith("ott_cache:"):
                del st.session_state[key]
        st.session_state["_ott_logic_version"] = OTT_LOGIC_VERSION
        # streamlit cache_data 잔존분도 제거
        try:
            st.cache_data.clear()
        except Exception:
            pass


def go_home() -> None:
    st.session_state.view = "home"
    st.session_state.selected_id = None


def go_detail(content_id: str) -> None:
    st.session_state.view = "detail"
    st.session_state.selected_id = content_id


def hex_to_rgb_css(color: str) -> str:
    """Streamlit markdown 이 #hex 를 제목으로 오인하지 않도록 rgb() 로 변환."""
    c = (color or "").strip()
    if c.startswith("rgb"):
        return c
    if c.startswith("#"):
        c = c[1:]
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return "rgb(120,120,120)"
    try:
        r = int(c[0:2], 16)
        g = int(c[2:4], 16)
        b = int(c[4:6], 16)
    except ValueError:
        return "rgb(120,120,120)"
    return f"rgb({r},{g},{b})"


def encode_media_url(url: str) -> str:
    """한글 경로 등 non-ASCII URL 을 브라우저가 로드 가능하게 인코딩."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    path = quote(parts.path, safe="/:@!$&'()*+,;=-._~")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def render_html(fragment: str) -> None:
    """
    HTML 렌더.
    st.markdown 은 빈 줄/들여쓰기에서 HTML을 코드블록으로 깨뜨리므로
    가능하면 st.html, 없으면 components.html 사용.
    """
    compact = " ".join(line.strip() for line in fragment.splitlines() if line.strip())
    if not compact:
        return
    if hasattr(st, "html"):
        st.html(compact)
        return
    try:
        import streamlit.components.v1 as components

        components.html(compact, height=40, scrolling=False)
    except Exception:
        st.markdown(compact, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 스타일 (스마트폰 비율 고정)
# ---------------------------------------------------------------------------
def inject_css() -> None:
    st.markdown(
        """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');

html, body, [class*="css"] {
  font-family: 'Pretendard', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif !important;
}

#MainMenu, footer, header { visibility: hidden; height: 0; }
[data-testid="stSidebar"] { display: none; }
.block-container {
  max-width: 430px !important;
  padding: 0.6rem 0.85rem 3.5rem !important;
  margin: 0 auto !important;
}
[data-testid="stAppViewContainer"] {
  background: linear-gradient(180deg, #0b0d12 0%, #141824 45%, #0f1219 100%);
}
[data-testid="stHeader"] { background: transparent; }

div[data-testid="stVerticalBlock"] > div { gap: 0.35rem; }

.phone-shell { width: 100%; color: #f2f4f8; }

.brand-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.4rem 0 0.85rem;
  border-bottom: 1px solid rgba(255,255,255,0.08);
  margin-bottom: 0.9rem;
}
.brand-name {
  font-size: 1.35rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  background: linear-gradient(90deg, #fff 0%, #a8b4ff 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.brand-sub {
  font-size: 0.72rem;
  color: #8b93a7;
  margin-top: 0.15rem;
}
.user-chip {
  font-size: 0.72rem;
  color: #c5cad8;
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.1);
  padding: 0.35rem 0.65rem;
  border-radius: 999px;
}

.section-title {
  font-size: 1.05rem;
  font-weight: 700;
  margin: 0.4rem 0 0.15rem;
  color: #fff;
}
.section-desc {
  font-size: 0.8rem;
  color: #8b93a7;
  margin-bottom: 0.85rem;
  line-height: 1.4;
}

/* 돋보기 서치창 */
div[data-testid="stTextInput"] {
  margin-bottom: 0.55rem;
}
div[data-testid="stTextInput"] label { display: none; }
div[data-testid="stTextInput"] > div > div {
  background: rgba(255,255,255,0.06) !important;
  border: 1px solid rgba(255,255,255,0.12) !important;
  border-radius: 12px !important;
  box-shadow: none !important;
}
div[data-testid="stTextInput"] input {
  color: #f2f4f8 !important;
  caret-color: #a8b4ff !important;
  font-size: 0.9rem !important;
  padding: 0.7rem 0.85rem 0.7rem 2.55rem !important;
  background-color: transparent !important;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18' fill='none' stroke='%238b93a7' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='8' cy='8' r='5.5'/%3E%3Cpath d='M12.5 12.5L16 16'/%3E%3C/svg%3E") !important;
  background-repeat: no-repeat !important;
  background-position: 0.85rem center !important;
  background-size: 1.05rem !important;
}
div[data-testid="stTextInput"] input::placeholder {
  color: #6f778c !important;
}
div[data-testid="stTextInput"] > div > div:focus-within {
  border-color: rgba(168,180,255,0.55) !important;
  background: rgba(255,255,255,0.08) !important;
}
.search-hint {
  font-size: 0.72rem;
  color: #8b93a7;
  margin: -0.2rem 0 0.65rem;
}

.content-card {
  display: flex;
  gap: 0.75rem;
  padding: 0.7rem;
  margin-bottom: 0.55rem;
  border-radius: 14px;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.07);
}
.thumb {
  flex-shrink: 0;
  width: 78px;
  height: 104px;
  border-radius: 10px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 0.35rem;
  font-size: 0.65rem;
  font-weight: 700;
  color: rgba(255,255,255,0.9);
  text-align: center;
  line-height: 1.2;
  box-shadow: inset 0 -30px 40px rgba(0,0,0,0.35);
  overflow: hidden;
  background: #1a1d27;
}
.thumb img {
  width: 78px !important;
  height: 104px !important;
  max-width: 78px !important;
  object-fit: cover !important;
  border-radius: 10px;
  display: block;
}
/* st.image 기반 목록 썸네일 */
.list-row [data-testid="stImage"] {
  border-radius: 10px;
  overflow: hidden;
  background: #1a1d27;
}
.list-row [data-testid="stImage"] img {
  width: 100% !important;
  aspect-ratio: 3 / 4;
  object-fit: cover !important;
  border-radius: 10px;
}
.detail-poster {
  width: 100%;
  max-height: 280px;
  object-fit: cover;
  border-radius: 16px;
  margin-bottom: 1rem;
  background: #1a1d27;
}
[data-testid="stImage"] img.detail-native,
div:has(> [data-testid="stImage"]) img {
  border-radius: 16px;
  object-fit: cover;
}
.card-body { flex: 1; min-width: 0; }
.card-title {
  font-size: 0.95rem;
  font-weight: 700;
  color: #fff;
  margin: 0 0 0.25rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.card-meta {
  font-size: 0.72rem;
  color: #9aa3b8;
  margin-bottom: 0.35rem;
}
.badge-genre {
  display: inline-block;
  font-size: 0.65rem;
  font-weight: 600;
  padding: 0.12rem 0.4rem;
  border-radius: 4px;
  background: rgba(120,140,255,0.2);
  color: #b8c2ff;
  margin-right: 0.3rem;
}
.badge-airing {
  display: inline-block;
  font-size: 0.65rem;
  font-weight: 700;
  padding: 0.12rem 0.4rem;
  border-radius: 4px;
  background: rgba(46, 204, 113, 0.2);
  color: #7dffa8;
  margin-right: 0.3rem;
}
.badge-ended {
  display: inline-block;
  font-size: 0.65rem;
  font-weight: 600;
  padding: 0.12rem 0.4rem;
  border-radius: 4px;
  background: rgba(255,255,255,0.08);
  color: #9aa3b8;
  margin-right: 0.3rem;
}
.group-title {
  font-size: 0.88rem;
  font-weight: 700;
  color: #c5cad8;
  margin: 0.85rem 0 0.45rem;
  padding-top: 0.25rem;
}
.rating-badge {
  display: inline-block;
  font-size: 0.7rem;
  font-weight: 700;
  color: #ffd27a;
  background: rgba(255, 180, 60, 0.12);
  border: 1px solid rgba(255, 180, 60, 0.28);
  padding: 0.14rem 0.45rem;
  border-radius: 6px;
  margin: 0.15rem 0 0.35rem;
}
.rating-detail {
  font-size: 0.88rem;
  color: #ffd27a;
  font-weight: 700;
  margin: 0.35rem 0 0.15rem;
}
.rating-sub {
  font-size: 0.72rem;
  color: #8b93a7;
  margin-bottom: 0.75rem;
}
.ep-rating-wrap {
  margin: 0.35rem 0 1rem;
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 12px;
  overflow: hidden;
  background: rgba(255,255,255,0.03);
}
.ep-rating-head {
  display: grid;
  grid-template-columns: 1.1fr 1.2fr 0.9fr;
  gap: 0.35rem;
  padding: 0.55rem 0.75rem;
  font-size: 0.72rem;
  font-weight: 700;
  color: #9aa3b8;
  background: rgba(255,255,255,0.04);
  border-bottom: 1px solid rgba(255,255,255,0.08);
}
.ep-rating-row {
  display: grid;
  grid-template-columns: 1.1fr 1.2fr 0.9fr;
  gap: 0.35rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.82rem;
  color: #e8ebf4;
  border-bottom: 1px solid rgba(255,255,255,0.05);
}
.ep-rating-row:last-child { border-bottom: none; }
.ep-rating-row .ep-num { font-weight: 700; color: #fff; }
.ep-rating-row .ep-rate {
  font-weight: 800;
  color: #ffd27a;
  text-align: right;
}
.ep-rating-row .ep-date { color: #9aa3b8; font-size: 0.78rem; }
.ep-rating-note {
  font-size: 0.7rem;
  color: #8b93a7;
  margin: 0.15rem 0 0.85rem;
}
.unconfirmed {
  color: #ff8c2a !important;
  font-weight: 700;
  font-size: 0.78rem;
}
.ott-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-top: 0.25rem;
}
.ott-pill {
  font-size: 0.65rem;
  font-weight: 600;
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  color: #fff;
}

.detail-hero {
  width: 100%;
  height: 200px;
  border-radius: 16px;
  display: flex;
  align-items: flex-end;
  padding: 1rem;
  margin-bottom: 1rem;
  box-shadow: inset 0 -60px 80px rgba(0,0,0,0.55);
}
.detail-hero h1 {
  margin: 0;
  font-size: 1.45rem;
  font-weight: 800;
  color: #fff;
  text-shadow: 0 2px 8px rgba(0,0,0,0.5);
}
.detail-meta {
  font-size: 0.82rem;
  color: #a0a8bc;
  margin-bottom: 0.75rem;
  line-height: 1.5;
}
.detail-desc {
  font-size: 0.88rem;
  color: #d0d5e2;
  line-height: 1.55;
  margin-bottom: 1.1rem;
}
.ott-link-grid {
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
  margin: 0.5rem 0 1.2rem;
}
.ott-link {
  display: flex;
  align-items: center;
  justify-content: space-between;
  text-decoration: none !important;
  padding: 0.85rem 1rem;
  border-radius: 12px;
  color: #fff !important;
  font-weight: 700;
  font-size: 0.95rem;
}
.ott-link:hover { opacity: 0.88; }
.ott-link span.arrow { font-size: 1.1rem; opacity: 0.85; }
a[href].ott-badge:active { opacity: 0.85; }
.fake-pay {
  margin-top: 0.5rem;
  padding: 0.9rem 1rem;
  border-radius: 12px;
  background: rgba(255,255,255,0.05);
  border: 1px dashed rgba(255,255,255,0.18);
  text-align: center;
  color: #8b93a7;
  font-size: 0.82rem;
}
.fake-pay .button-look {
  display: inline-block;
  margin-top: 0.45rem;
  padding: 0.45rem 1.2rem;
  border-radius: 8px;
  background: #3a4158;
  color: #c5cad8;
  font-weight: 600;
  font-size: 0.85rem;
}
.source-tag {
  font-size: 0.68rem;
  color: #7d879c;
  margin: 0.15rem 0 0.5rem;
}
.news-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin: 0.4rem 0 1rem;
}
.news-item {
  display: block;
  text-decoration: none !important;
  padding: 0.7rem 0.8rem;
  border-radius: 10px;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
  color: #e8ebf4 !important;
}
.news-item:hover { background: rgba(255,255,255,0.07); }
.news-title {
  font-size: 0.85rem;
  font-weight: 600;
  line-height: 1.35;
  margin-bottom: 0.25rem;
}
.news-desc {
  font-size: 0.72rem;
  color: #9aa3b8;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

div.stButton > button {
  width: 100%;
  border-radius: 10px !important;
  border: 1px solid rgba(255,255,255,0.12) !important;
  background: rgba(255,255,255,0.06) !important;
  color: #e8ebf4 !important;
  font-weight: 600 !important;
  padding: 0.45rem 0.6rem !important;
}
div.stButton > button:hover {
  border-color: rgba(168,180,255,0.45) !important;
  background: rgba(168,180,255,0.12) !important;
}
/* 장르 탭 (전체 / 드라마 / 예능) — 컬럼 버튼 */
.genre-tab-marker {
  display: none !important;
  height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
}
div[data-testid="stHorizontalBlock"]:has(.genre-tab-marker) {
  gap: 0.4rem !important;
  margin: 0.1rem 0 0.65rem !important;
}
div[data-testid="column"]:has(.genre-tab-marker) {
  width: 33.33% !important;
}
div[data-testid="column"]:has(.genre-tab-marker) div.stButton {
  margin: 0 !important;
}
div[data-testid="column"]:has(.genre-tab-marker) div.stButton > button {
  min-height: 2.55rem !important;
  font-size: 0.95rem !important;
  font-weight: 700 !important;
  letter-spacing: -0.02em !important;
  padding: 0.55rem 0.2rem !important;
  border-radius: 10px !important;
  white-space: nowrap !important;
  color: #f4f6fb !important;
}
div[data-testid="column"]:has(.genre-tab-marker) div.stButton > button[kind="primary"],
div[data-testid="column"]:has(.genre-tab-marker) button[data-testid="baseButton-primary"] {
  background: rgba(168, 180, 255, 0.32) !important;
  border: 1px solid rgba(190, 200, 255, 0.9) !important;
  color: #ffffff !important;
  font-weight: 800 !important;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def brand_header() -> None:
    user = st.session_state.user
    st.markdown(
        f"""
<div class="phone-shell">
  <div class="brand-bar">
    <div>
      <div class="brand-name">방송→OTT</div>
      <div class="brand-sub">지상파·PP 콘텐츠, 어디서 볼까?</div>
    </div>
    <div class="user-chip">👤 {html.escape(user["name"])}</div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def render_genre_tabs() -> str:
    """전체 / 드라마 / 예능 — 동일 너비 버튼 탭."""
    options = ["전체", "드라마", "예능"]
    current = st.session_state.get("genre") or "전체"
    if current not in options:
        current = "전체"

    cols = st.columns(3, gap="small")
    for col, label in zip(cols, options):
        with col:
            # CSS 타겟용 마커 (같은 컬럼에 버튼과 함께 둠)
            st.markdown(
                '<div class="genre-tab-marker" aria-hidden="true"></div>',
                unsafe_allow_html=True,
            )
            selected = label == current
            if st.button(
                label,
                key=f"genre_tab_{label}",
                use_container_width=True,
                type="primary" if selected else "secondary",
            ):
                if label != current:
                    st.session_state.list_page = 0
                st.session_state.genre = label
                st.rerun()

    return st.session_state.get("genre") or "전체"


def render_thumb(title: str, index: int, poster_url: str = "") -> str:
    poster_url = encode_media_url(poster_url)
    if poster_url and is_safe_poster_url(poster_url):
        src = html.escape(poster_url, quote=True)
        alt = html.escape(title, quote=True)
        return (
            f'<div style="flex-shrink:0;width:78px;height:104px;overflow:hidden;'
            f'border-radius:10px;background:#1a1d27;">'
            f'<img src="{src}" alt="{alt}" width="78" height="104" '
            f'style="width:78px;height:104px;object-fit:cover;display:block;border:0;" '
            f'referrerpolicy="no-referrer"/>'
            f"</div>"
        )
    c1, c2 = palette_for(index)
    g1, g2 = hex_to_rgb_css(c1), hex_to_rgb_css(c2)
    short = html.escape(title if len(title) <= 8 else title[:7] + "…")
    return (
        f'<div style="flex-shrink:0;width:78px;height:104px;border-radius:10px;'
        f'display:flex;align-items:flex-end;justify-content:center;padding:6px;'
        f'font-size:11px;font-weight:700;color:rgba(255,255,255,0.92);text-align:center;'
        f'line-height:1.2;overflow:hidden;background:linear-gradient(145deg,{g1},{g2});">'
        f"{short}</div>"
    )


def _unconfirmed_ott_html() -> str:
    return (
        '<span style="display:inline-block;margin-top:6px;padding:3px 8px;'
        'border-radius:999px;font-size:11px;font-weight:700;'
        'color:#ffb020;background:rgba(255,176,32,0.12);'
        'border:1px solid rgba(255,176,32,0.35);">[미확인]</span>'
    )


def ott_logo_badge_html(
    name: str,
    url: str,
    *,
    compact: bool = True,
) -> str:
    """OTT 로고 배지 + 바로가기 링크. 외부 이미지 없음(인라인 이니셜)."""
    meta = OTT_META.get(name)
    if not meta or not url:
        return ""
    bg = hex_to_rgb_css(meta["color"])
    letter = html.escape(str(meta.get("letter") or name[:1]))
    label = html.escape(name)
    safe_url = html.escape(url, quote=True)
    title_attr = html.escape(f"{name}에서 보기", quote=True)
    if compact:
        # 목록용: 로고 원형 + 짧은 라벨 (탭 영역 확보)
        return (
            f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer" '
            f'title="{title_attr}" '
            f'style="display:inline-flex;align-items:center;gap:5px;margin:2px 6px 0 0;'
            f'padding:3px 8px 3px 3px;border-radius:999px;text-decoration:none;'
            f'color:#fff;background:{bg};font-size:11px;font-weight:700;'
            f'line-height:1;vertical-align:middle;-webkit-tap-highlight-color:transparent;">'
            f'<span style="display:inline-flex;align-items:center;justify-content:center;'
            f'width:20px;height:20px;border-radius:50%;background:rgba(0,0,0,0.22);'
            f'font-size:9px;font-weight:800;letter-spacing:-0.02em;">{letter}</span>'
            f"<span>{label}</span></a>"
        )
    # 상세용: 넓은 로고 버튼
    return (
        f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer" '
        f'title="{title_attr}" '
        f'style="display:flex;align-items:center;justify-content:space-between;'
        f'gap:10px;padding:0.85rem 1rem;border-radius:12px;text-decoration:none;'
        f'color:#fff;background:{bg};font-weight:700;font-size:0.95rem;">'
        f'<span style="display:inline-flex;align-items:center;gap:10px;">'
        f'<span style="display:inline-flex;align-items:center;justify-content:center;'
        f'width:32px;height:32px;border-radius:10px;background:rgba(0,0,0,0.22);'
        f'font-size:13px;font-weight:800;">{letter}</span>'
        f"<span>{label}</span></span>"
        f'<span style="font-size:1.1rem;opacity:0.85;">↗</span></a>'
    )


def ott_pills_html(
    otts: list[str],
    title: str = "",
    ott_links: dict | None = None,
) -> str:
    """목록 카드용 클릭 가능 OTT 로고. URL은 캐시/검색 템플릿만 사용(네트워크 0)."""
    if not otts:
        return _unconfirmed_ott_html()
    parts = []
    for name in otts:
        if name not in OTT_META:
            continue
        url = ott_landing_url(name, title, ott_links)
        badge = ott_logo_badge_html(name, url, compact=True)
        if badge:
            parts.append(badge)
    if not parts:
        return _unconfirmed_ott_html()
    return (
        '<div style="margin-top:4px;line-height:1.5;display:flex;flex-wrap:wrap;'
        'align-items:center;">' + "".join(parts) + "</div>"
    )


def ott_detail_links_html(
    otts: list[str],
    title: str,
    ott_links: dict | None = None,
) -> str:
    parts = []
    for name in otts:
        if name not in OTT_META:
            continue
        url = ott_landing_url(name, title, ott_links)
        badge = ott_logo_badge_html(name, url, compact=False)
        if badge:
            parts.append(badge)
    if not parts:
        return ""
    return (
        '<div style="display:flex;flex-direction:column;gap:0.55rem;'
        'margin:0.5rem 0 1.2rem;">' + "".join(parts) + "</div>"
    )


def render_content_card(
    item: dict,
    *,
    index: int,
    airing: bool,
) -> None:
    """카드 1장 — components.html 로 렌더 (markdown HTML 깨짐/img 제거 방지)."""
    import streamlit.components.v1 as components

    aired = item["aired_at"].strftime("%Y.%m.%d")
    thumb = render_thumb(item["title"], index, item.get("poster_url") or "")
    pills = ott_pills_html(
        item.get("otts") or [],
        item.get("title") or "",
        item.get("ott_links") or {},
    )
    title_e = html.escape(item["title"])
    ch_e = html.escape(item["channel"])
    genre_e = html.escape(item["genre"])
    status = "방영 중" if airing else "종영"
    status_bg = "rgba(46,204,113,0.18)" if airing else "rgba(255,255,255,0.08)"
    status_fg = "#6dffb0" if airing else "#9aa3b8"
    rating_label = format_rating_label(item.get("rating"))
    rating_html = (
        f'<div style="margin-top:4px;font-size:12px;font-weight:700;color:#ffd666;">'
        f"{html.escape(rating_label)}</div>"
        if rating_label
        else ""
    )
    src_label = html.escape(source_label(item.get("ott_source", "none")))
    card = (
        '<div style="margin:0;padding:0;background:#141824;">'
        f'<div style="display:flex;gap:12px;padding:11px;margin:0;'
        f'border-radius:14px;background:rgba(255,255,255,0.04);'
        f'border:1px solid rgba(255,255,255,0.07);font-family:Pretendard,Apple SD Gothic Neo,'
        f'Noto Sans KR,sans-serif;color:#f2f4f8;">'
        f"{thumb}"
        f'<div style="flex:1;min-width:0;">'
        f'<div style="font-size:15px;font-weight:700;color:#fff;white-space:nowrap;'
        f'overflow:hidden;text-overflow:ellipsis;">{title_e}</div>'
        f'<div style="margin-top:4px;font-size:12px;color:#8b93a7;">'
        f'<span style="display:inline-block;padding:2px 7px;border-radius:999px;'
        f'font-size:11px;font-weight:700;color:{status_fg};background:{status_bg};'
        f'margin-right:4px;">{status}</span>'
        f'<span style="display:inline-block;padding:2px 7px;border-radius:999px;'
        f'font-size:11px;font-weight:700;color:#c5cad8;background:rgba(255,255,255,0.06);'
        f'margin-right:4px;">{genre_e}</span>'
        f"{ch_e} · {aired}</div>"
        f"{rating_html}{pills}"
        f'<div style="margin-top:4px;font-size:11px;color:#6b7280;">{src_label}</div>'
        f"</div></div></div>"
    )
    # OTT 로고 줄이 늘어날 수 있어 여유 높이
    n_otts = len(item.get("otts") or [])
    height = 132 if n_otts <= 2 else 148
    components.html(
        f'<!DOCTYPE html><html><body style="margin:0;background:#141824;">{card}</body></html>',
        height=height,
        scrolling=False,
    )


def render_list_thumb(title: str, index: int, poster_url: str = "") -> None:
    """(레거시) 목록 썸네일."""
    render_html(render_thumb(title, index, poster_url))


def ott_landing_url(ott_name: str, title: str, ott_links: dict | None = None) -> str:
    if ott_links and ott_links.get(ott_name):
        return ott_links[ott_name]
    meta = OTT_META.get(ott_name)
    if not meta:
        return "#"
    return meta["search"].format(query=quote(title))


def get_borragi_result(
    title: str,
    channel: str = "",
    *,
    ott_cache: dict | None = None,
    fetch_if_missing: bool = False,
    light: bool = True,
) -> dict:
    """
    OTT 결과. 디스크 캐시(OTT 있는 항목) → 세션 → (옵션) 네트워크 순.
    """
    key = f"ott_cache:{OTT_LOGIC_VERSION}:{title}"

    cached = get_cached_ott(title, ott_cache)
    if cached is not None:
        # 빈 결과도 '조회 완료'로 취급 — 매 로드 재요청 방지
        result = {
            "otts": list(cached.get("otts") or []),
            "ott_links": dict(cached.get("ott_links") or {}),
            "poster_url": "",
            "rating": None,
            "source": cached.get("source") or "none",
            "news": [],
            "query": title,
            "confirmed": bool(cached.get("otts")),
        }
        st.session_state[key] = result
        return result

    sess = st.session_state.get(key)
    if isinstance(sess, dict) and sess.get("source") not in (None, "pending"):
        return sess

    if not fetch_if_missing:
        return {
            "otts": [],
            "ott_links": {},
            "poster_url": "",
            "rating": None,
            "source": "pending",
            "news": [],
            "query": title,
            "confirmed": False,
        }

    result = resolve_otts_for_title(title, channel=channel, light=light)
    set_cached_ott(title, result, ott_cache)
    st.session_state[key] = result
    return result


def enrich_item(
    item: dict,
    ratings_cache: dict | None = None,
    poster_cache: dict | None = None,
    ott_cache: dict | None = None,
    *,
    fetch_ott: bool = False,
    light_ott: bool = True,
) -> dict:
    """네이버 보러가기 OTT + 공식 포스터 + 시청률 반영."""
    result = get_borragi_result(
        item["title"],
        item.get("channel") or "",
        ott_cache=ott_cache,
        fetch_if_missing=fetch_ott,
        light=light_ott,
    )
    otts = [o for o in (result.get("otts") or []) if o in OTT_META]
    rating = get_rating(item["title"], ratings_cache) or result.get("rating")
    # data 고정 → 디스크 캐시 → 실시간 결과 (깨진 dthumb/블로그 URL 차단)
    poster = resolve_poster_url(
        item,
        fetched=result.get("poster_url") or "",
        cache=poster_cache,
    )
    return {
        "id": item["id"],
        "title": item["title"],
        "genre": item["genre"],
        "channel": item["channel"],
        "broadcaster": item["broadcaster"],
        "aired_at": item["aired_at"],
        "episode": item["episode"],
        "desc": item["desc"],
        "otts": otts,
        "ott_links": {
            k: v
            for k, v in (result.get("ott_links") or {}).items()
            if k in otts and v
        },
        "poster_url": poster,
        "ott_source": result.get("source", "none"),
        "naver_news": list(result.get("news") or []),
        "rating": rating,
    }


def source_label(source: str) -> str:
    if source == "naver_borragi":
        return "네이버 검색 · 보러가기 OTT만 표시"
    if source == "pending":
        return "OTT 확인 대기 중"
    return "OTT 시청 여부 미확인"


def is_currently_airing(item: dict) -> bool:
    """episode 필드 기준. 예정/종영은 방영 중이 아님."""
    ep = str(item.get("episode") or "")
    if "방영 예정" in ep or "종영" in ep:
        return False
    return "방영 중" in ep


def sort_items_by_view_rate(items: list[dict], ratings_cache: dict | None) -> list[dict]:
    """현재 방영 중 우선 → 시청률 높은 순. 동률은 최근 방영일."""

    def rate_of(item: dict) -> float:
        rating = get_rating(item.get("title") or "", ratings_cache)
        if not rating or rating.get("view_rate") is None:
            return -1.0
        try:
            return float(rating["view_rate"])
        except (TypeError, ValueError):
            return -1.0

    return sorted(
        items,
        key=lambda it: (
            1 if is_currently_airing(it) else 0,
            rate_of(it),
            it.get("aired_at") or date.min,
        ),
        reverse=True,
    )


def filter_items_by_query(items: list[dict], query: str) -> list[dict]:
    """제목·채널·장르·설명으로 프로그램 검색."""
    q = (query or "").strip().lower()
    if not q:
        return items
    tokens = [t for t in q.split() if t]
    if not tokens:
        return items

    def matches(item: dict) -> bool:
        haystack = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("channel") or ""),
                str(item.get("broadcaster") or ""),
                str(item.get("genre") or ""),
                str(item.get("desc") or ""),
                str(item.get("episode") or ""),
            ]
        ).lower()
        return all(token in haystack for token in tokens)

    return [it for it in items if matches(it)]


# ---------------------------------------------------------------------------
# 화면: 홈
# ---------------------------------------------------------------------------
def view_home() -> None:
    brand_header()
    st.markdown(
        """
<div class="section-title">최근 방송</div>
<div class="section-desc">방영 중 우선 · 시청률 높은 순 · 네이버 ‘보러가기’ OTT</div>
        """,
        unsafe_allow_html=True,
    )

    # 검색은 폼으로 — 타이핑마다 전체 재실행(멈춤) 방지
    with st.form("search_form", clear_on_submit=False):
        typed = st.text_input(
            "프로그램 검색",
            value=st.session_state.get("search_query") or "",
            placeholder="프로그램 제목·채널 검색",
            label_visibility="collapsed",
            key="program_search_input",
        )
        submitted = st.form_submit_button("검색", use_container_width=True)
    if submitted:
        st.session_state.search_query = (typed or "").strip()
        st.session_state.list_page = 0
        st.rerun()

    search_query = st.session_state.get("search_query") or ""

    genre = render_genre_tabs()

    items = get_sorted_contents(genre)
    items = filter_items_by_query(items, search_query)
    if search_query.strip():
        render_html(
            f'<div class="search-hint">‘{html.escape(search_query.strip())}’ 검색 결과 '
            f"{len(items)}건</div>"
        )
    if not items:
        st.info("검색어·장르에 맞는 프로그램이 없습니다.")
        return

    all_contents = get_sorted_contents("전체")
    all_titles = [c["title"] for c in all_contents]
    channels = {c["title"]: c.get("channel") or "" for c in all_contents}
    col_a, col_b = st.columns(2)
    with col_a:
        refresh_otts = st.button("포스터·OTT 새로고침", key="refresh_otts")
    with col_b:
        refresh_ratings_btn = st.button("시청률 새로고침", key="refresh_ratings")

    # ---- 기본: 디스크/시드 캐시만 사용 (네트워크 0) → 즉시 화면 표시 ----
    ratings_cache = _load_ratings_cached()
    poster_cache = _load_posters_cached()
    ott_cache = _load_otts_cached()

    # 버튼으로만 네트워크 (자동 조회 없음)
    if refresh_ratings_btn:
        with st.spinner("시청률 갱신 중…"):
            from ratings import refresh_ratings

            ratings_cache = refresh_ratings(
                all_titles, force=True, channels=channels
            )
            _load_ratings_cached.clear()

    # 방영 중 우선 → 시청률 높은 순 → 현재 페이지
    items = sort_items_by_view_rate(items, ratings_cache)
    page = int(st.session_state.get("list_page") or 0)
    total_pages = max(1, (len(items) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    st.session_state.list_page = page
    start = page * LIST_PAGE_SIZE
    page_items = items[start : start + LIST_PAGE_SIZE]

    if refresh_otts:
        with st.spinner("포스터·OTT 새로고침 중…"):
            ensure_poster_cache(page_items, force=True, max_fetch=LIST_PAGE_SIZE)
            poster_cache = load_poster_cache()
            ott_cache = ensure_ott_cache(
                page_items,
                logic_version=OTT_LOGIC_VERSION,
                force=True,
                max_fetch=LIST_PAGE_SIZE,
                refetch_empty=False,
            )
            _load_posters_cached.clear()
            _load_otts_cached.clear()

    looked, confirmed, total = ott_cache_stats(all_titles, ott_cache)
    st.caption(cache_meta_label(ratings_cache))
    st.caption(f"OTT 확인 {confirmed}/{total} · 캐시로 즉시 표시 · 새로고침 시에만 네트워크")

    last_group: str | None = None
    enriched: list[dict] = []
    for i, raw in enumerate(page_items):
        item = enrich_item(
            raw,
            ratings_cache,
            poster_cache,
            ott_cache,
            fetch_ott=False,
        )
        enriched.append(item)

        airing = is_currently_airing(item)
        group = "airing" if airing else "ended"
        if group != last_group:
            if group == "airing":
                st.markdown(
                    '<p style="margin:10px 0 4px;font-size:13px;font-weight:700;color:#a8b4ff;">'
                    "방영 중 · 시청률 순</p>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<p style="margin:10px 0 4px;font-size:13px;font-weight:700;color:#a8b4ff;">'
                    "종영 · 시청률 순</p>",
                    unsafe_allow_html=True,
                )
            last_group = group

        render_content_card(item, index=start + i, airing=airing)

    # 버튼 12개 대신 selectbox 1개 — 위젯/리렌더 비용 감소
    if enriched:
        options = {f"{it['title']} ({it['channel']})": it["id"] for it in enriched}
        pick = st.selectbox(
            "상세 볼 프로그램",
            list(options.keys()),
            label_visibility="collapsed",
            key=f"pick_page_{page}",
        )
        if st.button("상세·시청률 보기 →", key="open_selected", use_container_width=True):
            go_detail(options[pick])
            st.rerun()

    nav_l, nav_m, nav_r = st.columns([1, 2, 1])
    with nav_l:
        if st.button("← 이전", disabled=page <= 0, key="list_prev", use_container_width=True):
            st.session_state.list_page = page - 1
            st.rerun()
    with nav_m:
        st.caption(f"{page + 1} / {total_pages} 페이지 · {len(items)}편")
    with nav_r:
        if st.button(
            "다음 →",
            disabled=page >= total_pages - 1,
            key="list_next",
            use_container_width=True,
        ):
            st.session_state.list_page = page + 1
            st.rerun()


# ---------------------------------------------------------------------------
# 화면: 상세
# ---------------------------------------------------------------------------
def view_detail() -> None:
    raw = get_content_by_id(st.session_state.selected_id or "")
    if not raw:
        st.warning("콘텐츠를 찾을 수 없습니다.")
        if st.button("← 목록으로"):
            go_home()
            st.rerun()
        return

    if st.button("← 목록으로", key="back_home"):
        go_home()
        st.rerun()

    ratings_cache = _load_ratings_cached()
    poster_cache = _load_posters_cached()
    ott_cache = _load_otts_cached()
    # 상세도 캐시만 사용 (진입 시 네트워크 금지 → 즉시 표시)
    item = enrich_item(
        raw,
        ratings_cache,
        poster_cache,
        ott_cache,
        fetch_ott=False,
        light_ott=True,
    )

    rating = item.get("rating") or get_rating(item["title"], ratings_cache) or {}
    episodes = get_episode_ratings(rating)

    if st.button("이 작품 OTT·시청률 새로고침", key="detail_refresh"):
        with st.spinner("불러오는 중…"):
            from ratings import fetch_rating_for_title, save_cache

            result = resolve_otts_for_title(
                item["title"],
                channel=item.get("channel") or "",
                light=True,
            )
            set_cached_ott(item["title"], result, ott_cache)
            fresh = fetch_rating_for_title(
                item["title"], channel=item.get("channel") or ""
            )
            if fresh:
                items_map = dict(ratings_cache.get("items") or {})
                items_map[item["title"]] = fresh
                save_cache({**ratings_cache, "items": items_map})
            _load_ratings_cached.clear()
            _load_otts_cached.clear()
            st.rerun()

    sorted_all = get_sorted_contents("전체")
    try:
        idx = next(i for i, c in enumerate(sorted_all) if c["id"] == item["id"])
    except StopIteration:
        idx = 0
    c1, c2 = palette_for(idx)
    title_e = html.escape(item["title"])
    aired = item["aired_at"].strftime("%Y년 %m월 %d일")
    poster = item.get("poster_url") or ""
    rating_label = format_rating_label(rating)
    air_date = html.escape((rating.get("air_date") or "").rstrip("."))
    rating_type = rating.get("type") or ""
    type_label = "최고" if rating_type == "HIGHEST" else "최신"
    muted = hex_to_rgb_css("#9aa3b8")
    if rating_label:
        rating_block = (
            f'<div class="rating-detail">{html.escape(rating_label)}'
            f' <span style="font-weight:500;color:{muted};">({type_label})</span></div>'
            f'<div class="rating-sub">'
            f'{html.escape(cache_meta_label(ratings_cache))}'
            + (f' · 방영 {air_date}' if air_date else "")
            + "</div>"
        )
    else:
        rating_block = (
            f'<div class="rating-sub">{html.escape(cache_meta_label(ratings_cache))}'
            f' · 시청률 정보 없음</div>'
        )

    if episodes:
        rows = []
        for ep in episodes:
            rows.append(
                "<div class=\"ep-rating-row\">"
                f"<div class=\"ep-num\">{html.escape(ep['episode'] or '-')}</div>"
                f"<div class=\"ep-date\">{html.escape((ep['air_date'] or '-').rstrip('.'))}</div>"
                f"<div class=\"ep-rate\">{html.escape(format_rate_percent(ep['view_rate']))}</div>"
                "</div>"
            )
        episode_block = (
            '<div class="section-title" style="margin-top:0.85rem;">회차별 시청률</div>'
            f'<div class="ep-rating-note">총 {len(episodes)}회 · 최신 회차 순</div>'
            '<div class="ep-rating-wrap">'
            '<div class="ep-rating-head"><div>회차</div><div>방영일</div><div style="text-align:right;">시청률</div></div>'
            + "".join(rows)
            + "</div>"
        )
    else:
        episode_block = (
            '<div class="section-title" style="margin-top:0.85rem;">회차별 시청률</div>'
            '<div class="ep-rating-note">회차별 시청률 정보가 없습니다.</div>'
        )

    white = hex_to_rgb_css("#ffffff")
    poster = encode_media_url(poster) if poster else ""
    if poster and is_safe_poster_url(poster):
        hero = (
            f'<img class="detail-poster" src="{html.escape(poster, quote=True)}" '
            f'alt="{title_e}" width="400" height="280" '
            f'style="width:100%;max-height:280px;object-fit:cover;border-radius:16px;'
            f'display:block;margin-bottom:1rem;background:#1a1d27;" '
            f'loading="eager" referrerpolicy="no-referrer"/>'
            f'<h1 style="margin:0 0 0.75rem;font-size:1.35rem;font-weight:800;'
            f'color:{white};">{title_e}</h1>'
        )
    else:
        g1, g2 = hex_to_rgb_css(c1), hex_to_rgb_css(c2)
        hero = (
            f'<div class="detail-hero" style="background:linear-gradient(160deg,{g1},{g2});">'
            f"<h1>{title_e}</h1></div>"
        )

    render_html(
        f"""
{hero}
<div class="detail-meta">
  <span class="badge-genre">{html.escape(item["genre"])}</span>
  {html.escape(item["broadcaster"])}<br/>
  방영일 {aired} · {html.escape(item["episode"])}
</div>
{rating_block}
{episode_block}
<div class="detail-desc">{html.escape(item["desc"])}</div>
        """
    )

    render_html('<div class="section-title">시청 가능한 OTT</div>')
    render_html(
        f'<div class="source-tag">{html.escape(source_label(item.get("ott_source", "none")))}</div>'
    )

    if not item["otts"]:
        render_html(
            '<p class="unconfirmed" style="font-size:1rem;margin:0.6rem 0 1rem;">'
            "[미확인] — 네이버 보러가기에 표시된 OTT가 없습니다.</p>"
        )
    else:
        import streamlit.components.v1 as components

        grid = ott_detail_links_html(
            item["otts"],
            item["title"],
            item.get("ott_links") or {},
        )
        # 링크 클릭이 안정적으로 동작하도록 iframe HTML 사용 (추가 네트워크 없음)
        components.html(
            '<!DOCTYPE html><html><body style="margin:0;background:transparent;'
            'font-family:Pretendard,Apple SD Gothic Neo,Noto Sans KR,sans-serif;">'
            f"{grid}</body></html>",
            height=min(72 + 64 * len(item["otts"]), 320),
            scrolling=False,
        )
        st.caption("로고를 누르면 해당 OTT로 이동합니다. (네이버 보러가기 기준)")

    news = item.get("naver_news") or []
    if news:
        st.markdown(
            '<div class="section-title">관련 뉴스 (네이버)</div>',
            unsafe_allow_html=True,
        )
        news_html = ['<div class="news-list">']
        for n in news:
            n_title = html.escape(n.get("title") or "")
            n_desc = html.escape(n.get("description") or "")
            n_link = html.escape(n.get("link") or "#")
            news_html.append(
                f'<a class="news-item" href="{n_link}" target="_blank" rel="noopener">'
                f'<div class="news-title">{n_title}</div>'
                f'<div class="news-desc">{n_desc}</div></a>'
            )
        news_html.append("</div>")
        st.markdown("".join(news_html), unsafe_allow_html=True)

    st.markdown(
        """
<div class="fake-pay">
  프리미엄 알림 (데모)
  <br/><span class="button-look">결제하기 (샘플 · 동작 없음)</span>
</div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def main() -> None:
    init_session()
    inject_css()

    if st.session_state.view == "home":
        st.caption(
            f"테스트 계정 `{TEST_USER['id']}` 자동 로그인 · "
            f"기준일 {date.today().isoformat()} · 모바일 비율"
        )
        view_home()
    else:
        view_detail()


if __name__ == "__main__":
    main()
