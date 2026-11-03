@echo off
chcp 936 >nul
title EXB 转图片 - 把 exb 放进 IN, 自动开图截图到 OUT
cd /d "%~dp0"
echo ============================================================
echo   EXB 转 PNG   (不依赖 CAXA 批转换器, 全自动)
echo   IN  : %~dp0IN
echo   OUT : %~dp0OUT
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0exb2png.ps1"
echo.
echo 完成. 图片在 OUT 文件夹里, 直接打开看 (可再丢掉给程序读).
pause
