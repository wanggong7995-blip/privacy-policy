@echo off
chcp 65001 >nul
setlocal
REM 유튜브 일간 요약 - 작업 스케줄러가 매일 실행하는 스크립트
REM 직접 실행해도 된다. 인자는 그대로 전달된다 (예: run.bat --date 2026-09-01)

cd /d "%~dp0.."
if not exist "local\logs" mkdir "local\logs"

set "LOG=local\logs\last_run.log"
set "HISTORY=local\logs\history.log"

echo [%date% %time%] 실행 시작 > "%LOG%"
python youtube_daily.py %* >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

type "%LOG%"

if not "%RC%"=="0" (
    echo [%date% %time%] 실패 ^(종료 코드 %RC%^) >> "%HISTORY%"
    echo.
    echo [실패] 종료 코드 %RC% - 자세한 내용은 %LOG% 를 보세요.
    exit /b %RC%
)

echo [%date% %time%] 성공 >> "%HISTORY%"

REM ---- 결과를 GitHub에 올린다 (git 저장소가 아니거나 실패해도 요약은 이미 저장됨) ----
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo.
    echo git 저장소가 아니라 푸시를 건너뜁니다. 요약은 summaries 폴더에 저장됐습니다.
    exit /b 0
)

git add summaries .youtube_channel_cache.json >nul 2>&1
git diff --staged --quiet
if not errorlevel 1 (
    echo.
    echo 새로 추가된 요약이 없습니다.
    exit /b 0
)

git commit -q -m "유튜브 일간 요약 자동 업데이트 (로컬)"
git pull --rebase -q origin main && git push -q origin HEAD:main
if errorlevel 1 (
    echo.
    echo 푸시에 실패했습니다. 요약은 로컬에 저장돼 있으니 나중에 직접 푸시하세요.
    exit /b 0
)

echo.
echo GitHub에 올렸습니다.
exit /b 0
