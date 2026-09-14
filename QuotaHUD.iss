; QuotaHUD 安装包脚本（Inno Setup 6）
#define MyAppName "QuotaHUD 额度悬浮窗"
#define MyAppVersion "1.0.2"
#define MyAppExeName "QuotaHUD.exe"

[Setup]
AppId={{6CD07D59-A4D9-491F-A168-6EFE1C0DDC0F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher="QuotaHUD"
DefaultDirName={localappdata}\QuotaHUD
DefaultGroupName=QuotaHUD
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=QuotaHUD-Setup-1.0.2
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\QuotaHUD.exe
SetupIconFile=icon.ico
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "开机自动启动 QuotaHUD"; GroupDescription: "启动选项:"; Flags: checkedonce

[Files]
Source: "dist\QuotaHUD\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\QuotaHUD"; Filename: "{app}\QuotaHUD.exe"
Name: "{group}\卸载 QuotaHUD"; Filename: "{uninstallexe}"
Name: "{autodesktop}\QuotaHUD"; Filename: "{app}\QuotaHUD.exe"; Tasks: desktopicon
Name: "{userstartup}\QuotaHUD"; Filename: "{app}\QuotaHUD.exe"; Parameters: "--no-window"; Tasks: autostart

[Run]
Filename: "{app}\QuotaHUD.exe"; Description: "{cm:LaunchProgram,QuotaHUD}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载不删除 %APPDATA%\QuotaHUD 数据（保留凭证与配置）
Type: filesandordirs; Name: "{app}"
