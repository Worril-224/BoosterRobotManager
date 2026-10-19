@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在以控制台模式启动 Booster 机器人管理器…
python\python.exe -m brm
pause
