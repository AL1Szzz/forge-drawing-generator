@echo off
chcp 65001 >nul
title 一键跑批 - 锻件尺寸图
rem Usage: drag a batch folder onto this file, or double-click and type a path.
rem NOTE: keep REM lines ASCII only; cmd under codepage 65001 mangles CJK in REM.
setlocal
set "BATCH=%~1"
if "%BATCH%"=="" (
  echo 把批次文件夹拖到本窗口然后回车:
  set /p BATCH=批次目录=
)
rem strip a trailing backslash so that the quoted path does not end in an escaped quote
if "%BATCH:~-1%"=="\" set "BATCH=%BATCH:~0,-1%"
rem Python: on another machine set FORGE_PYTHON to override the default below
set "PY=C:\Program Files\Python311\python.exe"
if defined FORGE_PYTHON set "PY=%FORGE_PYTHON%"
rem Skill path is relative to this BAT: ..\..\b5a9a823\skill\run_batch.py
set "SKILL=%~dp0..\..\b5a9a823\skill\run_batch.py"
echo.
"%PY%" -X utf8 "%SKILL%" "%BATCH%"
set "RC=%ERRORLEVEL%"
echo.
echo 完成。核对上面的输出与批次目录里的 复核清单.md，然后按任意键关闭本窗口。
pause >nul
exit /b %RC%
