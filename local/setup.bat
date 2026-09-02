@echo off
chcp 65001 >nul
setlocal
REM 유튜브 일간 요약 - 내 PC에서 매일 자동 실행되도록 한 번만 설정한다.

cd /d "%~dp0.."
echo ============================================================
echo  유튜브 일간 요약 - 로컬 설정
echo ============================================================
echo.

echo [1/5] Python 확인
python --version
if errorlevel 1 (
    echo.
    echo Python이 없습니다. https://www.python.org/downloads/ 에서 설치하세요.
    echo 설치 화면 맨 아래 "Add python.exe to PATH" 를 반드시 체크해야 합니다.
    echo 설치 후 이 창을 닫고 새 창에서 다시 실행하세요.
    pause
    exit /b 1
)
echo.

echo [2/5] 필요한 패키지 설치
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements-youtube.txt
if errorlevel 1 (
    echo 패키지 설치에 실패했습니다.
    pause
    exit /b 1
)
echo     완료
echo.

echo [3/5] 구독 토큰 확인
if "%CLAUDE_CODE_OAUTH_TOKEN%"=="" (
    echo.
    echo CLAUDE_CODE_OAUTH_TOKEN 이 설정되어 있지 않습니다.
    echo.
    echo   1^) claude setup-token   으로 토큰을 발급받고
    echo   2^) setx CLAUDE_CODE_OAUTH_TOKEN "sk-ant-oat01-..."   로 저장한 뒤
    echo   3^) 이 창을 닫고 새 창에서 이 스크립트를 다시 실행하세요.
    echo.
    echo setx 로 저장해야 작업 스케줄러가 실행할 때도 토큰을 읽을 수 있습니다.
    pause
    exit /b 1
)
echo     설정됨
echo.

echo [4/5] 진단 - 인증, 채널, 자막이 실제로 되는지 확인
echo.
python youtube_daily.py --diagnose
if errorlevel 1 (
    echo.
    echo 진단에서 실패한 항목이 있습니다. 위 메시지를 확인하고 해결한 뒤 다시 실행하세요.
    pause
    exit /b 1
)
echo.

echo [5/5] 매일 아침 8시 10분 자동 실행 등록
schtasks /Create /TN "YoutubeDailySummary" /TR "\"%~dp0run.bat\"" /SC DAILY /ST 08:10 /F
if errorlevel 1 (
    echo.
    echo 작업 등록에 실패했습니다. 이 창을 관리자 권한으로 다시 열어 실행해 보세요.
    pause
    exit /b 1
)
echo.

echo ============================================================
echo  설정 완료
echo ============================================================
echo.
echo  매일 아침 8시 10분에 자동으로 전날 방송을 요약합니다.
echo  PC가 꺼져 있으면 그날은 건너뜁니다.
echo.
echo  결과:      summaries 폴더의 날짜별 .md 파일
echo  실행 기록: local\logs\history.log
echo  마지막 로그: local\logs\last_run.log
echo.
echo  지금 한 번 돌려보려면:  local\run.bat
echo  자동 실행을 끄려면:     schtasks /Delete /TN "YoutubeDailySummary" /F
echo.
pause
