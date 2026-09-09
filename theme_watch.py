#!/usr/bin/env python3
"""테마별 주식 뉴스·유튜브 감시기.

themes.yml 에 등록한 테마(양자, 로봇 등)마다
  1) 구글 뉴스 RSS에서 키워드로 기사를 모으고
  2) 유튜브 검색 + 이미 등록된 채널(youtube_channels.yml)에서 영상을 모은 뒤
  3) Claude(구독 한도)로 주목할 만한 것을 골라 브리핑을 만들어
themes/YYYY-MM-DD.md 로 저장한다.

사용 예:
    python theme_watch.py                     # 오늘 기준 최근 lookback_days 치
    python theme_watch.py --dry-run           # 수집만 하고 요약은 건너뜀 (비용 0)
    python theme_watch.py --themes 양자 로봇   # 이름에 해당 문자열이 든 테마만
    python theme_watch.py --days-back 3       # 최근 3일치
    python theme_watch.py --no-transcript     # 자막 심층요약 없이 목록만 요약
    python theme_watch.py --diagnose          # 수집기만 점검 (Claude 호출 없음)
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import youtube_daily as yd

KST = timezone(timedelta(hours=9))

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "themes.yml"
DEFAULT_OUT_DIR = ROOT / "themes"
CHANNELS_CONFIG = ROOT / "youtube_channels.yml"

NEWS_RSS = (
    "https://news.google.com/rss/search"
    "?q={query}+when:{days}d&hl=ko&gl=KR&ceid=KR:ko"
)
YT_SEARCH = "https://www.youtube.com/results?search_query={query}&sp={sp}"
# 유튜브 "업로드 날짜" 필터 토큰
SP_TODAY = "EgIIAg%3D%3D"
SP_WEEK = "EgIIAw%3D%3D"

DEFAULT_MODEL = "claude-opus-5"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ 데이터 구조


@dataclass
class Theme:
    name: str
    keywords: list[str]
    tickers: list[str] = field(default_factory=list)


@dataclass
class Article:
    title: str
    url: str
    source: str
    published: Optional[datetime]
    keyword: str = ""

    @property
    def when(self) -> str:
        return self.published.strftime("%m-%d %H:%M") if self.published else "시각 미상"


@dataclass
class Clip:
    video_id: str
    title: str
    url: str
    channel: str
    when: str  # "3시간 전" 같은 표기 또는 "09-08 14:20"
    origin: str  # "검색" 또는 "구독채널"
    summary: str = ""
    transcript_label: str = ""
    error: str = ""


@dataclass
class ThemeResult:
    theme: Theme
    articles: list[Article] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)
    picked_articles: list[Article] = field(default_factory=list)
    picked_clips: list[Clip] = field(default_factory=list)
    briefing: str = ""
    error: str = ""


# ---------------------------------------------------------------------- 설정


def load_config(path: Path) -> tuple[list[Theme], dict]:
    if not path.exists():
        raise SystemExit(
            f"설정 파일이 없습니다: {path}\nthemes.yml 을 만들고 테마를 등록하세요."
        )
    try:
        import yaml
    except ImportError:  # pragma: no cover
        raise SystemExit("PyYAML이 필요합니다: pip install -r requirements-youtube.txt")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    themes: list[Theme] = []
    for item in data.get("themes") or []:
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        name = str(item.get("name") or "").strip()
        keywords = [str(k).strip() for k in (item.get("keywords") or []) if str(k).strip()]
        if not name or not keywords:
            log(f"[건너뜀] name 또는 keywords 가 없는 항목: {item}")
            continue
        themes.append(
            Theme(
                name=name,
                keywords=keywords,
                tickers=[str(t) for t in (item.get("tickers") or [])],
            )
        )
    if not themes:
        raise SystemExit(f"{path} 의 themes 목록이 비어 있습니다.")
    return themes, (data.get("options") or {})


# -------------------------------------------------------------------- 뉴스 수집


TITLE_TAIL_RE = re.compile(r"\s+-\s+[^-]+$")


def normalize_title(title: str) -> str:
    """중복 판정용. 매체명 꼬리표와 기호·공백을 지운다."""
    base = TITLE_TAIL_RE.sub("", title)
    return re.sub(r"[^0-9a-z가-힣]", "", base.lower())


def fetch_news(keyword: str, days: int) -> list[Article]:
    url = NEWS_RSS.format(query=quote_plus(keyword), days=max(1, days))
    xml_text = yd.http_get(url, retries=3)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise RuntimeError(f"뉴스 RSS 파싱 실패: {exc}")

    out: list[Article] = []
    for item in root.iterfind("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue

        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        if not source:
            m = re.search(r"\s+-\s+([^-]+)$", title)
            source = m.group(1).strip() if m else "출처 미상"

        published: Optional[datetime] = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date).astimezone(KST)
            except (TypeError, ValueError):
                published = None

        out.append(
            Article(
                title=html.unescape(TITLE_TAIL_RE.sub("", title)),
                url=link,
                source=html.unescape(source),
                published=published,
                keyword=keyword,
            )
        )
    return out


def round_robin(buckets: list[list], limit: int) -> list:
    """키워드별 결과를 번갈아 뽑는다.

    그냥 이어붙여 자르면 기사가 많이 걸리는 키워드 하나가 상한을 다 먹어버려서
    뒤쪽 키워드가 통째로 빠진다. 키워드마다 한 건씩 돌아가며 채운다.
    """
    out: list = []
    idx = 0
    while len(out) < limit and any(idx < len(b) for b in buckets):
        for bucket in buckets:
            if idx < len(bucket):
                out.append(bucket[idx])
                if len(out) >= limit:
                    break
        idx += 1
    return out


def collect_articles(theme: Theme, days: int, limit: int) -> list[Article]:
    seen: set[str] = set()
    buckets: list[list[Article]] = []
    for keyword in theme.keywords:
        try:
            items = fetch_news(keyword, days)
        except Exception as exc:
            log(f"    [뉴스 실패] {keyword}: {exc}")
            continue
        bucket: list[Article] = []
        for art in items:
            key = normalize_title(art.title)
            if not key or key in seen:
                continue
            seen.add(key)
            bucket.append(art)
        bucket.sort(key=lambda a: a.published or datetime.min.replace(tzinfo=KST), reverse=True)
        buckets.append(bucket)
        log(f"    뉴스 '{keyword}': {len(bucket)}건")

    collected = round_robin(buckets, limit)
    collected.sort(key=lambda a: a.published or datetime.min.replace(tzinfo=KST), reverse=True)
    return collected


# ------------------------------------------------------------------ 유튜브 수집


YT_INITIAL_DATA_RE = re.compile(r"ytInitialData\s*=\s*(\{.*?\})\s*;\s*</script>", re.DOTALL)
REL_TIME_RE = re.compile(
    r"(\d+)\s*(초|분|시간|일|주|개월|년|second|minute|hour|day|week|month|year)",
    re.IGNORECASE,
)
REL_UNIT_DAYS = {
    "초": 0, "second": 0,
    "분": 0, "minute": 0,
    "시간": 0, "hour": 0,
    "일": 1, "day": 1,
    "주": 7, "week": 7,
    "개월": 30, "month": 30,
    "년": 365, "year": 365,
}


def relative_days(text: str) -> Optional[float]:
    """'3시간 전' / '2 days ago' 를 대략 며칠 전인지로 바꾼다. 모르면 None."""
    if not text:
        return None
    m = REL_TIME_RE.search(text)
    if not m:
        return None
    amount = int(m.group(1))
    unit = m.group(2).lower()
    per = REL_UNIT_DAYS.get(unit)
    if per is None:
        return None
    if per == 0:
        return 0.0
    return float(amount * per)


def _walk_renderers(node, key: str, out: list):
    if isinstance(node, dict):
        if key in node and isinstance(node[key], dict):
            out.append(node[key])
        for value in node.values():
            _walk_renderers(value, key, out)
    elif isinstance(node, list):
        for value in node:
            _walk_renderers(value, key, out)


def _runs_text(node) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return str(node["simpleText"])
    runs = node.get("runs")
    if isinstance(runs, list):
        return "".join(str(r.get("text", "")) for r in runs if isinstance(r, dict))
    return ""


def search_youtube(keyword: str, days: int) -> list[Clip]:
    """유튜브 검색 결과 페이지를 긁는다. 검색용 RSS는 폐지돼서 이 방법을 쓴다."""
    sp = SP_TODAY if days <= 1 else SP_WEEK
    url = YT_SEARCH.format(query=quote_plus(keyword), sp=sp)
    page = yd.http_get(url, retries=3)

    m = YT_INITIAL_DATA_RE.search(page)
    if not m:
        raise RuntimeError("검색 결과에서 ytInitialData 를 찾지 못함 (유튜브 구조 변경 가능)")
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ytInitialData 파싱 실패: {exc}")

    renderers: list = []
    _walk_renderers(data, "videoRenderer", renderers)

    clips: list[Clip] = []
    for r in renderers:
        video_id = r.get("videoId")
        title = _runs_text(r.get("title"))
        if not video_id or not title:
            continue
        when = _runs_text(r.get("publishedTimeText"))
        age = relative_days(when)
        if age is not None and age > days:
            continue
        channel = _runs_text(r.get("ownerText")) or _runs_text(r.get("longBylineText"))
        clips.append(
            Clip(
                video_id=video_id,
                title=html.unescape(title),
                url=yd.WATCH_URL.format(video_id=video_id),
                channel=html.unescape(channel) or "채널 미상",
                when=when or "시각 미상",
                origin="검색",
            )
        )
    return clips


# 등록 채널 필터에서 무시할 낱말. 경제 방송 제목에 늘 붙어 있어서
# 이걸로 걸러내지 않으면 테마와 무관한 영상이 전부 후보로 들어온다.
GENERIC_WORDS = {
    "주가", "주식", "관련주", "종목", "수혜주", "테마주", "시황", "전망",
    "수출", "수주", "실적", "투자", "증시", "시장", "가격", "수요",
    "ai", "stock", "stocks", "market", "news", "order", "price", "buy",
}

_feed_cache: dict[str, tuple[str, list]] = {}


def watchlist_feeds(cache: dict) -> list[tuple[str, str, list]]:
    """(표시 이름, 피드 제목, 영상 목록). 테마마다 다시 받지 않도록 한 번만 읽는다."""
    try:
        specs, _ = yd.load_config(CHANNELS_CONFIG)
    except SystemExit as exc:
        log(f"    [구독채널 건너뜀] {exc}")
        return []

    out: list[tuple[str, str, list]] = []
    for spec in specs:
        if spec.name in _feed_cache:
            feed_title, videos = _feed_cache[spec.name]
        else:
            try:
                channel_id = yd.resolve_channel_id(spec, cache)
                feed_title, videos = yd.fetch_channel_videos(channel_id)
            except Exception as exc:
                log(f"    [구독채널 실패] {spec.name}: {exc}")
                _feed_cache[spec.name] = ("", [])
                continue
            _feed_cache[spec.name] = (feed_title, videos)
        out.append((spec.name, feed_title, videos))
    return out


def collect_watchlist_clips(theme: Theme, days: int, cache: dict) -> list[Clip]:
    """이미 등록해 둔 채널의 최근 영상 중 키워드가 걸리는 것만 고른다."""
    cutoff = datetime.now(KST) - timedelta(days=days)
    lowered = [k.lower() for k in theme.keywords]
    # "양자컴퓨터 주가" 같은 구절은 통째로는 잘 안 걸리므로 낱말로도 본다.
    # 단, '주가'·'관련주' 같은 흔한 낱말로 보면 테마와 무관한 방송이 전부 걸린다.
    words = {
        w for kw in lowered for w in kw.split()
        if len(w) >= 2 and w not in GENERIC_WORDS
    }
    if not words:
        return []

    out: list[Clip] = []
    for name, feed_title, videos in watchlist_feeds(cache):
        for video in videos:
            if video.published < cutoff:
                continue
            haystack = f"{video.title}\n{video.description}".lower()
            if not any(w in haystack for w in words):
                continue
            out.append(
                Clip(
                    video_id=video.video_id,
                    title=video.title,
                    url=video.url,
                    channel=name or feed_title,
                    when=video.published.strftime("%m-%d %H:%M"),
                    origin="구독채널",
                )
            )
    return out


def collect_clips(theme: Theme, days: int, limit: int, use_watchlist: bool, cache: dict) -> list[Clip]:
    seen: set[str] = set()
    buckets: list[list[Clip]] = []

    for keyword in theme.keywords:
        try:
            found = search_youtube(keyword, days)
        except Exception as exc:
            log(f"    [영상 검색 실패] {keyword}: {exc}")
            continue
        bucket: list[Clip] = []
        for clip in found:
            if clip.video_id in seen:
                continue
            seen.add(clip.video_id)
            bucket.append(clip)
        buckets.append(bucket)
        log(f"    영상 '{keyword}': {len(bucket)}건")

    # 구독 채널에서 걸린 영상은 검색 결과에 밀리지 않게 앞자리를 준다.
    watchlist: list[Clip] = []
    if use_watchlist:
        for clip in collect_watchlist_clips(theme, days, cache):
            if clip.video_id in seen:
                continue
            seen.add(clip.video_id)
            watchlist.append(clip)
        if watchlist:
            log(f"    구독채널: {len(watchlist)}건")

    room = max(0, limit - len(watchlist))
    return watchlist + round_robin(buckets, room)


# -------------------------------------------------------------------- 선별·요약


SELECT_PROMPT = """당신은 한국 주식 투자자를 위해 하루치 정보를 골라주는 편집자입니다.

아래는 '{theme}' 테마로 최근 {days}일 사이에 수집한 기사와 유튜브 영상 후보입니다.
이 중 투자 판단에 실제로 도움이 될 만한 것만 고르세요.

고르는 기준:
- 새로운 사실(수주, 실적, 정책, 기술 성과, 대형 계약, 규제 변화)이 담긴 것을 우선한다.
- 단순 시황 나열, 광고성 기사, 제목만 자극적인 것, 같은 사건의 중복 보도는 제외한다.
- 확실히 이 테마와 무관한 항목은 고르지 않는다. 고를 게 없으면 빈 배열로 답한다.

[기사 후보]
{news}

[영상 후보]
{videos}

JSON만 출력하세요. 설명이나 코드블록 표시를 붙이지 마세요.
{{"news": [고른 기사 번호 최대 {news_n}개], "videos": [고른 영상 번호 최대 {video_n}개]}}
"""

VIDEO_PROMPT = """당신은 유튜브 방송 내용을 한국어로 요약하는 편집자입니다.
아래 자막은 '{theme}' 테마와 관련해 고른 영상입니다.

규칙:
- 자막은 음성 인식 결과라 오탈자가 섞여 있습니다. 문맥으로 바로잡아 읽으세요.
- 자막에 실제로 나온 내용만 쓰고, 없는 사실을 채워 넣지 마세요.
- 확실하지 않은 고유명사(종목명, 숫자)는 추측하지 말고 생략하세요.
- 인사말, 광고, 구독 요청은 빼세요.
- 아래 형식만 출력하고 제목 줄은 붙이지 마세요.

**한 줄 요약**: (한 문장)

**핵심 내용**
- (3~5개, 각 항목 한두 문장)

**언급된 종목**: (쉼표로. 없으면 "없음")

제목: {title}
채널: {channel}

자막:
{transcript}
"""

BRIEF_PROMPT = """당신은 한국 주식 투자자를 위한 테마 브리핑을 쓰는 애널리스트입니다.
아래는 '{theme}' 테마에서 최근 {days}일 사이에 나온 주요 기사와 영상 요약입니다.

이 자료만 근거로 브리핑을 쓰세요. 자료에 없는 사실, 수치, 종목을 지어내지 마세요.
투자 권유("사라", "팔아라", 목표주가)는 쓰지 마세요. 사실과 맥락만 정리합니다.

출력 형식(마크다운, 제목 줄은 붙이지 말 것):

**오늘의 핵심**: (이 테마에서 오늘 가장 중요한 것 한 문장)

**정리**
- (3~5개. 무슨 일이 있었고 왜 중요한지. 근거가 된 기사/영상의 매체나 채널 이름을 문장 안에 자연스럽게 넣을 것)

**주목할 종목·기업**: (자료에 실제로 언급된 것만 쉼표로. 없으면 "없음")

**체크포인트**: (앞으로 확인해야 할 것 1~2개)

관련 종목(참고용): {tickers}

[기사]
{news}

[영상 요약]
{videos}
"""


def _numbered_news(articles: list[Article]) -> str:
    if not articles:
        return "(없음)"
    return "\n".join(
        f"{i}. {a.title} / {a.source} / {a.when}" for i, a in enumerate(articles, 1)
    )


def _numbered_videos(clips: list[Clip]) -> str:
    if not clips:
        return "(없음)"
    return "\n".join(
        f"{i}. {c.title} / {c.channel} / {c.when}" for i, c in enumerate(clips, 1)
    )


def _pick_indices(raw, count: int, total: int) -> list[int]:
    picked: list[int] = []
    if not isinstance(raw, list):
        return picked
    for value in raw:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= total and idx not in picked:
            picked.append(idx)
        if len(picked) >= count:
            break
    return picked


def select_items(claude, theme: Theme, result: ThemeResult, opts: dict, days: int, model: str):
    news_n = int(opts.get("news_picks", 5))
    video_n = int(opts.get("video_picks", 3))

    if not result.articles and not result.clips:
        return

    prompt = SELECT_PROMPT.format(
        theme=theme.name,
        days=days,
        news=_numbered_news(result.articles),
        videos=_numbered_videos(result.clips),
        news_n=news_n,
        video_n=video_n,
    )
    try:
        data = claude.ask_json(prompt, timeout=240, model=model)
    except Exception as exc:
        log(f"    [선별 실패] {exc} → 최신순 상위로 대체합니다.")
        result.picked_articles = result.articles[:news_n]
        result.picked_clips = result.clips[:video_n]
        return

    news_idx = _pick_indices(data.get("news"), news_n, len(result.articles))
    video_idx = _pick_indices(data.get("videos"), video_n, len(result.clips))
    result.picked_articles = [result.articles[i - 1] for i in news_idx]
    result.picked_clips = [result.clips[i - 1] for i in video_idx]
    log(f"    선별: 기사 {len(result.picked_articles)}건 / 영상 {len(result.picked_clips)}건")


def deepen_clips(claude, theme: Theme, result: ThemeResult, opts: dict, model: str):
    limit = int(opts.get("deep_videos_per_theme", 2))
    if limit <= 0:
        return
    languages = list(opts.get("languages") or ["ko", "ko-KR", "en"])

    for clip in result.picked_clips[:limit]:
        try:
            transcript, label = yd.fetch_transcript(clip.video_id, languages)
        except Exception as exc:
            clip.error = f"자막 없음: {type(exc).__name__}"
            log(f"    [자막 실패] {clip.title[:30]}: {exc}")
            continue
        clip.transcript_label = label
        body = transcript[: yd.CHUNK_CHARS]
        try:
            clip.summary = claude.ask(
                VIDEO_PROMPT.format(
                    theme=theme.name,
                    title=clip.title,
                    channel=clip.channel,
                    transcript=body,
                ),
                timeout=420,
                model=model,
            )
            log(f"    자막 요약 완료: {clip.title[:30]}")
        except Exception as exc:
            clip.error = f"요약 실패: {exc}"
            log(f"    [요약 실패] {clip.title[:30]}: {exc}")


def write_briefing(claude, theme: Theme, result: ThemeResult, days: int, model: str):
    if not result.picked_articles and not result.picked_clips:
        return
    video_block = []
    for clip in result.picked_clips:
        head = f"- {clip.title} ({clip.channel}, {clip.when})"
        if clip.summary:
            head += "\n" + "\n".join("  " + line for line in clip.summary.splitlines())
        video_block.append(head)

    prompt = BRIEF_PROMPT.format(
        theme=theme.name,
        days=days,
        tickers=", ".join(theme.tickers) or "없음",
        news=_numbered_news(result.picked_articles),
        videos="\n".join(video_block) or "(없음)",
    )
    try:
        result.briefing = claude.ask(prompt, timeout=420, model=model)
    except Exception as exc:
        result.error = f"브리핑 생성 실패: {exc}"
        log(f"    [브리핑 실패] {exc}")


# ------------------------------------------------------------------- 결과 출력


def render_markdown(target: Date, results: list[ThemeResult], days: int, generated_at: datetime) -> str:
    lines = [
        f"# 테마 감시 리포트 {target.isoformat()}",
        "",
        f"최근 {days}일치 · 생성 {generated_at.strftime('%Y-%m-%d %H:%M')} KST",
        "",
    ]

    total_news = sum(len(r.articles) for r in results)
    total_clips = sum(len(r.clips) for r in results)
    lines += [
        f"수집 {total_news}건 기사 / {total_clips}건 영상 · 테마 {len(results)}개",
        "",
        "---",
        "",
    ]

    for result in results:
        theme = result.theme
        lines.append(f"## {theme.name}")
        if theme.tickers:
            lines.append(f"> 관련 종목: {', '.join(theme.tickers)}")
        lines.append("")

        if result.error and not result.briefing:
            lines += [f"_{result.error}_", ""]
        if not result.articles and not result.clips:
            lines += ["_최근 기간에 걸린 기사나 영상이 없습니다._", "", "---", ""]
            continue

        if result.briefing:
            lines += [result.briefing.strip(), ""]

        if result.picked_articles:
            lines += ["### 주목 기사", ""]
            for art in result.picked_articles:
                lines.append(f"- [{art.title}]({art.url}) — {art.source} · {art.when}")
            lines.append("")

        # 자막 요약이 없는 영상은 목록으로, 있는 영상은 각각 소제목을 달아 펼친다.
        listed = [c for c in result.picked_clips if not c.summary]
        detailed = [c for c in result.picked_clips if c.summary]

        if listed:
            lines += ["### 주목 영상", ""]
            for clip in listed:
                row = f"- [{clip.title}]({clip.url}) — {clip.channel} · {clip.when}"
                if clip.error:
                    row += f" ({clip.error})"
                lines.append(row)
            lines.append("")

        for clip in detailed:
            lines += [
                f"### 🎬 [{clip.title}]({clip.url})",
                f"<sub>{clip.channel} · {clip.when} · {clip.transcript_label or '자막'}</sub>",
                "",
                clip.summary.strip(),
                "",
            ]

        rest_news = [a for a in result.articles if a not in result.picked_articles]
        rest_clips = [c for c in result.clips if c not in result.picked_clips]
        if rest_news or rest_clips:
            lines += [
                '<details class="more"><summary>그 밖에 수집된 것 '
                f"(기사 {len(rest_news)} · 영상 {len(rest_clips)})</summary>",
                "",
            ]
            for art in rest_news:
                lines.append(f"- [{art.title}]({art.url}) — {art.source} · {art.when}")
            for clip in rest_clips:
                lines.append(f"- 🎬 [{clip.title}]({clip.url}) — {clip.channel} · {clip.when}")
            lines += ["", "</details>", ""]

        lines += ["---", ""]

    lines += [
        "",
        "> 자동 수집·요약본입니다. 요약 과정에서 원문과 달라질 수 있으니 "
        "중요한 판단은 원문 기사와 영상을 직접 확인하세요. 투자 권유가 아닙니다.",
        "",
    ]
    return "\n".join(lines)


def rebuild_index(out_dir: Path) -> None:
    files = sorted(
        (p for p in out_dir.glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", p.name)),
        reverse=True,
    )
    lines = ["# 테마 감시 리포트 모음", "", "최신순입니다.", ""]
    for path in files:
        lines.append(f"- [{path.stem}]({path.name})")
    lines.append("")
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------- 실행


def diagnose(themes: list[Theme], opts: dict, days: int) -> int:
    print("=== 테마 감시기 점검 ===")
    print(f"테마 {len(themes)}개 / 최근 {days}일")

    ok = True
    theme = themes[0]
    keyword = theme.keywords[0]

    print(f"\n[1] 구글 뉴스 RSS — '{keyword}'")
    try:
        arts = fetch_news(keyword, days)
        print(f"    OK: {len(arts)}건")
        for a in arts[:3]:
            print(f"      - {a.title[:60]} ({a.source}, {a.when})")
    except Exception as exc:
        ok = False
        print(f"    실패: {exc}")

    print(f"\n[2] 유튜브 검색 — '{keyword}'")
    try:
        clips = search_youtube(keyword, days)
        print(f"    OK: {len(clips)}건")
        for c in clips[:3]:
            print(f"      - {c.title[:60]} ({c.channel}, {c.when})")
    except Exception as exc:
        ok = False
        print(f"    실패: {exc}  → 구독채널 필터로만 동작합니다")

    print("\n[3] Claude CLI 인증")
    try:
        import claude_cli

        ready, detail = claude_cli.is_ready()
        print(f"    {'OK' if ready else '실패'}: {detail}")
        ok = ok and ready
    except Exception as exc:
        ok = False
        print(f"    실패: {exc}")

    print("\n" + ("모두 정상입니다." if ok else "위 실패 항목을 확인하세요."))
    return 0 if ok else 1


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="테마별 주식 뉴스·유튜브 감시기")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--themes", nargs="*", default=None,
                   help="이름에 이 문자열이 든 테마만 처리")
    p.add_argument("--days-back", type=int, default=None, help="최근 며칠치를 볼지")
    p.add_argument("--date", default=None, help="리포트 파일 날짜 (기본: 오늘 KST)")
    p.add_argument("--dry-run", action="store_true", help="수집만 하고 요약은 건너뜀")
    p.add_argument("--no-transcript", action="store_true", help="자막 심층요약 생략")
    p.add_argument("--diagnose", action="store_true", help="수집기·인증만 점검")
    p.add_argument("--model", default=None)
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    themes, opts = load_config(args.config)

    if args.themes:
        needles = [n.lower() for n in args.themes]
        themes = [t for t in themes if any(n in t.name.lower() for n in needles)]
        if not themes:
            log("조건에 맞는 테마가 없습니다.")
            return 1

    days = args.days_back if args.days_back is not None else int(opts.get("lookback_days", 2))
    model = args.model or opts.get("model") or DEFAULT_MODEL

    if args.diagnose:
        return diagnose(themes, opts, days)

    claude = None
    if not args.dry_run:
        try:
            import claude_cli
        except ImportError:
            log("claude_cli.py 를 찾을 수 없습니다.")
            return 1
        ready, detail = claude_cli.is_ready()
        if not ready:
            log(f"Claude CLI 준비 안 됨: {detail}")
            return 1
        claude = claude_cli

    target = Date.fromisoformat(args.date) if args.date else datetime.now(KST).date()
    cache = yd.load_cache()
    results: list[ThemeResult] = []

    for theme in themes:
        log(f"\n[{theme.name}]")
        result = ThemeResult(theme=theme)
        result.articles = collect_articles(
            theme, days, int(opts.get("max_news_candidates", 30))
        )
        result.clips = collect_clips(
            theme,
            days,
            int(opts.get("max_video_candidates", 25)),
            bool(opts.get("include_watchlist_channels", True)),
            cache,
        )
        log(f"  수집: 기사 {len(result.articles)}건 / 영상 {len(result.clips)}건")

        if claude is not None:
            select_items(claude, theme, result, opts, days, model)
            if not args.no_transcript:
                deepen_clips(claude, theme, result, opts, model)
            write_briefing(claude, theme, result, days, model)

        results.append(result)

    yd.save_cache(cache)

    if args.dry_run:
        log("\n--dry-run: 수집 결과만 확인하고 파일은 쓰지 않습니다.")
        for result in results:
            print(f"\n## {result.theme.name}")
            for art in result.articles[:10]:
                print(f"  📰 {art.title[:70]} — {art.source} · {art.when}")
            for clip in result.clips[:10]:
                print(f"  🎬 {clip.title[:70]} — {clip.channel} · {clip.when} [{clip.origin}]")
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / f"{target.isoformat()}.md"
    path.write_text(
        render_markdown(target, results, days, datetime.now(KST)), encoding="utf-8"
    )
    rebuild_index(args.out_dir)
    log(f"\n저장 완료: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
