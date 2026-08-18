@echo off
rem wechat-claw web 启动脚本（Windows）
rem 用法: web\start.bat [--port 8650]
cd /d "%~dp0\.."
python web\launcher.py %*
