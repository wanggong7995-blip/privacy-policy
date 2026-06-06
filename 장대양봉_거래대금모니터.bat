@echo off
chcp 65001 > nul
title 장대양봉 거래대금 모니터 (2000억↑ + 장대양봉)

echo.
echo  =====================================================
echo   장대양봉 거래대금 모니터
echo   조건: 거래대금 2000억 이상 + 장대 양봉
echo  =====================================================
echo.

:: stock_monitor.py가 같은 폴더에 있는지 확인
if not exist "%~dp0stock_monitor.py" (
    echo  [오류] stock_monitor.py 파일이 없습니다.
    echo  이 배치파일과 같은 폴더에 stock_monitor.py를 넣어주세요.
    pause
    exit /b 1
)

:: 바탕화면 바로가기 자동 생성 (최초 1회)
set "SHORTCUT=%USERPROFILE%\Desktop\장대양봉 거래대금 모니터.lnk"
if not exist "%SHORTCUT%" (
    echo  [바로가기] 바탕화면에 바로가기를 생성합니다...
    powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%SHORTCUT%');$s.TargetPath='%~f0';$s.WorkingDirectory='%~dp0';$s.IconLocation='%SystemRoot%\System32\shell32.dll,162';$s.Description='장대양봉 거래대금 모니터';$s.Save()"
    echo  [바로가기] 완료 - 다음부터는 바탕화면에서 바로 실행하세요.
    echo.
)

:: pip 패키지 자동 설치 (최초 1회)
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
