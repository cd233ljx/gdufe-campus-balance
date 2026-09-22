; SPDX-License-Identifier: Apache-2.0
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef AppSource
  #error AppSource is required
#endif
#ifndef OutputPath
  #error OutputPath is required
#endif
#ifndef ProductId
  #define ProductId "GDUFE.CampusBalance"
#endif
#ifndef ProductName
  #define ProductName "GDUFE Campus Balance"
#endif

[Setup]
AppId={#ProductId}
AppName={#ProductName}
AppVersion={#AppVersion}
AppPublisher=CardsClaim contributors
AppPublisherURL=https://github.com/cd233ljx/gdufe-campus-balance
AppSupportURL=https://github.com/cd233ljx/gdufe-campus-balance/issues
AppUpdatesURL=https://github.com/cd233ljx/gdufe-campus-balance/releases/latest
DefaultDirName={localappdata}\Programs\{#ProductName}
DefaultGroupName={#ProductName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableDirPage=no
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
WizardStyle=modern
SetupIconFile=..\build\cardsclaim.ico
UninstallDisplayIcon={app}\gdufe-campus-balance.exe
LicenseFile=..\LICENSE
OutputDir={#OutputPath}
OutputBaseFilename=gdufe-campus-balance-v{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
AppMutex=Local\{#ProductId}.Running
SetupMutex=Local\{#ProductId}.Setup
CloseApplications=no
RestartApplications=no
Uninstallable=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "安装选项："
Name: "startup"; Description: "开机自启（登录当前 Windows 用户后，在托盘中运行）"; GroupDescription: "安装选项："

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Excludes: "data,installation.ini"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#AppSource}\..\installation.ini"; DestDir: "{app}"; Flags: ignoreversion

[INI]
Filename: "{app}\installation.ini"; Section: "install"; Key: "DataDir"; String: "{localappdata}\{#ProductName}\data"
Filename: "{app}\installation.ini"; Section: "install"; Key: "MutexName"; String: "Local\{#ProductId}.Running"

[Icons]
Name: "{autoprograms}\{#ProductName}"; Filename: "{app}\gdufe-campus-balance.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#ProductName}"; Filename: "{app}\gdufe-campus-balance.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#ProductName}"; ValueData: """{app}\gdufe-campus-balance.exe"" --startup"; Tasks: startup; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#ProductName}"; Tasks: not startup; Flags: deletevalue

[Run]
Filename: "{app}\gdufe-campus-balance.exe"; Description: "立即启动广财校园工具箱"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteData: Boolean;

procedure InitializeWizard;
var
  ExistingDir, Command: String;
begin
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#ProductId}_is1', 'InstallLocation', ExistingDir) then
  begin
    if (ExpandConstant('{param:TASKS|not-specified}') = 'not-specified') and
       (ExpandConstant('{param:MERGETASKS|not-specified}') = 'not-specified') then
    begin
      if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#ProductName}', Command) then
        WizardSelectTasks('startup')
      else
        WizardSelectTasks('!startup');
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not WizardIsTaskSelected('desktopicon') then
    DeleteFile(ExpandConstant('{autodesktop}\{#ProductName}.lnk'));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectTasks) and WizardIsTaskSelected('startup') and
     (Length(ExpandConstant('"{app}\gdufe-campus-balance.exe" --startup')) > 260) then
  begin
    MsgBox('安装路径过长，Windows 无法注册开机自启。请返回选择较短的安装路径，或取消勾选开机自启。', mbError, MB_OK);
    Result := False;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  DeleteData := False;
  #ifdef InstallerTest
  { Compiled only into the separately named QA product to test both choices. }
  if UninstallSilent then
    DeleteData := ExpandConstant('{param:TESTPURGEDATA|0}') = '1';
  #endif
  if not UninstallSilent then
    DeleteData := MsgBox('是否同时清除本机的登录、邮箱授权码、设置和查询历史？' + #13#10 + #13#10 +
      '选“否”（默认）会保留数据，重新安装后可继续使用。' + #13#10 +
      '选“是”会永久清除上述本地数据。', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DeleteData then
  begin
    if not DelTree(ExpandConstant('{localappdata}\{#ProductName}\data'), True, True, True) then
      MsgBox('部分本地数据未能删除，请关闭软件后手动检查用户数据目录。', mbError, MB_OK);
  end;
end;
