#!/usr/bin/env python3
"""유튜브 채널 일간 요약기.

설정 파일(youtube_channels.yml)에 등록한 채널들의 하루치 업로드를 모아
자막을 받아오고, Claude로 요약해 summaries/YYYY-MM-DD.md 로 저장한다.
쇼츠는 기본적으로 제외한다.

사용 예:
    python youtube_daily.py                    # 어제(KST) 방송 요약
    python youtube_daily.py --date 2026-08-20  # 특정 날짜
    python youtube_daily.py --days-back 0      # 오늘(KST) 올라온 것까지
    python youtube_daily.py --include-shorts   # 쇼츠도 포함 (기본은 제외)
    python youtube_daily.py --dry-run          # 요약 없이 대상 영상만 확인
    python youtube_daily.py --check-auth       # 자격 증명만 확인 (비용 없음)
    python youtube_daily.py --diagnose         # 인증·프록시·RSS·자막을 차례로 점검
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

KST = timezone(timedelta(hours=9))

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "youtube_channels.yml"
DEFAULT_OUT_DIR = ROOT / "summaries"
CACHE_PATH = ROOT / ".youtube_channel_cache.json"

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
SHORTS_URL = "https://www.youtube.com/shorts/{video_id}"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

# 자막이 아주 긴 경우(수 시간짜리 라이브 등) 나눠서 요약한 뒤 합친다.
CHUNK_CHARS = 240_000

# 요약을 어디로 보낼지. cli 는 Claude 구독 한도(claude CLI), api 는 API 크레딧을 쓴다.
BACKEND_CLI = "cli"
BACKEND_API = "api"
CLI_TIMEOUT = 600

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "medium"
DEFAULT_LANGUAGES = ["ko", "ko-KR", "en"]


# ---------------------------------------------------------------- 데이터 구조


@dataclass
class ChannelSpec:
    name: str
    source: str  # URL / @핸들 / UC... 채널 ID
    channel_id: Optional[str] = None
    note: str = ""


@dataclass
class Video:
    video_id: str
    title: str
    url: str
    published: datetime  # KST
    description: str = ""


@dataclass
class VideoResult:
    video: Video
    summary: str = ""
    transcript_label: str = ""
    error: str = ""


@dataclass
class ChannelResult:
    channel: ChannelSpec
    videos: list[VideoResult] = field(default_factory=list)
    skipped_shorts: int = 0
    error: str = ""


# ------------------------------------------------------------------- 유틸리티


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def http_get(url: str, retries: int = 3, timeout: int = 30) -> str:
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                },
            )
            with _build_opener(no_redirect=False).open(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:  # 네트워크 계열
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"{url} 요청 실패: {last}")


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------------- 설정 로딩


def load_config(path: Path) -> tuple[list[ChannelSpec], dict]:
    if not path.exists():
        raise SystemExit(
            f"설정 파일이 없습니다: {path}\n"
            "youtube_channels.yml 을 만들고 요약할 채널을 등록하세요."
        )
    try:
        import yaml
    except ImportError:  # pragma: no cover - 의존성 안내
        raise SystemExit("PyYAML이 필요합니다: pip install -r requirements-youtube.txt")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_channels = data.get("channels") or []
    channels: list[ChannelSpec] = []
    for item in raw_channels:
        if isinstance(item, str):
            channels.append(ChannelSpec(name=item, source=item))
            continue
        source = item.get("url") or item.get("id") or item.get("handle") or ""
        if not source:
            log(f"[건너뜀] url/id/handle 이 없는 항목: {item}")
            continue
        channels.append(
            ChannelSpec(
                name=item.get("name") or source,
                source=str(source),
                note=item.get("note", "") or "",
            )
        )
    if not channels:
        raise SystemExit(
            f"{path} 의 channels 목록이 비어 있습니다. 요약할 채널을 한 개 이상 등록하세요."
        )
    return channels, (data.get("options") or {})


# --------------------------------------------------------------- 채널 ID 해석


CHANNEL_ID_RE = re.compile(r"\b(UC[0-9A-Za-z_-]{22})\b")


def resolve_channel_id(spec: ChannelSpec, cache: dict) -> str:
    """URL / @핸들 / 채널 ID 중 무엇이 오든 UC... 형태의 채널 ID로 바꾼다."""
    source = spec.source.strip()

    direct = CHANNEL_ID_RE.search(source)
    if direct and ("/channel/" in source or source == direct.group(1)):
        return direct.group(1)

    if source in cache:
        return cache[source]

    if source.startswith("@"):
        page_url = f"https://www.youtube.com/{source}"
    elif source.startswith("http"):
        page_url = source
    else:
        page_url = f"https://www.youtube.com/@{source}"

    html = http_get(page_url)
    match = CHANNEL_ID_RE.search(html)
    if not match:
        raise RuntimeError(f"채널 ID를 찾지 못했습니다: {page_url}")

    cache[source] = match.group(1)
    return match.group(1)


# ------------------------------------------------------------------ RSS 수집


def fetch_channel_videos(channel_id: str) -> tuple[str, list[Video]]:
    xml_text = http_get(FEED_URL.format(channel_id=channel_id))
    root = ET.fromstring(xml_text)

    feed_title_el = root.find("atom:title", NS)
    feed_title = (feed_title_el.text or "").strip() if feed_title_el is not None else ""

    videos: list[Video] = []
    for entry in root.findall("atom:entry", NS):
        vid_el = entry.find("yt:videoId", NS)
        title_el = entry.find("atom:title", NS)
        published_el = entry.find("atom:published", NS)
        if vid_el is None or published_el is None:
            continue

        desc_el = entry.find("media:group/media:description", NS)
        published = datetime.fromisoformat(
            (published_el.text or "").replace("Z", "+00:00")
        ).astimezone(KST)

        videos.append(
            Video(
                video_id=vid_el.text or "",
                title=(title_el.text or "").strip() if title_el is not None else "",
                url=WATCH_URL.format(video_id=vid_el.text or ""),
                published=published,
                description=(desc_el.text or "").strip() if desc_el is not None else "",
            )
        )

    videos.sort(key=lambda v: v.published)
    return feed_title, videos


def videos_on(videos: Iterable[Video], target: Date) -> list[Video]:
    return [v for v in videos if v.published.date() == target]


# -------------------------------------------------------------- 쇼츠 걸러내기


SHORTS_HASHTAG_RE = re.compile(r"#\s?shorts?\b", re.IGNORECASE)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트를 따라가지 않고 HTTPError로 돌려받기 위한 핸들러."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def probe_short(video_id: str, timeout: int = 15) -> Optional[bool]:
    """youtube.com/shorts/<id> 로 확인한다.

    일반 영상이면 /watch 로 리다이렉트되고, 쇼츠면 그 자리에서 페이지가 열린다.
    True=쇼츠, False=일반 영상, None=판단 불가(이 경우 제외하지 않는다).
    """
    request = urllib.request.Request(
        SHORTS_URL.format(video_id=video_id),
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"},
    )
    try:
        with _build_opener(no_redirect=True).open(request, timeout=timeout) as resp:
            # <head> 안의 canonical 링크만 보면 되므로 앞부분만 읽는다.
            body = resp.read(200_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            return False
        return None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    # 동의 화면이나 봇 차단 페이지도 200을 주므로, 실제 쇼츠 페이지인지 확인한다.
    if f"/shorts/{video_id}" in body or '"isShort":true' in body:
        return True
    return None


def classify_short(video: Video) -> tuple[bool, str]:
    """(쇼츠 여부, 판단 근거)를 돌려준다. 확실하지 않으면 쇼츠가 아닌 것으로 본다."""
    if SHORTS_HASHTAG_RE.search(video.title) or SHORTS_HASHTAG_RE.search(video.description):
        return True, "#shorts 태그"
    if probe_short(video.video_id) is True:
        return True, "쇼츠 URL 확인"
    return False, ""


# -------------------------------------------------------------------- 자막


@lru_cache(maxsize=1)
def build_proxy_config():
    """YouTube가 클라우드 IP를 차단할 때를 위한 선택적 프록시 설정.

    GitHub Actions 같은 클라우드 IP에서는 YouTube가 자막 요청을 막기 때문에
    프록시 없이는 자막을 받지 못한다.
    """
    user = os.environ.get("WEBSHARE_PROXY_USERNAME")
    password = os.environ.get("WEBSHARE_PROXY_PASSWORD")
    if user and password:
        from youtube_transcript_api.proxies import WebshareProxyConfig

        return WebshareProxyConfig(proxy_username=user, proxy_password=password)

    http_url = os.environ.get("YT_HTTP_PROXY")
    https_url = os.environ.get("YT_HTTPS_PROXY")
    if http_url or https_url:
        from youtube_transcript_api.proxies import GenericProxyConfig

        return GenericProxyConfig(http_url=http_url, https_url=https_url)

    return None


def proxy_label() -> str:
    config = build_proxy_config()
    if config is None:
        return "없음"
    return type(config).__name__.replace("ProxyConfig", "")


@lru_cache(maxsize=2)
def _build_opener(no_redirect: bool) -> urllib.request.OpenerDirector:
    """프록시가 설정돼 있으면 YouTube 요청 전체를 그 프록시로 보낸다.

    자막만 프록시를 타고 나머지 요청이 클라우드 IP로 나가면, 그 요청들이
    차단 판정의 빌미가 될 수 있으므로 경로를 하나로 맞춘다.
    """
    handlers: list[urllib.request.BaseHandler] = []

    config = build_proxy_config()
    if config is not None:
        handlers.append(urllib.request.ProxyHandler(config.to_requests_dict()))

    if no_redirect:
        handlers.append(_NoRedirect())

    return urllib.request.build_opener(*handlers)


def fetch_transcript(video_id: str, languages: list[str]) -> tuple[str, str]:
    """(자막 본문, 자막 설명 라벨)을 돌려준다. 실패하면 예외를 던진다."""
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi(proxy_config=build_proxy_config())
    transcript_list = api.list(video_id)

    transcript = None
    label = ""
    try:
        transcript = transcript_list.find_manually_created_transcript(languages)
        label = "수동 자막"
    except Exception:
        try:
            transcript = transcript_list.find_generated_transcript(languages)
            label = "자동 생성 자막"
        except Exception:
            # 선호 언어에 없으면 아무 자막이나 받아 한국어로 번역 시도
            for candidate in transcript_list:
                transcript = candidate
                label = "기타 언어 자막"
                if candidate.is_translatable:
                    try:
                        transcript = candidate.translate("ko")
                        label = "번역 자막"
                    except Exception:
                        pass
                break

    if transcript is None:
        raise RuntimeError("사용 가능한 자막이 없습니다")

    fetched = transcript.fetch()
    text = " ".join(snippet.text.strip() for snippet in fetched if snippet.text.strip())
    if not text:
        raise RuntimeError("자막이 비어 있습니다")

    lang = getattr(transcript, "language", "") or getattr(transcript, "language_code", "")
    return text, f"{lang} {label}".strip()


# -------------------------------------------------------------------- 요약


SYSTEM_PROMPT = """당신은 유튜브 방송 내용을 한국어로 요약하는 편집자입니다.

규칙:
- 자막은 음성 인식 결과라 오탈자와 잘못 끊긴 문장이 섞여 있습니다. 문맥으로 바로잡아 읽으세요.
- 자막에 실제로 나온 내용만 쓰고, 없는 사실을 채워 넣지 마세요.
- 확실하지 않은 고유명사(사람 이름, 종목명, 숫자)는 추측하지 말고 그대로 두거나 생략하세요.
- 인사말, 광고, 구독 요청 같은 군더더기는 빼고 실질적인 내용만 남기세요.
- 출력은 마크다운이며, 아래 형식을 그대로 따릅니다. 제목 줄(###)은 붙이지 마세요.

**한 줄 요약**: (방송 전체를 한 문장으로)

**핵심 내용**
- (중요한 순서로 3~7개. 각 항목은 한두 문장으로 구체적으로)

**언급된 주요 대상**: (인물·기업·종목·지역·작품 등 핵심 고유명사를 쉼표로. 없으면 "없음")
"""


class FatalSummaryError(RuntimeError):
    """더 진행해도 모든 영상이 같은 이유로 실패하는 오류(인증 실패 등)."""


def _is_auth_failure(exc: BaseException) -> bool:
    """자격 증명 문제인지 판별한다.

    키가 아예 없으면 SDK가 anthropic 예외가 아니라 평범한 TypeError를 던지므로
    그 경우까지 함께 본다.
    """
    if isinstance(exc, TypeError) and "authentication method" in str(exc):
        return True
    try:
        import anthropic
    except ImportError:
        return False

    # 예외를 판별하다 또 다른 예외를 내지 않도록 방어적으로 조회한다.
    auth_errors = tuple(
        cls
        for cls in (
            getattr(anthropic, "AuthenticationError", None),
            getattr(anthropic, "PermissionDeniedError", None),
        )
        if isinstance(cls, type) and issubclass(cls, BaseException)
    )
    return bool(auth_errors) and isinstance(exc, auth_errors)


AUTH_HINT = (
    "GitHub Actions에서 돌리는 경우 ANTHROPIC_API_KEY 시크릿이 등록되어 있는지 확인하세요 "
    "(Settings → Secrets and variables → Actions). 내 PC라면 환경변수나 `ant auth login` 프로필을 확인하세요."
)


def _is_credit_failure(exc: BaseException) -> bool:
    """크레딧 잔액 부족인지 판별한다. 인증은 되지만 호출은 못 하는 상태."""
    message = str(exc).lower()
    return "credit balance" in message and "too low" in message


CREDIT_HINT = (
    "Anthropic 콘솔의 Plans & Billing 에서 크레딧을 충전하세요. "
    "인증 자체는 정상이므로 충전하면 바로 됩니다."
)


def _credit_failure_message(exc: BaseException) -> str:
    return (
        "크레딧 잔액이 부족합니다. 이대로면 모든 영상이 같은 이유로 실패하므로 중단합니다. "
        f"{CREDIT_HINT} 원본 오류: {exc}"
    )


def _auth_failure_message(exc: BaseException) -> str:
    return (
        "Claude API 인증에 실패했습니다. 이대로면 모든 영상이 같은 이유로 실패하므로 중단합니다. "
        f"{AUTH_HINT} 원본 오류: {exc}"
    )


def select_backend(explicit: Optional[str] = None) -> str:
    """구독 토큰이 있으면 CLI(구독 한도), 없으면 API(크레딧)를 쓴다."""
    if explicit and explicit != "auto":
        return explicit
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return BACKEND_CLI
    return BACKEND_API


def _call_claude_cli(model: str, prompt: str) -> str:
    """claude CLI를 통해 구독 한도로 요약한다."""
    import claude_cli

    # CLI는 시스템 프롬프트 인자를 따로 받지 않으므로 프롬프트 앞에 붙인다.
    full_prompt = f"{SYSTEM_PROMPT}\n\n---\n\n{prompt}"
    try:
        return claude_cli.ask(full_prompt, timeout=CLI_TIMEOUT, model=model)
    except claude_cli.ClaudeCliError as exc:
        if exc.is_auth_error:
            raise FatalSummaryError(
                "claude CLI 인증에 실패했습니다. 이대로면 모든 영상이 같은 이유로 실패하므로 "
                "중단합니다. CLAUDE_CODE_OAUTH_TOKEN 이 유효한지 확인하세요 "
                f"(재발급: claude setup-token). 원본 오류: {exc}"
            ) from exc
        raise


def _extract_text(message) -> str:
    return "".join(block.text for block in message.content if block.type == "text").strip()


def _call_claude(client, model: str, effort: str, prompt: str, max_tokens: int = 4000) -> str:
    if client is None:
        return _call_claude_cli(model, prompt)

    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            output_config={"effort": effort},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
    except Exception as exc:
        if _is_auth_failure(exc):
            raise FatalSummaryError(_auth_failure_message(exc)) from exc
        if _is_credit_failure(exc):
            raise FatalSummaryError(_credit_failure_message(exc)) from exc
        raise

    if message.stop_reason == "refusal":
        detail = getattr(message.stop_details, "explanation", "") or ""
        raise RuntimeError(f"모델이 요약을 거부했습니다. {detail}".strip())

    return _extract_text(message)


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def summarize_video(
    client,
    channel_name: str,
    video: Video,
    transcript: Optional[str],
    model: str,
    effort: str,
) -> str:
    header = (
        f"채널: {channel_name}\n"
        f"영상 제목: {video.title}\n"
        f"게시 시각(KST): {video.published:%Y-%m-%d %H:%M}\n"
    )

    if not transcript:
        body = video.description or "(설명 없음)"
        prompt = (
            f"{header}\n"
            "이 영상은 자막을 가져올 수 없어 제목과 영상 설명만 있습니다. "
            "아래 정보만으로 알 수 있는 범위에서 요약하고, 추측은 하지 마세요.\n\n"
            f"--- 영상 설명 ---\n{body}\n"
        )
        return _call_claude(client, model, effort, prompt, max_tokens=1500)

    if len(transcript) <= CHUNK_CHARS:
        prompt = f"{header}\n아래는 이 방송의 자막 전문입니다.\n\n--- 자막 ---\n{transcript}\n"
        return _call_claude(client, model, effort, prompt)

    # 아주 긴 방송: 구간별로 정리한 뒤 하나로 합친다.
    parts = _chunks(transcript, CHUNK_CHARS)
    log(f"    자막이 길어 {len(parts)}개 구간으로 나눠 요약합니다 ({len(transcript):,}자)")
    partials = []
    for idx, part in enumerate(parts, start=1):
        prompt = (
            f"{header}\n"
            f"아래는 이 방송 자막의 {idx}/{len(parts)} 구간입니다. "
            "이 구간에서 다뤄진 내용만 항목별로 정리하세요.\n\n"
            f"--- 자막 구간 ---\n{part}\n"
        )
        partials.append(f"[{idx}구간]\n{_call_claude(client, model, effort, prompt)}")

    joined = "\n\n".join(partials)
    merge_prompt = (
        f"{header}\n"
        "아래는 같은 방송을 구간별로 정리한 메모입니다. "
        "중복을 정리하고 방송 전체를 아우르는 하나의 요약으로 합치세요.\n\n"
        f"--- 구간 메모 ---\n{joined}\n"
    )
    return _call_claude(client, model, effort, merge_prompt)


# ------------------------------------------------------------------ 문서 생성


def render_markdown(target: Date, results: list[ChannelResult], generated_at: datetime) -> str:
    weekday = "월화수목금토일"[target.weekday()]
    lines = [
        f"# {target:%Y-%m-%d}({weekday}) 유튜브 일간 요약",
        "",
        f"- 생성 시각: {generated_at:%Y-%m-%d %H:%M} KST",
    ]

    total = sum(len(r.videos) for r in results)
    lines.append(f"- 대상 영상: {total}개 / 채널 {len(results)}개")
    lines.append("")

    if total == 0 and all(not r.error for r in results):
        lines.append("이 날 새로 올라온 영상이 없습니다.")
        lines.append("")

    for result in results:
        lines.append(f"## {result.channel.name}")
        lines.append("")

        if result.error:
            lines.append(f"> 채널을 불러오지 못했습니다: {result.error}")
            lines.append("")
            continue

        if not result.videos:
            if result.skipped_shorts:
                lines.append(f"이 날은 쇼츠 {result.skipped_shorts}개뿐이라 요약할 영상이 없습니다.")
            else:
                lines.append("이 날 올라온 영상이 없습니다.")
            lines.append("")
            continue

        if result.skipped_shorts:
            lines.append(f"<sub>쇼츠 {result.skipped_shorts}개는 제외했습니다.</sub>")
            lines.append("")

        for item in result.videos:
            video = item.video
            lines.append(f"### [{video.title}]({video.url})")
            meta = f"게시 {video.published:%H:%M} KST"
            if item.transcript_label:
                meta += f" · {item.transcript_label}"
            lines.append(f"<sub>{meta}</sub>")
            lines.append("")
            if item.error:
                lines.append(f"> 요약하지 못했습니다: {item.error}")
            else:
                lines.append(item.summary)
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "이 문서는 `youtube_daily.py` 가 각 영상의 자막을 받아 Claude로 요약한 것입니다. "
        "자막 인식 오류나 요약 과정에서 원문과 달라질 수 있으니 중요한 판단은 원본 영상을 확인하세요."
    )
    lines.append("")
    return "\n".join(lines)


def rebuild_index(out_dir: Path) -> None:
    files = sorted(
        (p for p in out_dir.glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", p.name)),
        reverse=True,
    )
    lines = [
        "# 유튜브 일간 요약 모음",
        "",
        "최신순입니다.",
        "",
    ]
    for path in files:
        lines.append(f"- [{path.stem}]({path.name})")
    lines.append("")
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


# -------------------------------------------------------------------- 실행


def _check_auth_cli(model: str) -> int:
    try:
        import claude_cli
    except ImportError:
        log("실패: claude_cli.py 를 찾을 수 없습니다.")
        return 1

    ready, reason = claude_cli.is_ready()
    log(f"사전 점검: {reason}")
    if not ready:
        return 2

    try:
        answer = claude_cli.ask("ok 라고만 답해줘.", timeout=120, model=model)
    except claude_cli.ClaudeCliError as exc:
        if exc.is_auth_error:
            log("인증 실패 — CLAUDE_CODE_OAUTH_TOKEN 을 확인하세요 (재발급: claude setup-token)")
            log(f"  원본 오류: {exc}")
            return 2
        log(f"실패: claude CLI 호출에 실패했습니다: {exc}")
        return 1

    log(f"호출 OK — 구독 한도로 요약할 수 있는 상태입니다. (응답: {answer[:40]})")
    return 0


def check_auth(model: str, backend: str) -> int:
    """요약을 돌리기 전에 자격 증명이 쓸 수 있는 상태인지 가볍게 확인한다.

    토큰을 생성하지 않는 Models API를 쓰므로 요약 비용이 들지 않는다.
    """
    backend_label = "구독(claude CLI)" if backend == BACKEND_CLI else "API 크레딧"
    log(f"인증 확인  |  모델 {model}  |  프록시 {proxy_label()}  |  요약 {backend_label}")

    if backend == BACKEND_CLI:
        return _check_auth_cli(model)

    try:
        import anthropic
    except ImportError:
        log("실패: anthropic 패키지가 없습니다. pip install -r requirements-youtube.txt")
        return 1

    try:
        client = anthropic.Anthropic()
        client.models.list(limit=1)
    except Exception as exc:
        if _is_auth_failure(exc):
            log(f"인증 실패 — {AUTH_HINT}")
            log(f"  원본 오류: {exc}")
            return 2
        log(f"실패: 인증을 확인하는 중 오류가 났습니다: {exc}")
        return 1

    log("인증 OK — 자격 증명이 정상입니다.")

    # 설정된 모델 이름이 실제로 쓸 수 있는지도 함께 본다 (오타·권한 확인).
    try:
        info = client.models.retrieve(model)
        log(f"모델 OK — {info.id}")
    except Exception as exc:
        log(f"경고: 인증은 됐지만 모델 '{model}' 을 조회하지 못했습니다: {exc}")

    # 인증이 통과해도 크레딧이 0이면 요약은 못 한다. 실제로 한 번 호출해봐야
    # 알 수 있으므로, 1토큰짜리 최소 요청을 보낸다.
    try:
        client.messages.create(
            model=model,
            max_tokens=1,
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": "."}],
        )
    except Exception as exc:
        if _is_credit_failure(exc):
            log(f"호출 실패 — {CREDIT_HINT}")
            log(f"  원본 오류: {exc}")
            return 2
        log(f"경고: 시험 호출에서 오류가 났습니다: {exc}")
        return 0

    log("호출 OK — 요약을 만들 수 있는 상태입니다.")
    return 0


def diagnose(channels: list[ChannelSpec], options: dict, model: str, backend: str) -> int:
    """어디까지 되고 어디서 막히는지 한 번에 점검한다.

    프록시를 붙인 뒤 자막이 실제로 뚫렸는지 확인하는 용도이기도 하다.
    """
    languages = options.get("languages") or DEFAULT_LANGUAGES
    failures: list[str] = []

    log("=" * 60)
    log("1. 요약 백엔드")
    log("=" * 60)
    if check_auth(model, backend) != 0:
        failures.append("요약 백엔드 인증")

    log("")
    log("=" * 60)
    log("2. 프록시")
    log("=" * 60)
    label = proxy_label()
    if label == "없음":
        log("프록시 없음 — 클라우드 IP에서 실행 중이라면 YouTube가 자막 요청을 막습니다.")
    else:
        log(f"프록시 {label} 설정됨 — 모든 YouTube 요청이 이 프록시를 거칩니다.")

    log("")
    log("=" * 60)
    log("3. 채널 RSS 조회")
    log("=" * 60)
    cache = load_cache()
    probe_video: Optional[Video] = None
    for spec in channels:
        try:
            channel_id = resolve_channel_id(spec, cache)
            _, videos = fetch_channel_videos(channel_id)
            log(f"OK   {spec.name}: 최근 영상 {len(videos)}개")
            if probe_video is None and videos:
                probe_video = videos[-1]
        except Exception as exc:
            log(f"실패 {spec.name}: {exc}")
            failures.append(f"RSS 조회({spec.name})")
    save_cache(cache)

    log("")
    log("=" * 60)
    log("4. 자막 조회")
    log("=" * 60)
    if probe_video is None:
        log("건너뜀 — RSS에서 확인할 영상을 얻지 못했습니다.")
        failures.append("자막 조회(확인 불가)")
    else:
        log(f"확인 대상: {probe_video.title} ({probe_video.url})")
        try:
            text, transcript_label = fetch_transcript(probe_video.video_id, languages)
            log(f"OK   자막 {len(text):,}자 ({transcript_label})")
        except Exception as exc:
            first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else str(exc)
            log(f"실패 {first_line}")
            if label == "없음":
                log("     → 프록시를 붙이면 해결됩니다 (youtube-daily-README.md 참고).")
            else:
                log(f"     → 프록시({label})를 쓰는데도 막혔습니다. 자격 증명과 잔여 트래픽을 확인하세요.")
            failures.append("자막 조회")

    log("")
    log("=" * 60)
    if failures:
        log(f"결과: {len(failures)}개 항목 실패 — {', '.join(failures)}")
        return 1
    log("결과: 모든 항목 정상. 자막까지 확보한 요약을 만들 수 있습니다.")
    return 0


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="유튜브 채널 일간 요약기")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="채널 설정 파일")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="요약 저장 폴더")
    parser.add_argument("--date", help="요약할 날짜(KST, YYYY-MM-DD)")
    parser.add_argument(
        "--days-back",
        type=int,
        default=1,
        help="오늘 기준 며칠 전을 요약할지 (기본 1 = 어제)",
    )
    parser.add_argument("--model", default=None, help=f"사용할 모델 (기본 {DEFAULT_MODEL})")
    parser.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--max-videos", type=int, default=None, help="채널당 최대 영상 수")
    parser.add_argument(
        "--include-shorts", action="store_true", help="쇼츠도 요약에 포함 (기본은 제외)"
    )
    parser.add_argument("--dry-run", action="store_true", help="요약 없이 대상 영상만 출력")
    parser.add_argument(
        "--backend",
        choices=["auto", BACKEND_CLI, BACKEND_API],
        default="auto",
        help="요약 경로. auto=구독 토큰이 있으면 cli, 없으면 api (기본)",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="인증·프록시·RSS·자막을 차례로 점검하고 끝낸다",
    )
    parser.add_argument(
        "--check-auth",
        action="store_true",
        help="자격 증명만 확인하고 끝낸다 (요약 비용 없음)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if args.diagnose:
        channels, options = load_config(args.config)
        model = args.model or options.get("model") or DEFAULT_MODEL
        return diagnose(channels, options, model, select_backend(args.backend))

    if args.check_auth:
        model = args.model or DEFAULT_MODEL
        if args.config.exists():
            try:
                _, options = load_config(args.config)
                model = args.model or options.get("model") or DEFAULT_MODEL
            except SystemExit:
                pass  # 채널이 비어 있어도 인증 확인은 할 수 있다
        return check_auth(model, select_backend(args.backend))

    channels, options = load_config(args.config)

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target = (datetime.now(KST) - timedelta(days=args.days_back)).date()

    model = args.model or options.get("model") or DEFAULT_MODEL
    effort = args.effort or options.get("effort") or DEFAULT_EFFORT
    languages = options.get("languages") or DEFAULT_LANGUAGES
    max_videos = args.max_videos or options.get("max_videos_per_channel") or 10
    skip_shorts = options.get("skip_shorts", True) and not args.include_shorts

    backend = select_backend(args.backend)
    backend_label = "구독(claude CLI)" if backend == BACKEND_CLI else "API 크레딧"

    log(
        f"대상 날짜(KST): {target}  |  채널 {len(channels)}개  |  모델 {model} (effort={effort})"
        f"  |  쇼츠 {'제외' if skip_shorts else '포함'}  |  프록시 {proxy_label()}"
        f"  |  요약 {backend_label}"
    )

    # client 가 None 이면 _call_claude 가 CLI 경로로 간다.
    client = None
    if not args.dry_run and backend == BACKEND_API:
        try:
            import anthropic
        except ImportError:
            raise SystemExit("anthropic 패키지가 필요합니다: pip install -r requirements-youtube.txt")
        if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            log("경고: ANTHROPIC_API_KEY 가 설정되어 있지 않습니다. 저장된 인증 프로필을 사용합니다.")
        client = anthropic.Anthropic()

    cache = load_cache()
    results: list[ChannelResult] = []
    fatal: Optional[str] = None

    for spec in channels:
        result = ChannelResult(channel=spec)
        results.append(result)
        log(f"\n[{spec.name}] 확인 중...")

        try:
            spec.channel_id = resolve_channel_id(spec, cache)
            feed_title, videos = fetch_channel_videos(spec.channel_id)
            if spec.name == spec.source and feed_title:
                spec.name = feed_title
        except Exception as exc:
            result.error = str(exc)
            log(f"  실패: {exc}")
            continue

        todays = videos_on(videos, target)

        if skip_shorts:
            kept: list[Video] = []
            for video in todays:
                is_short, reason = classify_short(video)
                if is_short:
                    result.skipped_shorts += 1
                    log(f"  - [쇼츠 제외] {video.title} ({reason})")
                else:
                    kept.append(video)
            todays = kept

        todays = todays[:max_videos]
        summary_line = f"  {target} 업로드 {len(todays)}개"
        if result.skipped_shorts:
            summary_line += f" (쇼츠 {result.skipped_shorts}개 제외)"
        log(summary_line)

        for video in todays:
            log(f"  - {video.published:%H:%M} {video.title}")
            item = VideoResult(video=video)
            result.videos.append(item)

            if args.dry_run:
                continue

            transcript: Optional[str] = None
            try:
                transcript, item.transcript_label = fetch_transcript(video.video_id, languages)
                log(f"    자막 {len(transcript):,}자 ({item.transcript_label})")
            except Exception as exc:
                item.transcript_label = "자막 없음 — 영상 설명 기반"
                log(f"    자막 실패: {exc}")

            try:
                item.summary = summarize_video(
                    client, spec.name, video, transcript, model, effort
                )
            except FatalSummaryError as exc:
                fatal = str(exc)
                break
            except Exception as exc:
                if _is_auth_failure(exc):
                    fatal = _auth_failure_message(exc)
                    break
                if _is_credit_failure(exc):
                    fatal = _credit_failure_message(exc)
                    break
                item.error = str(exc)
                log(f"    요약 실패: {exc}")

        if fatal:
            break

    save_cache(cache)

    if args.dry_run:
        log("\n--dry-run 이므로 파일을 만들지 않았습니다.")
        return 0

    if fatal:
        # 오류 메시지만 가득한 문서를 저장소에 남기지 않는다.
        log(f"\n중단: {fatal}")
        return 2

    # 모든 채널을 못 불러온 것은 "그날 영상이 없음"과 구분해야 한다.
    # 그대로 두면 채널 목록을 통째로 못 읽은 날도 성공으로 끝난다.
    failed_channels = sum(1 for r in results if r.error)
    if results and failed_channels == len(results):
        log(
            f"실패: 채널 {len(results)}개를 모두 불러오지 못했습니다. "
            "YouTube가 이 IP의 요청을 막고 있거나 일시적인 장애일 수 있습니다."
        )
        for r in results:
            log(f"  - {r.channel.name}: {r.error}")
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"{target:%Y-%m-%d}.md"
    out_path.write_text(
        render_markdown(target, results, datetime.now(KST)), encoding="utf-8"
    )
    rebuild_index(args.out_dir)
    log(f"\n저장 완료: {out_path}")

    attempted = sum(len(r.videos) for r in results)
    summarized = sum(1 for r in results for item in r.videos if item.summary)
    with_transcript = sum(
        1 for r in results for item in r.videos if item.transcript_label
        and "자막 없음" not in item.transcript_label
    )

    if attempted and not summarized:
        log(f"실패: 대상 영상 {attempted}개 중 요약에 성공한 것이 하나도 없습니다.")
        return 1

    if attempted and not with_transcript:
        if build_proxy_config() is None:
            hint = (
                "YouTube가 이 IP의 자막 요청을 막고 있을 수 있습니다. "
                "GitHub Actions 같은 클라우드 IP라면 프록시가 필요합니다 "
                "(youtube-daily-README.md 참고)."
            )
        else:
            hint = (
                f"프록시({proxy_label()})를 쓰고 있는데도 자막을 못 받았습니다. "
                "프록시 자격 증명이나 잔여 트래픽을 확인하세요."
            )
        log(f"경고: 대상 영상 {attempted}개 모두 자막을 가져오지 못했습니다. {hint}")

    log(f"요약 {summarized}/{attempted}개 성공, 자막 확보 {with_transcript}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
