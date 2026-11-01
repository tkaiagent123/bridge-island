@echo off
rem Double-click this file to open bridge-island in your browser. No command line needed.
rem Leave the black window open while you use the app; close it to stop the app.
cd /d "%~dp0"
title bridge-island
if not defined UV_SYSTEM_CERTS set UV_SYSTEM_CERTS=1
where uv >nul 2>nul
if %errorlevel%==0 (
  uv run bridge-island ui
) else (
  python -m bridge_island ui
)
if errorlevel 1 (
  echo.
  echo bridge-island could not start.
  echo Install uv from https://docs.astral.sh/uv/ and double-click this file again.
  echo More help: docs\app.md
  pause
)
