@echo off
rem wechat-claw web launcher (Windows). ASCII only - cmd parses this file in the
rem system codepage, non-ASCII comments can garble and confuse console output.
rem Usage: web\start.bat [--port 8650]
cd /d "%~dp0\.."
python web\launcher.py %*
