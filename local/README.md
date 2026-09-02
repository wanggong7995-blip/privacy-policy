# 내 PC에서 매일 자동 실행하기

GitHub Actions는 클라우드 IP라 YouTube가 자막 요청을 막습니다. 집 IP에서 돌리면
**자막이 정상적으로 들어와** 요약 품질이 크게 올라갑니다. 프록시 비용도 들지 않습니다.

대신 **PC가 켜져 있어야** 그날 요약이 만들어집니다.

## 준비물

- Windows PC
- Python 3.10 이상 — [python.org](https://www.python.org/downloads/) 에서 설치.
  설치 화면 맨 아래 **"Add python.exe to PATH"** 를 반드시 체크하세요
- Claude 구독 토큰 (`CLAUDE_CODE_OAUTH_TOKEN`)

## 설정 (한 번만)

### 1. 저장소를 PC에 내려받기

```
git clone https://github.com/wanggong7995-blip/privacy-policy.git
cd privacy-policy
```

git이 없으면 GitHub 페이지에서 **Code → Download ZIP** 으로 받아 풀어도 됩니다.
(다만 그 경우 결과가 GitHub에 자동으로 올라가지는 않고 PC에만 저장됩니다.)

### 2. 구독 토큰을 환경변수로 저장

```
claude setup-token
setx CLAUDE_CODE_OAUTH_TOKEN "sk-ant-oat01-..."
```

**`setx` 로 저장해야** 작업 스케줄러가 실행할 때도 토큰을 읽습니다.
저장한 뒤에는 창을 닫고 새로 열어야 적용됩니다.

### 3. 설정 스크립트 실행

`local\setup.bat` 을 더블클릭하거나 명령 프롬프트에서 실행하세요.

```
local\setup.bat
```

Python 확인 → 패키지 설치 → 토큰 확인 → 진단 → 매일 08:10 자동 실행 등록까지
한 번에 처리합니다. 중간에 실패하면 무엇이 문제인지 알려주고 멈춥니다.

## 확인

| 하고 싶은 것 | 방법 |
| --- | --- |
| 지금 한 번 돌려보기 | `local\run.bat` |
| 특정 날짜 다시 만들기 | `local\run.bat --date 2026-09-01` |
| 어디까지 되는지 점검 | `python youtube_daily.py --diagnose` |
| 실행 기록 보기 | `local\logs\history.log` |
| 마지막 실행 로그 | `local\logs\last_run.log` |
| 자동 실행 끄기 | `schtasks /Delete /TN "YoutubeDailySummary" /F` |
| 자동 실행 확인 | `schtasks /Query /TN "YoutubeDailySummary"` |

## 결과

`summaries` 폴더에 날짜별 `.md` 파일로 쌓입니다. git 저장소로 받으셨다면 실행 후
자동으로 GitHub에도 올라가므로 휴대폰에서도 볼 수 있습니다.

푸시에 실패해도 요약은 이미 PC에 저장된 뒤이므로 잃지 않습니다.

## 알아두면 좋은 점

- **PC가 꺼져 있으면 그날은 건너뜁니다.** 나중에 `local\run.bat --date 2026-09-01`
  처럼 날짜를 지정해 다시 만들 수 있지만, 채널 RSS는 최근 15개 영상만 제공하므로
  업로드가 잦은 채널은 며칠 지나면 목록에서 빠질 수 있습니다.
- **GitHub Actions 정기 실행은 꺼 두었습니다.** 클라우드 IP에서는 자막을 못 받아
  품질이 낮은 요약에 구독 사용량만 쓰기 때문입니다. Actions 탭에서 수동 실행과
  진단은 그대로 쓸 수 있고, 다시 켜려면 워크플로 파일의 `schedule` 주석을 풀면 됩니다.
- 요약은 Claude 구독 한도를 사용합니다. 하루 몇 편 수준이면 부담이 크지 않습니다.
