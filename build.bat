@echo off
REM QuotaHUD 一键构建：图标 -> 文件夹版 -> 便携版 -> 安装包
cd /d %~dp0
echo [1/4] 生成图标...
python make_icon.py || goto :err
echo [2/4] 文件夹版 PyInstaller（供安装包使用）...
.venv\Scripts\python.exe -m PyInstaller QuotaHUD.spec --noconfirm || goto :err
echo [3/4] 便携版单文件 PyInstaller（onefile，可直接分发）...
.venv\Scripts\python.exe -m PyInstaller QuotaHUD-Portable.spec --noconfirm || goto :err
if exist dist\QuotaHUD-Portable.exe copy /y dist\QuotaHUD-Portable.exe Output\QuotaHUD-Portable-1.0.0.exe >nul
echo [4/4] Inno Setup 制作安装包...
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" QuotaHUD.iss || goto :err
echo.
echo 完成!
echo   安装包: %~dp0Output\QuotaHUD-Setup-1.0.0.exe
echo   便携版: %~dp0Output\QuotaHUD-Portable-1.0.0.exe
goto :eof
:err
echo 构建失败
exit /b 1
