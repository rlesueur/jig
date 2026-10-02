; Jig's Windows installer. Built by installer\build.ps1, which passes AppVersion, Stage and Icon.
;
; A per-user install: no administrator rights. Jig goes in %LOCALAPPDATA%\Programs\Jig, and its settings
; and data in %LOCALAPPDATA%\Jig (kept on uninstall unless you choose to delete them). Options for
; testing a second install alongside another: /DIR=<program folder> /JIGHOME=<settings and data folder>
; /PORT=<port> /ENTRY=<Task Scheduler entry for Start with Windows>.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{95703079-D843-48C3-A6C6-5F82AC829549}
AppName=Jig
AppVersion={#AppVersion}
AppVerName=Jig {#AppVersion}
AppPublisher=Jig
AppPublisherURL=https://github.com/rlesueur/jig
AppSupportURL=https://github.com/rlesueur/jig/issues
DefaultDirName={localappdata}\Programs\Jig
DisableWelcomePage=no
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=JigSetup-{#AppVersion}
SetupIconFile={#Icon}
UninstallDisplayIcon={app}\jig.ico
UninstallDisplayName=Jig
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=no

[Messages]
WelcomeLabel1=Welcome to Jig
WelcomeLabel2=Jig is a friendly AI helper that runs on your own computer.%n%nThis puts Jig on your computer for you alone: it doesn't need administrator rights, and it doesn't need Python, Git or a terminal.%n%nWhen it's done, Jig opens in its own window and helps you choose a model to think with.
WizardSelectTasks=Start with Windows
SelectTasksDesc=Should Jig be ready whenever you are?
SelectTasksLabel2=Jig can start by itself when you sign in to Windows, so it's always there when you need it. Tick the box if you'd like that.
WizardReady=Ready to install
ReadyLabel1=Jig is ready to go on your computer.
ReadyLabel2a=Click Install to continue.
ReadyLabel2b=Click Install to continue.
FinishedHeadingLabel=Jig is ready
FinishedLabelNoIcons=Jig is on your computer. Find it in the Start menu, or by its icon by the clock.
FinishedLabel=Jig is on your computer. Find it in the Start menu, or by its icon by the clock.

[Tasks]
Name: "autostart"; Description: "Start Jig when I sign in to Windows. This adds an entry to Task Scheduler for your account only. You can turn it off any time in Jig's Settings."; Flags: unchecked

[InstallDelete]
; An upgrade replaces Jig's program files; your settings and data are elsewhere and are kept.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"

[Files]
Source: "{#Stage}\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs ignoreversion
Source: "{#Stage}\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs ignoreversion
Source: "{#Stage}\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Stage}\jig.toml.template"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Icon}"; DestDir: "{app}"; DestName: "jig.ico"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Jig"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m jig.tray --config ""{code:JigHome}\jig.toml"" --open"; WorkingDir: "{app}"; IconFilename: "{app}\jig.ico"; Comment: "Open Jig"; AppUserModelID: "Jig.App"

[Registry]
; jig://start, used by the "Turn Jig on" button on Jig's "Jig is off" page.
Root: HKCU; Subkey: "Software\Classes\jig"; ValueType: string; ValueName: ""; ValueData: "URL:Jig"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\jig"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\jig\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\jig.ico"
Root: HKCU; Subkey: "Software\Classes\jig\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\python\pythonw.exe"" -m jig.tray --config ""{code:JigHome}\jig.toml"" ""%1"""
; Where this install keeps its settings and data, for the uninstaller.
Root: HKCU; Subkey: "Software\Jig"; Flags: uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Jig\Install"; ValueType: string; ValueName: "JigHome"; ValueData: "{code:JigHome}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m jig.tray --config ""{code:JigHome}\jig.toml"" --open"; WorkingDir: "{app}"; Description: "Open Jig now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function JigHome(Param: String): String;
begin
  Result := ExpandConstant('{param:JIGHOME|{localappdata}\Jig}');
end;

function ConfigPath(): String;
begin
  Result := JigHome('') + '\jig.toml';
end;

function RunJig(Args: String; var Output: String): Integer;
var
  Res: TExecOutput;
  ResultCode: Integer;
begin
  Output := '';
  if not ExecAndCaptureOutput(ExpandConstant('{app}\python\python.exe'), Args, ExpandConstant('{app}'), SW_HIDE,
                              ewWaitUntilTerminated, ResultCode, Res) then
  begin
    Output := SysErrorMessage(ResultCode);
    Result := -1;
    exit;
  end;
  Output := Trim(StringJoin(#13#10, Res.StdOut) + #13#10 + StringJoin(#13#10, Res.StdErr));
  Result := ResultCode;
end;

procedure WriteConfig();
var
  Template: AnsiString;
  Text, Entry: String;
begin
  if FileExists(ConfigPath()) then
    exit; { an upgrade keeps your settings }
  ForceDirectories(JigHome(''));
  LoadStringFromFile(ExpandConstant('{app}\jig.toml.template'), Template);
  Text := String(Template);
  Entry := ExpandConstant('{param:ENTRY|\Jig\Jig}');
  StringChangeEx(Entry, '\', '\\', True);
  StringChangeEx(Text, '@PORT@', ExpandConstant('{param:PORT|8766}'), True);
  StringChangeEx(Text, '@ENTRY@', Entry, True);
  if not SaveStringToFile(ConfigPath(), AnsiString(Text), False) then
    RaiseException('Jig couldn''t write its settings to ' + ConfigPath());
end;

function WebView2Installed(): Boolean;
var
  Version: String;
begin
  { Where Microsoft documents finding the WebView2 Runtime, for all users or this user. }
  Result := (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) or
             RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version)) and
            (Version <> '') and (Version <> '0.0.0.0');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Output: String;
begin
  Result := '';
  { An update replaces Jig's program files, so close Jig's window and its tray icon (which turns Jig off) first. }
  if FileExists(ExpandConstant('{app}\python\python.exe')) and FileExists(ConfigPath()) then
  begin
    RunJig('-m jig.desktop --config "' + ConfigPath() + '" --close', Output);
    RunJig('-m jig.tray --config "' + ConfigPath() + '" --quit', Output);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Output: String;
begin
  if CurStep = ssPostInstall then
  begin
    WriteConfig();
    if not WebView2Installed() then
      SuppressibleMsgBox('Jig opens in its own window, which uses Microsoft Edge WebView2. It isn''t on this computer yet. ' +
                         'When Jig opens, it will offer to get it for you (it''s free from Microsoft), or to use your browser instead.',
                         mbInformation, MB_OK, IDOK);
    if WizardIsTaskSelected('autostart') then
    begin
      WizardForm.StatusLabel.Caption := 'Setting up Start with Windows...';
      if RunJig('-m jig.cli --config "' + ConfigPath() + '" autostart enable --yes', Output) <> 0 then
        SuppressibleMsgBox('Jig is installed, but Start with Windows couldn''t be turned on:' + #13#10#13#10 + Output + #13#10#13#10 +
                           'You can turn it on later in Jig''s Settings.', mbError, MB_OK, IDOK);
    end;
  end;
end;

var
  UninstallHome: String;

function InitializeUninstall(): Boolean;
begin
  { Read before the uninstaller removes the registry entries. }
  if not RegQueryStringValue(HKCU, 'Software\Jig\Install', 'JigHome', UninstallHome) then
    UninstallHome := '';
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Config, Output: String;
begin
  if UninstallHome = '' then
  begin
    if CurUninstallStep = usUninstall then
      SuppressibleMsgBox('Jig couldn''t find where this copy keeps its settings, so it can''t turn Jig off or remove Start with Windows. ' +
                         'If Jig is still running, quit it from its icon by the clock first. Its data is left where it is.', mbError, MB_OK, IDOK);
    exit;
  end;
  Config := UninstallHome + '\jig.toml';
  if CurUninstallStep = usUninstall then
  begin
    { Close Jig's window, turn Jig off (the tray does it the same way the web page does), then remove Start with Windows. }
    RunJig('-m jig.desktop --config "' + Config + '" --close', Output);
    RunJig('-m jig.tray --config "' + Config + '" --quit', Output);
    RunJig('-m jig.cli --config "' + Config + '" stop', Output);
    if RunJig('-m jig.cli --config "' + Config + '" autostart disable', Output) <> 0 then
      SuppressibleMsgBox('Jig couldn''t remove its Start with Windows entry:' + #13#10#13#10 + Output, mbError, MB_OK, IDOK);
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    { Only a folder with this copy's jig.toml in it is Jig's. }
    if not FileExists(Config) then
      exit;
    if UninstallSilent() then
    begin
      if ExpandConstant('{param:DELETEDATA|0}') = '1' then
        DelTree(UninstallHome, True, True, True);
    end
    else if MsgBox('Keep your Jig data?' + #13#10#13#10 + 'Jig''s memories, notes, settings and saved keys are in ' + UninstallHome +
                   '. Keep them if you might install Jig again.' + #13#10#13#10 +
                   'Yes keeps them. No deletes them, and can''t be undone.', mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO then
      DelTree(UninstallHome, True, True, True);
  end;
end;

