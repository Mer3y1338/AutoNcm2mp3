@echo off
setlocal

REM ===========================================================================
REM PyInstaller build script for AutoNcm2Mp3
REM Usage:
REM   1. py -m pip install -r requirements.txt pyinstaller
REM   2. build.bat
REM Output: dist\AutoNcm2Mp3.exe
REM ===========================================================================

REM ---- locate python ----
set PY=py
where py >nul 2>nul
if errorlevel 1 (
    set PY=python
    where python >nul 2>nul
    if errorlevel 1 (
        echo [!] Python not found. Install Python 3.10+ first.
        exit /b 1
    )
)

REM ---- ensure pyinstaller ----
%PY% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [*] Installing pyinstaller ...
    %PY% -m pip install pyinstaller
    if errorlevel 1 (
        echo [!] Failed to install pyinstaller.
        exit /b 1
    )
)

REM ---- generate logo.ico from logo.png ----
set ICON_OPT=
if exist logo.png (
    echo [*] Generating logo.ico from logo.png ...
    %PY% -c "from PIL import Image; img=Image.open('logo.png').convert('RGBA'); img.save('logo.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
    if exist logo.ico set ICON_OPT=--icon logo.ico
)

REM ---- pack data files ----
set DATA_OPTS=
if exist logo.png set DATA_OPTS=%DATA_OPTS% --add-data "logo.png;."
if exist logo.ico set DATA_OPTS=%DATA_OPTS% --add-data "logo.ico;."

echo [*] Running PyInstaller ...
%PY% -m PyInstaller --noconfirm --windowed --onefile --name AutoNcm2Mp3 %ICON_OPT% %DATA_OPTS% --collect-submodules watchdog --collect-submodules pystray --collect-submodules PIL --hidden-import Crypto.Cipher.AES run.py

if errorlevel 1 (
    echo [!] Build failed.
    exit /b 1
)

echo.
echo [+] Done: dist\AutoNcm2Mp3.exe
endlocal
