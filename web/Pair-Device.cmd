@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "APP=%~dp0RFIDShowcase.exe"
if not exist "%APP%" set "APP=%~dp0dist\RFIDShowcase\RFIDShowcase.exe"
if not exist "%APP%" (
  echo 展示アプリがありません。開発PCでbuild-showcase.ps1を実行してください。
  pause
  exit /b 1
)
"%APP%" --pair-device
if errorlevel 1 echo ペアリングに失敗しました。Data\Showcase\Logs\launcher.logを確認してください。
pause
