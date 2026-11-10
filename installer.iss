[Setup]
AppId={{9BB195C8-3F39-4EF1-9B9E-417A432B7841}
AppName=涨势 · A股研究台
AppVersion=0.6.0
AppPublisher=涨势研究台
DefaultDirName={localappdata}\Programs\Zhangshi
DefaultGroupName=涨势研究台
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=release
OutputBaseFilename=Zhangshi-Setup-0.6.0-x64
SetupIconFile=static\assets\app.ico
UninstallDisplayIcon={app}\Zhangshi.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
SetupMutex=Zhangshi.AfterCloseLab.Desktop

[Languages]
Name: "chinesesimp"; MessagesFile: "packaging\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "dist\Zhangshi\*"; DestDir: "{app}"; Excludes: "data\*,portable.flag"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\Zhangshi\data\*"; DestDir: "{localappdata}\Zhangshi\data"; Excludes: "desktop-profile\*,*.log,*.tmp"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall; Check: SeedData

[Icons]
Name: "{group}\涨势研究台"; Filename: "{app}\Zhangshi.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\涨势研究台"; Filename: "{app}\Zhangshi.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Zhangshi.exe"; Description: "启动涨势研究台"; Flags: nowait postinstall skipifsilent

[Code]
var NeedsSeed: Boolean;
function InitializeSetup(): Boolean;
begin
  NeedsSeed := not FileExists(ExpandConstant('{localappdata}\Zhangshi\data\active.json'));
  Result := True;
end;
function SeedData(): Boolean;
begin
  Result := NeedsSeed;
end;
