# 📺 유튜브 일간 요약기

즐겨 보는 유튜브 채널의 하루치 업로드를 자동으로 모아, 자막을 받아 Claude로 요약한 뒤
`summaries/YYYY-MM-DD.md` 파일로 저장합니다. GitHub Actions가 매일 아침 알아서 돌립니다.

## 1. 채널 등록

`youtube_channels.yml` 을 열어 보고 싶은 채널을 적습니다. URL·핸들·채널 ID 중 아무거나 됩니다.

```yaml
channels:
  - name: 슈카월드
    url: https://www.youtube.com/@syukaworld

  - handle: "@johndoe"

  - id: UCsJ6RuBiTVWRX156FVbeaGg
```

`name` 을 생략하면 채널의 실제 이름을 자동으로 가져와 씁니다.

## 2. API 키 등록

요약에 Claude API를 사용하므로 키가 필요합니다.
[console.anthropic.com](https://console.anthropic.com/) 에서 키를 만든 뒤,
이 저장소의 **Settings → Secrets and variables → Actions → New repository secret** 에
`ANTHROPIC_API_KEY` 라는 이름으로 등록하세요.

> 이 저장소는 과거 등록분과의 호환을 위해 `YOUTUBE_SUMMARY` 라는 이름도 함께 읽습니다.
> 둘 다 있으면 `ANTHROPIC_API_KEY` 가 우선입니다. 새로 등록한다면 `ANTHROPIC_API_KEY` 를 쓰세요 —
> 이름만 보고 무엇이 들어 있는지 알 수 있어야 나중에 헷갈리지 않습니다.

## 3. 실행

### 자동 (기본)

`.github/workflows/youtube-daily-summary.yml` 이 **매일 아침 8시(KST)** 에 돌면서
전날 올라온 영상을 요약하고 결과를 저장소에 커밋합니다.

### 수동

Actions 탭 → **유튜브 일간 요약** → *Run workflow* 로 아무 때나 돌릴 수 있고,
`date` 칸에 `2026-08-20` 처럼 날짜를 넣으면 그날치를 다시 만듭니다.

### 내 PC에서

```bash
pip install -r requirements-youtube.txt
export ANTHROPIC_API_KEY=sk-ant-...

python youtube_daily.py                    # 어제(KST) 방송 요약
python youtube_daily.py --date 2026-08-20  # 특정 날짜
python youtube_daily.py --days-back 0      # 오늘 올라온 것까지
python youtube_daily.py --include-shorts   # 쇼츠도 포함 (기본은 제외)
python youtube_daily.py --dry-run          # 요약 없이 대상 영상만 확인 (API 비용 0)
python youtube_daily.py --check-auth       # 자격 증명만 확인 (API 비용 0)
```

## 결과물

`summaries/` 폴더에 날짜별로 쌓이고, `summaries/README.md` 가 목차 역할을 합니다.
영상 하나마다 이렇게 정리됩니다.

```markdown
### [8월 20일 아침 방송](https://www.youtube.com/watch?v=...)
<sub>게시 07:30 KST · 한국어 자동 생성 자막</sub>

**한 줄 요약**: ...

**핵심 내용**
- ...

**언급된 주요 대상**: ...
```

## 설정 항목

`youtube_channels.yml` 의 `options` 에서 조절합니다.

| 항목 | 설명 | 기본값 |
| --- | --- | --- |
| `languages` | 자막 언어 우선순위 | `[ko, ko-KR, en]` |
| `max_videos_per_channel` | 채널당 하루 최대 요약 개수 (비용 안전장치) | `10` |
| `skip_shorts` | 쇼츠 제외 여부 | `true` |
| `model` | 사용할 모델 | `claude-opus-5` |
| `effort` | 사고 강도 (`low`~`max`). 낮출수록 저렴 | `medium` |

### 쇼츠 제외

기본적으로 쇼츠는 요약하지 않습니다. RSS 피드에는 영상 길이나 쇼츠 여부가 들어 있지 않아서
두 가지로 판별합니다.

1. 제목이나 설명에 `#shorts` 태그가 있는 경우
2. `youtube.com/shorts/<영상ID>` 를 열어봤을 때 `/watch` 로 넘어가지 않는 경우
   (일반 영상은 리다이렉트되고, 쇼츠는 그 자리에서 열립니다)

**판별에 실패하면 쇼츠가 아닌 것으로 보고 요약에 포함합니다.** YouTube가 동의 화면이나
봇 차단 페이지를 돌려줄 때 멀쩡한 방송이 조용히 빠지는 쪽이 더 나쁘기 때문입니다.
제외된 개수는 요약 문서에 "쇼츠 N개는 제외했습니다"로 표시됩니다.

한국어 자막이 없으면 다른 언어 자막을 받아 한국어로 번역해 요약하고,
자막을 아예 못 구하면 제목과 영상 설명만으로 요약한 뒤 그렇게 표시합니다.
3시간짜리 라이브처럼 자막이 아주 긴 경우에는 구간별로 나눠 요약한 뒤 하나로 합칩니다.

## 프록시 (Actions에서 자막을 받으려면 필수)

**YouTube는 클라우드 IP에서 오는 자막 요청을 차단합니다.** 실제로 첫 실행에서 확인된 내용입니다.

```
YouTube is blocking requests from your IP...
You are doing requests from an IP belonging to a cloud provider
```

RSS(영상 목록)는 막히지 않아서 어떤 영상이 올라왔는지는 알 수 있지만, 자막이 없으면
영상 설명만으로 요약하게 되어 품질이 크게 떨어집니다. GitHub Actions에서 제대로 쓰려면
프록시가 필요합니다. 설정하면 자막뿐 아니라 RSS·쇼츠 판별까지 **모든 YouTube 요청이
같은 프록시를 거칩니다.**

### 방법 1: Webshare (권장)

`youtube-transcript-api` 가 공식 지원하는 방식입니다.

1. [webshare.io](https://www.webshare.io/) 가입 후 **Residential** 프록시를 구매합니다
   (Datacenter 프록시는 YouTube에서 대부분 막히므로 반드시 Residential이어야 합니다)
2. Dashboard → Proxy → Settings 에서 **Proxy Username** 과 **Password** 를 확인합니다
3. 저장소 Settings → Secrets and variables → Actions 에 두 개를 등록합니다
   - `WEBSHARE_PROXY_USERNAME`
   - `WEBSHARE_PROXY_PASSWORD`

### 방법 2: 직접 준비한 프록시

이미 쓰는 프록시가 있다면 `YT_HTTP_PROXY` / `YT_HTTPS_PROXY` 시크릿에
`http://아이디:비밀번호@호스트:포트` 형식으로 넣으면 됩니다.

### 확인 방법

실행 로그 첫 줄에 어떤 프록시를 쓰는지 찍힙니다.

```
대상 날짜(KST): 2026-08-22  |  채널 2개  |  ...  |  프록시 Webshare
```

`프록시 없음` 이면 시크릿이 전달되지 않은 것입니다. 프록시를 켰는데도 자막을 못 받으면
로그에 자격 증명이나 잔여 트래픽을 확인하라는 경고가 남습니다.

## 인증 확인

키가 제대로 들어갔는지 요약을 돌려보지 않고 확인할 수 있습니다. 인증 확인은 물론
**실제로 호출이 되는지(크레딧 잔액)까지** 봅니다. 1토큰짜리 최소 요청 하나만 보내므로
비용은 사실상 0입니다.

```bash
python youtube_daily.py --check-auth
```

```
인증 확인  |  모델 claude-opus-5  |  프록시 없음
인증 OK — 자격 증명이 정상입니다.
모델 OK — claude-opus-5
호출 OK — 요약을 만들 수 있는 상태입니다.
```

세 줄이 다 나와야 정상입니다. 인증만 통과하고 **크레딧이 0이면** 요약은 못 하는데,
`인증 OK` 만 보고 넘어가면 그걸 놓치게 되므로 마지막 호출 확인까지 합니다.
모델 이름 오타나 권한 문제도 함께 걸러집니다. 워크플로에도 `인증 확인` 단계로 들어가 있어서, 키가 없으면 자막을
받기 전에 바로 멈춥니다.

## 실행 결과 판정

실패를 조용히 넘기지 않도록 종료 코드를 구분합니다. Actions에서는 0이 아니면 빨간불이 됩니다.

| 코드 | 상황 |
| --- | --- |
| `0` | 정상. 요약할 영상이 없던 날도 포함 |
| `1` | 대상 영상이 있었는데 요약에 성공한 것이 하나도 없음 |
| `2` | 인증 실패 또는 크레딧 부족으로 중단. 어차피 모든 영상이 같은 이유로 실패하므로 즉시 멈추고, 오류만 담긴 문서를 저장소에 남기지 않습니다 |

자막을 하나도 못 가져왔지만 영상 설명으로 요약은 된 경우는 실패로 보지 않고 로그에 경고만 남깁니다.

## 알아두면 좋은 점

- **비용**: 1시간 방송의 자막은 대략 1~2만 토큰입니다. 하루 몇 편 수준이면 한 달에 몇 달러 정도이고,
  줄이고 싶으면 `effort: low` 로 낮추거나 `model: claude-sonnet-5` 로 바꾸면 됩니다.
- **정확도**: 자동 생성 자막은 고유명사와 숫자를 자주 틀립니다.
  요약이 확실하지 않은 부분은 원본 영상을 확인하세요.
- **멤버십 전용/비공개 영상**은 자막을 가져올 수 없어 설명 기반 요약으로 대체됩니다.
- **과거 날짜**: 채널 RSS는 최근 15개 영상만 제공합니다. 업로드가 잦은 채널은 며칠 지난 날짜를
  `--date` 로 다시 만들려 할 때 이미 목록에서 빠져 있을 수 있습니다.
