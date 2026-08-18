@echo off
rem SuperX 资产中心一键启动（Windows）
rem 关闭此窗口即停止服务；若需后台常驻，请改用 PowerShell Start-Process。
setlocal
set "SUPERX=%~dp0.."
python "%SUPERX%\superx.py" console serve --port 8765 --open
endlocal
