"""네이버 검색 '보러가기' OTT · 공식 포스터 · 시청률 추출."""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import quote

import requests

from config import get_naver_credentials, naver_credentials_configured

NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
MOBILE_SEARCH = "https://m.search.naver.com/search.naver"

# 보러가기 플랫폼명 → 앱에서 쓰는 OTT 키
_PLATFORM_MAP = {
    "넷플릭스": "넷플릭스",
    "티빙": "티빙",
    "웨이브": "웨이브",
    "쿠팡플레이": "쿠팡플레이",
    "쿠팡 플레이": "쿠팡플레이",
    "디즈니+": "디즈니+",
    "디즈니플러스": "디즈니+",
    "디즈니 플러스": "디즈니+",
    "왓챠": "왓챠",
}

_TAG_RE = re.compile(r"<[^>]+>")
# 보러가기 섹션 시작 (CSS 모듈 해시가 바뀌어도 _cmAreaTitle_ 접두로 매칭)
_BORRAGI_TITLE_RE = re.compile(
    r'_cmAreaTitle_[^"]*">\s*보러가기\s*</div>',
    re.IGNORECASE,
)
_PLATFORM_LIST_RE = re.compile(
    r'<ul class="_platformList_[^"]*">([\s\S]*?)</ul>',
    re.IGNORECASE,
)
_PLATFORM_CONTENT_RE = re.compile(
    r'_platformContent_[^"]*"[^>]*>([\s\S]{0,20000}?)</ul>\s*</div>',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r'_infoTitle_[^"]*">([^<]+)</strong>')
_ALT_RE = re.compile(r'<img[^>]+alt="([^"]+)"', re.IGNORECASE)
_VIEW_LINK_RE = re.compile(
    r'_viewLink_[^"]*"[^>]*href="([^"]+)"[^>]*>[\s\S]{0,400}?</a>',
    re.IGNORECASE,
)
_ITEM_RE = re.compile(r'<li class="listItem"[^>]*>([\s\S]*?)</li>', re.IGNORECASE)


def _strip_html(text: str) -> str:
    return unescape(_TAG_RE.sub("", text or "")).strip()


def _headers_api() -> dict[str, str]:
    client_id, client_secret = get_naver_credentials()
    return {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }


def _headers_mobile() -> dict[str, str]:
    # iPhone Safari UA 는 최근 403 차단되는 경우가 있어 Android Chrome 사용
    return {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13; SM-S908N) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://m.naver.com/",
        "Cache-Control": "no-cache",
    }


def _headers_desktop() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.naver.com/",
    }


def search_news(query: str, display: int = 10, sort: str = "sim") -> list[dict[str, Any]]:
    """관련 뉴스(참고용). Open API 사용."""
    if not naver_credentials_configured():
        return []
    try:
        res = requests.get(
            NEWS_URL,
            headers=_headers_api(),
            params={"query": query, "display": display, "start": 1, "sort": sort},
            timeout=8,
        )
        if res.status_code != 200:
            return []
        items = res.json().get("items", [])
        return [
            {
                "title": _strip_html(it.get("title", "")),
                "description": _strip_html(it.get("description", "")),
                "link": it.get("link") or it.get("originallink") or "",
                "pubDate": it.get("pubDate", ""),
            }
            for it in items
        ]
    except requests.RequestException:
        return []


def _normalize_platform(name: str) -> str | None:
    """보러가기 라벨 정확 매칭만 허용 (부분 매칭으로 다른 OTT가 섞이지 않게)."""
    cleaned = unescape(name or "").strip()
    return _PLATFORM_MAP.get(cleaned)


def _extract_item_link(item_html: str) -> str:
    m = _VIEW_LINK_RE.search(item_html)
    if m:
        return unescape(m.group(1)).replace("&amp;", "&")
    # fallback: any ott content url in item
    for pat, _ in [
        (r'https://www\.tving\.com/contents/[^"\s]+', "티빙"),
        (r'https://www\.wavve\.com/[^"\s]+', "웨이브"),
        (r'https://www\.netflix\.com/[^"\s]+', "넷플릭스"),
        (r'https://www\.coupangplay\.com/[^"\s]+', "쿠팡플레이"),
        (r'https://www\.disneyplus\.com/[^"\s]+', "디즈니+"),
        (r'https://watcha\.com/[^"\s]+', "왓챠"),
    ]:
        found = re.search(pat, item_html, re.I)
        if found:
            return unescape(found.group(0)).replace("&amp;", "&")
    return ""


_DOMAIN_TO_OTT = (
    (re.compile(r"tving\.com", re.I), "티빙"),
    (re.compile(r"wavve\.com", re.I), "웨이브"),
    (re.compile(r"netflix\.com", re.I), "넷플릭스"),
    (re.compile(r"coupangplay\.com", re.I), "쿠팡플레이"),
    (re.compile(r"disneyplus\.com", re.I), "디즈니+"),
    (re.compile(r"watcha\.com", re.I), "왓챠"),
)


def _is_web_watch_url(url: str) -> bool:
    """브라우저에서 열 수 있는 http(s) 시청 링크만 허용."""
    u = (url or "").strip().lower()
    if not u.startswith("http://") and not u.startswith("https://"):
        return False
    if "naver.com" in u or "pstatic.net" in u:
        return False
    return True


def _links_by_ott_from_chunk(chunk: str) -> dict[str, str]:
    """보러가기 영역 전체에서 OTT별 시청 링크 수집."""
    found: dict[str, str] = {}
    hrefs = re.findall(r'href="((?:https?://|watcha://)[^"]+)"', chunk, re.I)
    for href in hrefs:
        url = unescape(href).replace("&amp;", "&")
        if not _is_web_watch_url(url):
            continue
        for domain_re, ott in _DOMAIN_TO_OTT:
            if domain_re.search(url) and ott not in found:
                found[ott] = url
                break
    return found


def _borragi_section_html(html: str) -> str:
    """첫 번째 '보러가기' 패널 HTML. 접힌/펼친 platformList 를 모두 포함."""
    if not html:
        return ""
    title_m = _BORRAGI_TITLE_RE.search(html)
    if title_m:
        # 다음 cmAreaTitle 또는 충분한 윈도우까지
        start = title_m.start()
        window = html[start : start + 25000]
        next_title = _BORRAGI_TITLE_RE.search(window, pos=len(title_m.group(0)))
        # 다른 영역 타이틀(_cmAreaTitle_)이 나오면 그 전까지만
        other = re.search(r'_cmAreaTitle_[^"]*">', window[len(title_m.group(0)) :])
        if other:
            end = len(title_m.group(0)) + other.start()
            # '보러가기'가 두 번 있을 수 있어, 첫 패널 콘텐츠는 platformContent 우선
            content_m = _PLATFORM_CONTENT_RE.search(window)
            if content_m and content_m.end() <= end + 500:
                return window[: max(end, content_m.end())]
            return window[:end]
        content_m = _PLATFORM_CONTENT_RE.search(window)
        if content_m:
            return window[: max(content_m.end(), 8000)]
        return window

    # 타이틀 마커가 없으면 platformList 가 있는 덩어리만
    lists = list(_PLATFORM_LIST_RE.finditer(html))
    if not lists:
        return ""
    start = max(0, lists[0].start() - 200)
    end = lists[-1].end() + 200
    # 페이지 전체가 과하면 앞쪽 2~3개 ul 만
    if len(lists) > 3:
        end = lists[2].end() + 200
    return html[start:end]


def _labels_from_chunk(chunk: str) -> list[str]:
    labels: list[str] = []
    items = _ITEM_RE.findall(chunk)
    if items:
        for item_html in items:
            titles = _TITLE_RE.findall(item_html)
            alts = _ALT_RE.findall(item_html)
            label = titles[0] if titles else (alts[0] if alts else "")
            if label:
                labels.append(label)
    else:
        labels = _TITLE_RE.findall(chunk) + _ALT_RE.findall(chunk)
    return labels


def parse_borragi_platforms(html: str) -> list[dict[str, str]]:
    """
    네이버 검색 HTML의 '보러가기' 영역에서 플랫폼 추출.
    Returns: [{"name": "티빙", "url": "..."}, ...]  (우리 OTT 키만)
    """
    section = _borragi_section_html(html)
    if not section:
        return []

    # 접힌 ul(링크 없음) + 펼친 ul(링크 있음)을 모두 합친다
    ul_chunks = _PLATFORM_LIST_RE.findall(section)
    if not ul_chunks:
        ul_chunks = [section]

    labels: list[str] = []
    for chunk in ul_chunks:
        labels.extend(_labels_from_chunk(chunk))
    if not labels:
        labels = _labels_from_chunk(section)

    link_map = _links_by_ott_from_chunk(section)
    # listItem 단위로 링크 보강
    for item_html in _ITEM_RE.findall(section):
        titles = _TITLE_RE.findall(item_html)
        alts = _ALT_RE.findall(item_html)
        label = titles[0] if titles else (alts[0] if alts else "")
        mapped = _normalize_platform(label)
        if not mapped or mapped in link_map:
            continue
        item_link = _extract_item_link(item_html)
        if item_link and _is_web_watch_url(item_link):
            for domain_re, ott in _DOMAIN_TO_OTT:
                if domain_re.search(item_link) and ott == mapped:
                    link_map[mapped] = item_link
                    break
            else:
                if mapped not in link_map:
                    link_map[mapped] = item_link

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for label in labels:
        mapped = _normalize_platform(label)
        if not mapped or mapped in seen:
            continue
        seen.add(mapped)
        results.append({"name": mapped, "url": link_map.get(mapped, "")})

    # 라벨은 없지만 링크만 있는 경우 (마크업 변형)
    if not results and link_map:
        for name, url in link_map.items():
            results.append({"name": name, "url": url})

    return results


def _clean_img_url(url: str) -> str:
    from urllib.parse import unquote

    u = unescape(url or "").replace("&amp;", "&")
    for _ in range(2):
        u = unquote(u)
    u = u.strip().strip('"').strip("'").strip("\\")
    # dthumb / 중첩 래퍼 안 원본 phinf URL
    if "dthumb" in u or u.count("http") > 1:
        m = re.search(
            r"(https?://(?:csearch-phinf|ssl|phinf)\.pstatic\.net/[^\s\"'&]+)",
            u,
            re.I,
        )
        if m:
            u = m.group(1)
    return u.rstrip("\\").rstrip("&")


def _unwrap_poster_src(url: str) -> str:
    """dthumb / search.pstatic 래퍼를 풀어 원본 이미지 URL 반환."""
    from urllib.parse import parse_qs, unquote, urlparse

    u = _clean_img_url(url)
    if not u:
        return ""

    for _ in range(5):
        prev = u
        decoded = unquote(u)
        if "dthumb-phinf.pstatic.net" in decoded or "dthumb-phinf.pstatic.net" in u:
            m = re.search(
                r"(https?://(?:csearch-phinf|ssl|phinf|mblogthumb-phinf|pup-post-phinf|"
                r"blogfiles\.pstatic|postfiles\.pstatic)\.net/[^\s\"'<>]+)",
                decoded,
                re.I,
            )
            if m:
                u = _clean_img_url(m.group(1))
            else:
                m2 = re.search(
                    r"src=(?:\"|%22|')?(https?://[^\"'<>]+)",
                    decoded,
                    re.I,
                )
                if m2:
                    u = _clean_img_url(m2.group(1).split("&opts=")[0].split("&twidth=")[0])
        if "search.pstatic.net/common" in u and "src=" in u:
            qs = parse_qs(urlparse(u.replace("&amp;", "&")).query)
            src = qs.get("src", [""])[0]
            if src:
                u = _clean_img_url(unquote(src))
        if u == prev:
            break

    if u.startswith("//"):
        u = "https:" + u
    # 쿼리에 남은 잘못된 따옴표 제거
    u = u.replace('"', "").replace("'", "")
    return u


def is_safe_poster_url(url: str) -> bool:
    """HTML src 에 넣어도 레이아웃이 깨지지 않는 URL인지 검사."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    if any(ch in url for ch in ('"', "'", "<", ">", "`", "\n", "\r")):
        return False
    if "dthumb-phinf.pstatic.net" in url:
        return False
    if "%22" in url or "%27" in url:
        return False
    return True


def _to_display_poster(url: str) -> str:
    """포스터 URL을 표시용으로 정규화."""
    u = _unwrap_poster_src(url)
    if not is_safe_poster_url(u):
        return ""
    return u

def extract_os_id(html: str) -> str | None:
    """방송 프로그램 os id (pkid=57) 우선. 에피소드(pkid=59)는 제외."""
    m = re.search(r"pkid=57(?:&|&amp;)os=(\d+)", html)
    if m:
        return m.group(1)
    m = re.search(r"os=(\d+)(?:&|&amp;)[^\"']*pkid=57", html)
    if m:
        return m.group(1)
    for pat in (
        r"contents/broadcast/(\d+)",
        r"[?&]os=(\d+)",
        r'data-cid="(\d+)"',
    ):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


def extract_poster_from_search_html(html: str, os_id: str | None = None) -> str:
    """검색 결과의 공식 main_image / 해당 프로그램 poster_image 추출."""
    from urllib.parse import unquote

    patterns = (
        r'src="(https://search\.pstatic\.net/common\?[^"]*main_image[^"]*)"',
        r'src="(https://csearch-phinf\.pstatic\.net/[^"]*main_image[^"]*)"',
        r'(https://csearch-phinf\.pstatic\.net/[^"\\\s]+main_image[^"\\\s]+)',
        r'src="(https://search\.pstatic\.net/common\?src=https%3A%2F%2Fcsearch-phinf[^"]*main_image[^"]*)"',
    )
    for pat in patterns:
        found = re.findall(pat, html, re.I)
        if found:
            poster = _to_display_poster(found[0])
            if poster:
                return poster

    # 프로그램 os 와 매칭되는 공식 poster_image 만 허용
    if os_id:
        m = re.search(
            rf"(https://(?:csearch-phinf|ssl)\.pstatic\.net/[^\"'\\\s]*{re.escape(os_id)}[^\"'\\\s]*poster_image[^\"'\\\s]*)",
            html,
            re.I,
        )
        if m:
            poster = _to_display_poster(m.group(1))
            if poster:
                return poster

    # 인코딩된 csearch 이미지가 1장뿐이면 사용 (예: 나는 자연인이다)
    encoded = re.findall(
        r"(csearch-phinf\.pstatic\.net%2F[^\"'\\\s]+\.jpg)",
        html,
        re.I,
    )
    plain = re.findall(
        r"(https://csearch-phinf\.pstatic\.net/[^\"'\\\s]+\.jpg)",
        html,
        re.I,
    )
    # main/poster 라벨 없는 이미지는 '유일'할 때만 폴백
    unique = []
    for raw in encoded + plain:
        u = unquote(raw)
        if not u.startswith("http"):
            u = "https://" + u
        if "poster_image" in u or "main_image" in u:
            continue
        if u not in unique:
            unique.append(u)
    if len(unique) == 1:
        return _to_display_poster(unique[0])
    return ""


def _poster_score(url: str, channel: str = "") -> int:
    prefer = (
        "cjenm.com",
        "csearch-phinf.pstatic.net",
        "image.tving.com",
        "tving.com",
        "ssl.pstatic.net/sstatic/keypage",
        "img.extmovie.com",
        "talkimg.imbc.com",
        "img.imbc.com",
        "i.namu.wiki",
        "cdn.instiz.net",
        "imbc.com",
        "kbs.co.kr",
        "sbs.co.kr",
        "jtbc.co.kr",
    )
    penalize = (
        "imgnews.naver.net",
        "dthumb-phinf",
        "mblogthumb",
        "pup-post-phinf",
        "blogfiles",
        "influencer-phinf",
        "ytimg.com",
        "twimg.com",
    )
    if not is_safe_poster_url(url):
        return -999
    score = 0
    for i, p in enumerate(prefer):
        if p in url:
            score += 80 - i
            break
    for p in penalize:
        if p in url:
            score -= 50
    # 지상파(KBS/SBS/MBC)인데 티빙 전용 이미지가 잡히면 감점 (오매칭 방지)
    ch = (channel or "").upper()
    if any(x in ch for x in ("KBS", "SBS", "MBC")) and ("tving.com" in url):
        score -= 60
    low = url.lower()
    if "poster" in low or "main_image" in low:
        score += 25
    if low.endswith((".jpg", ".jpeg", ".png", ".webp")):
        score += 5
    return score


def fetch_poster_via_image_api(title: str, channel: str = "") -> str:
    """네이버 이미지 검색 API로 포스터 후보 조회 (폴백)."""
    if not naver_credentials_configured():
        return ""

    queries = [f"{title} 포스터"]
    if channel:
        queries.insert(0, f"{title} {channel} 포스터")
        queries.append(f"{title} {channel}")
    queries.append(f"{title} 드라마 포스터")
    queries.append(f"{title} 예능 포스터")

    candidates: list[str] = []
    for query in queries:
        try:
            res = requests.get(
                "https://openapi.naver.com/v1/search/image",
                headers=_headers_api(),
                params={
                    "query": query,
                    "display": 10,
                    "sort": "sim",
                    "filter": "large",
                },
                timeout=8,
            )
            if res.status_code != 200:
                continue
            items = res.json().get("items", [])
        except (requests.RequestException, ValueError):
            continue

        for it in items:
            link = _to_display_poster((it.get("link") or "").strip())
            if link and is_safe_poster_url(link):
                candidates.append(link)

    if not candidates:
        return ""
    # 중복 제거 후 점수순
    uniq: list[str] = []
    for u in candidates:
        if u not in uniq:
            uniq.append(u)
    uniq.sort(key=lambda u: _poster_score(u, channel), reverse=True)
    return uniq[0]


def extract_poster_from_entertain_html(html: str, title: str) -> str:
    """네이버 방송 상세(각사 공식 포스터 미러)에서 포스터 추출."""
    # 1) alt에 제목이 있는 첫 이미지
    for m in re.finditer(r"<img[^>]+>", html[:80000], re.I):
        tag = m.group(0)
        alt_m = re.search(r'alt="([^"]*)"', tag, re.I)
        src_m = re.search(r'src="([^"]+)"', tag, re.I)
        if not src_m:
            continue
        alt = unescape(alt_m.group(1)) if alt_m else ""
        src = _clean_img_url(src_m.group(1))
        if title.replace(" ", "") in alt.replace(" ", "") or (
            len(title) >= 2 and title[:2] in alt
        ):
            if "platform_logo" in src:
                continue
            if any(k in src for k in ("csearch-phinf", "poster_image", "phinf", "keypage")):
                return _to_display_poster(src)

    # 2) poster_image 직접 URL
    posters = re.findall(
        r'(https://(?:csearch-phinf|ssl)\.pstatic\.net/[^"\\\s]+poster_image[^"\\\s]+)',
        html,
        re.I,
    )
    if posters:
        return _to_display_poster(posters[0].rstrip("\\"))

    # 3) 첫 csearch-phinf jpg
    phinf = re.findall(
        r'(https://csearch-phinf\.pstatic\.net/[^"\\\s]+\.jpg)',
        html,
        re.I,
    )
    if phinf:
        return _to_display_poster(phinf[0])
    return ""


def fetch_poster_url(title: str, search_html: str = "", channel: str = "") -> str:
    """검색 HTML → 방송 상세 → 이미지 검색 API 순으로 포스터 URL."""
    if not search_html:
        search_html = fetch_search_html(title)
        # 짧은 제목/검색 실패 시 채널 포함 재시도
        if (not search_html or len(search_html) < 5000) and channel:
            search_html = fetch_search_html(f"{title} {channel}") or search_html

    os_id = extract_os_id(search_html) if search_html else None
    candidates: list[str] = []

    if search_html:
        poster = extract_poster_from_search_html(search_html, os_id)
        if is_safe_poster_url(poster):
            candidates.append(poster)

    if os_id:
        try:
            res = requests.get(
                f"https://m.entertain.naver.com/contents/broadcast/{os_id}",
                headers=_headers_mobile(),
                timeout=12,
            )
            if res.status_code == 200:
                poster = extract_poster_from_entertain_html(res.text, title)
                if is_safe_poster_url(poster):
                    candidates.append(poster)
        except requests.RequestException:
            pass

    api_poster = fetch_poster_via_image_api(title, channel=channel)
    if is_safe_poster_url(api_poster):
        candidates.append(api_poster)

    if not candidates:
        return ""
    candidates.sort(key=lambda u: _poster_score(u, channel), reverse=True)
    return candidates[0]


def _search_html_score(html: str, status_code: int) -> int:
    """OTT 보러가기 파싱에 유리한 HTML일수록 높은 점수."""
    if status_code != 200 or not html or len(html) < 15000:
        return 0
    score = 1
    if "_platformList_" in html:
        score += 100
    if "_platformContent_" in html:
        score += 40
    if "_infoTitle_" in html:
        score += 30
    if "_cmAreaTitle_" in html and "보러가기" in html:
        score += 40
    elif "보러가기" in html:
        score += 8
    if "viewRate" in html or "pkid=57" in html:
        score += 5
    if "platform_logo_" in html:
        score += 20
    return score


def fetch_search_html(title: str, *, max_attempts: int = 2) -> str:
    """네이버 검색 HTML. platformList 가 있는 응답을 우선 선택."""
    attempts = (
        (
            # 모바일 where=m 이 platformList 를 더 자주 포함
            MOBILE_SEARCH,
            _headers_mobile(),
            {"where": "m", "sm": "mtp_hty.top", "query": title},
        ),
        (
            MOBILE_SEARCH,
            _headers_desktop(),
            {"where": "m", "sm": "mtp_hty.top", "query": title},
        ),
        (
            "https://search.naver.com/search.naver",
            _headers_desktop(),
            {"query": title},
        ),
    )
    best_html = ""
    best_score = 0
    for url, headers, params in attempts[: max(1, max_attempts)]:
        try:
            res = requests.get(url, headers=headers, params=params, timeout=8)
            score = _search_html_score(res.text, res.status_code)
            if score > best_score:
                best_score = score
                best_html = res.text
                # platformList 확보 시 추가 요청 생략
                if score >= 100:
                    break
        except requests.RequestException:
            continue
    return best_html if best_score > 0 else ""


def fetch_borragi_otts(title: str, search_html: str = "") -> list[dict[str, str]]:
    """네이버 검색에서 프로그램명 → 보러가기 OTT 목록."""
    if search_html:
        platforms = parse_borragi_platforms(search_html)
        if platforms:
            return platforms

    html = fetch_search_html(f"{title} 보러가기", max_attempts=2)
    if html:
        platforms = parse_borragi_platforms(html)
        if platforms:
            return platforms

    html = fetch_search_html(title, max_attempts=1)
    return parse_borragi_platforms(html) if html else []


_VIEW_RATE_RE = re.compile(
    r'"viewRate"\s*:\s*\{\s*"type"\s*:\s*"(LATEST|HIGHEST)"\s*,\s*"items"\s*:\s*(\[[^\]]*\])\s*\}',
    re.IGNORECASE,
)
_EPISODE_OBJ_RE = re.compile(
    r'\{[^{}]{0,200}"episode"\s*:\s*"[^"]+"[^{}]{0,200}"viewRate"\s*:\s*[0-9.]+[^{}]{0,80}\}',
    re.IGNORECASE,
)


def parse_view_rate(html: str) -> dict[str, Any] | None:
    """
    네이버 검색 패널의 viewRate JSON 파싱.
    반환 예: {view_rate, episode, air_date, channel, type}
    """
    if not html:
        return None
    m = _VIEW_RATE_RE.search(html)
    if not m:
        return None
    kind = m.group(1).upper()
    try:
        items = json.loads(m.group(2))
    except json.JSONDecodeError:
        return None
    if not items or not isinstance(items, list):
        return None
    item = items[0]
    if not isinstance(item, dict):
        return None
    rate = item.get("viewRate")
    try:
        rate_f = float(rate)
    except (TypeError, ValueError):
        return None
    return {
        "view_rate": rate_f,
        "episode": str(item.get("episode") or "").strip(),
        "air_date": str(item.get("airDate") or "").strip(),
        "channel": str(item.get("channelName") or "").strip(),
        "type": kind,  # LATEST | HIGHEST
    }


def parse_view_rate_history(html: str) -> list[dict[str, Any]]:
    """
    '{제목} 시청률' 검색 HTML에서 회차별 시청률 목록 추출.
    최신 회차 순으로 정렬된 [{episode, air_date, view_rate, channel}, ...]
    """
    if not html:
        return []

    episodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _EPISODE_OBJ_RE.findall(html):
        ep_m = re.search(r'"episode"\s*:\s*"([^"]+)"', raw)
        rate_m = re.search(r'"viewRate"\s*:\s*([0-9.]+)', raw)
        date_m = re.search(r'"airDate"\s*:\s*"([^"]+)"', raw)
        ch_m = re.search(r'"channelName"\s*:\s*"([^"]+)"', raw)
        if not ep_m or not rate_m:
            continue
        episode = ep_m.group(1).strip()
        if not episode or episode in seen:
            continue
        try:
            rate_f = float(rate_m.group(1))
        except ValueError:
            continue
        seen.add(episode)
        air = (date_m.group(1) if date_m else "").strip().rstrip(".")
        episodes.append(
            {
                "episode": episode,
                "air_date": air,
                "view_rate": rate_f,
                "channel": (ch_m.group(1) if ch_m else "").strip(),
            }
        )

    def _ep_num(item: dict[str, Any]) -> int:
        m = re.search(r"(\d+)", str(item.get("episode") or ""))
        return int(m.group(1)) if m else 0

    episodes.sort(key=_ep_num, reverse=True)
    return episodes


def fetch_view_rate(title: str, search_html: str = "") -> dict[str, Any] | None:
    """프로그램 최신(또는 최고) 시청률."""
    html = search_html or fetch_search_html(title)
    return parse_view_rate(html)


def fetch_view_rate_with_history(title: str) -> dict[str, Any] | None:
    """최신 시청률 + 회차별 시청률 목록."""
    html = fetch_search_html(f"{title} 시청률", max_attempts=2)
    episodes = parse_view_rate_history(html) if html else []
    latest = parse_view_rate(html) if html else None

    if not episodes and not latest:
        html = fetch_search_html(title, max_attempts=1)
        if not html:
            return None
        episodes = parse_view_rate_history(html)
        latest = parse_view_rate(html)

    if not latest and episodes:
        top = episodes[0]
        latest = {
            "view_rate": top["view_rate"],
            "episode": top["episode"],
            "air_date": top["air_date"],
            "channel": top.get("channel") or "",
            "type": "LATEST",
        }
    if not latest:
        return None

    return {
        "view_rate": latest["view_rate"],
        "episode": latest.get("episode") or "",
        "air_date": latest.get("air_date") or "",
        "channel": latest.get("channel") or "",
        "type": latest.get("type") or "LATEST",
        "episodes": episodes,
    }


def resolve_otts_for_title(
    title: str,
    fallback: list[str] | None = None,
    channel: str = "",
    *,
    light: bool = False,
) -> dict[str, Any]:
    """
    네이버 검색 '보러가기' OTT (+ 상세용 포스터/시청률) 반환.

    light=True: 목록용. 보러가기 검색 1회만으로 OTT만 채움 (느림/멈춤 방지).
    """
    del fallback  # 샘플/추정 OTT 사용 안 함

    # OTT는 '보러가기' 검색을 우선 (light=목록용: 요청 1회로 제한)
    attempts = 1 if light else 2
    borragi_html = fetch_search_html(f"{title} 보러가기", max_attempts=attempts)
    platforms = parse_borragi_platforms(borragi_html) if borragi_html else []
    if not platforms and not light:
        platforms = fetch_borragi_otts(title, borragi_html)

    otts = [p["name"] for p in platforms]
    links = {p["name"]: p["url"] for p in platforms if p.get("url")}

    if light:
        return {
            "otts": otts,
            "ott_links": links,
            "poster_url": "",
            "rating": None,
            "source": "naver_borragi" if otts else "none",
            "news": [],
            "query": title,
            "confirmed": bool(otts),
        }

    panel_html = fetch_search_html(title, max_attempts=1) if borragi_html else borragi_html
    poster_url = fetch_poster_url(title, panel_html or borragi_html, channel=channel)
    rating = parse_view_rate(panel_html or borragi_html)
    news: list[dict[str, Any]] = []
    if naver_credentials_configured():
        news = search_news(f"{title} 다시보기", display=3)

    return {
        "otts": otts,
        "ott_links": links,
        "poster_url": poster_url,
        "rating": rating,
        "source": "naver_borragi" if otts else "none",
        "news": news,
        "query": title,
        "confirmed": bool(otts),
    }


def ping_naver_api() -> tuple[bool, str]:
    """자격 증명(Open API) 또는 보러가기 페이지 접근 확인."""
    # 보러가기 파싱은 검색 페이지 기준이므로 페이지 접근을 우선 확인
    try:
        res = requests.get(
            MOBILE_SEARCH,
            headers=_headers_mobile(),
            params={"query": "드라마"},
            timeout=8,
        )
        if res.status_code == 200 and len(res.text) > 1000:
            page_ok = True
        else:
            page_ok = False
    except requests.RequestException:
        page_ok = False

    if not naver_credentials_configured():
        return (page_ok, "borragi_ok" if page_ok else "page_fail")

    try:
        res = requests.get(
            NEWS_URL,
            headers=_headers_api(),
            params={"query": "드라마", "display": 1},
            timeout=8,
        )
        api_ok = res.status_code == 200
    except requests.RequestException as exc:
        return page_ok, f"api_error:{exc}"

    if page_ok and api_ok:
        return True, "ok"
    if page_ok:
        return True, "borragi_ok"
    return False, f"http_{res.status_code}"
