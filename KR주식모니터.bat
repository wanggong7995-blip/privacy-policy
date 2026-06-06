@echo off
chcp 65001 > nul
title KR 주식 모니터 - 거래대금 2000억↑ + 장대 양봉

echo.
echo  =====================================================
echo   KR 주식 모니터  (거래대금 2000억 이상 + 장대 양봉)
echo  =====================================================
echo.

:: stock_monitor.py가 같은 폴더에 있는지 확인
if not exist "%~dp0stock_monitor.py" (
    echo  [오류] stock_monitor.py 파일이 없습니다.
    echo  이 배치파일과 같은 폴더에 stock_monitor.py를 넣어주세요.
    pause
    exit /b 1
)

:: pip 패키지 자동 설치
echo  [1/2] 필요한 패키지를 확인합니다...
pip show pykrx >nul 2>&1
if errorlevel 1 (
    echo  패키지 설치 중... (최초 1회만 실행)
    pip install pykrx pandas tabulate colorama -q
)

:: 프로그램 실행
echo  [2/2] 모니터링 시작...
echo.
python "%~dp0stock_monitor.py"

echo.
echo  =====================================================
echo   완료. 아무 키나 누르면 창이 닫힙니다.
echo  =====================================================
pause > nul
