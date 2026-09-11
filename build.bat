@echo off
REM QuotaHUD 一键构建：PyInstaller -> Inno Setup
cd /d %~dp0
echo [1/3] PyInstaller 打包...
.venv\Scripts\python.exe -m PyInstaller QuotaHUD.spec --noconfirm || goto :err
echo [2/3] 生成图标...
python make_icon.py || goto :err
echo [3/3] Inno Setup 制作安装包...
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" QuotaHUD.iss || goto :err
echo.
echo 完成! 安装包: %~dp0Output\QuotaHUD-Setup-1.0.0.exe
goto :eof
:err
echo 构建失败
exit /b 1
