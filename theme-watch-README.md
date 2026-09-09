# 테마 감시 리포트 (theme_watch.py)

양자·로봇 같은 **테마별로 주식 관련 기사와 유튜브 영상을 모아 요약**하는 도구입니다.
유튜브 일간 요약(`youtube_daily.py`)과 같은 저장소에서, 같은 Claude 구독 토큰을 씁니다.
**API 크레딧이 아니라 구독 한도**를 사용합니다.

## 무엇을 하나

테마마다 다음 순서로 돕니다.

1. **기사 수집** — 구글 뉴스 RSS에서 테마 키워드로 최근 기사를 모읍니다. (API 키 불필요)
2. **영상 수집** — ① 유튜브 검색 결과에서 최근 업로드를 긁고, ② 이미 등록해 둔 채널
   (`youtube_channels.yml`의 9개 채널)의 최근 영상 중 키워드가 걸리는 것도 함께 모읍니다.
3. **선별** — Claude가 후보 중 투자 판단에 도움이 될 만한 기사·영상만 고릅니다.
   (단순 시황 나열, 광고성, 중복 보도는 걸러냅니다)
4. **심층 요약** — 고른 영상 중 상위 몇 개는 자막을 받아 내용까지 요약합니다.
5. **브리핑** — 테마별로 "오늘의 핵심 / 정리 / 주목할 종목 / 체크포인트"를 씁니다.

결과는 `themes/YYYY-MM-DD.md` 로 쌓이고, 웹에서는
<https://wanggong7995-blip.github.io/privacy-policy/theme-watch.html> 로 봅니다.

## 실행

```bat
local\run_themes.bat
```

작업 스케줄러가 매일 자동으로 이 파일을 돌립니다. 직접 실행해도 되고, 인자는 그대로 넘어갑니다.

파이썬을 직접 부를 수도 있습니다.

```bat
python theme_watch.py                     :: 최근 lookback_days 치
python theme_watch.py --dry-run           :: 수집만 확인, 요약 안 함 (비용 0)
python theme_watch.py --themes 양자 로봇   :: 이름에 해당 문자열이 든 테마만
python theme_watch.py --days-back 3       :: 최근 3일치
python theme_watch.py --no-transcript     :: 자막 심층요약 생략 (빠름)
python theme_watch.py --diagnose          :: 수집기·인증만 점검 (Claude 호출 없음)
```

## 테마 바꾸기

`themes.yml` 만 고치면 됩니다. 파일을 열고 `themes:` 아래에 항목을 추가하거나 지우세요.

```yaml
  - name: 2차전지
    keywords:
      - 2차전지 관련주
      - 전고체 배터리
      - LFP 수주
    tickers: [LG에너지솔루션, 에코프로비엠]
```

- `keywords` 는 뉴스·영상 검색어입니다. **테마당 3~6개**가 적당합니다. 많을수록 느려집니다.
- `tickers` 는 리포트 머리말에 참고로 적어두는 용도이고, 수집에는 쓰이지 않습니다.
- 잠시 쉬고 싶은 테마는 지우지 말고 `enabled: false` 를 넣으면 됩니다.

`options:` 에서 조정할 만한 것:

| 항목 | 뜻 | 기본 |
|---|---|---|
| `lookback_days` | 며칠치를 훑을지 | 2 |
| `deep_videos_per_theme` | 자막까지 받아 요약할 영상 수 (0이면 안 함) | 2 |
| `news_picks` / `video_picks` | 브리핑에 실을 기사·영상 개수 | 5 / 3 |
| `include_watchlist_channels` | 등록 채널에서도 키워드로 찾을지 | true |

## 알아둘 것

- **유튜브 검색은 깨질 수 있습니다.** 유튜브가 검색용 RSS를 없애서 검색 결과 페이지를 직접
  읽는 방식입니다. 유튜브가 페이지 구조를 바꾸면 `[영상 검색 실패]` 로그가 뜨는데, 이때도
  등록 채널 필터와 뉴스 수집은 계속 동작합니다. 로그에 이 메시지가 반복되면 알려주세요.
- **집 IP에서 돌려야 합니다.** 클라우드 IP에서는 유튜브가 자막을 막습니다.
  (유튜브 일간 요약과 같은 이유로 GitHub Actions 정기 실행은 쓰지 않습니다)
- **구글 뉴스 링크는 리다이렉트 주소**입니다. 클릭하면 원문 매체로 넘어갑니다.
- 요약은 자동 생성물이라 원문과 달라질 수 있습니다. 투자 권유가 아닙니다.

## 문제가 생기면

```bat
python theme_watch.py --diagnose
```

뉴스 RSS · 유튜브 검색 · Claude 인증을 차례로 점검합니다.
실행 로그는 `local\logs\themes_last_run.log` 에 남습니다.
