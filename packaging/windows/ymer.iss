; Ymer for Windows: installs the server bundle and Start menu shortcuts to run it with
; Docker Desktop. Built by the release workflow:
;   iscc /DAppVersion=0.1.0-beta.1 /DBundleDir=<staged bundle> /DOutputDir=dist ymer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef BundleDir
  #define BundleDir "..\..\dist\bundle"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{5B0E5A57-2D0C-4B8F-9A35-6F1B7E0C9A11}
AppName=Ymer
AppVersion={#AppVersion}
AppVerName=Ymer {#AppVersion}
AppPublisher=Ymer
AppPublisherURL=https://github.com/MKlolbullen/Ymer
AppSupportURL=https://github.com/MKlolbullen/Ymer/issues
; Per-user install: no administrator rights, and the settings file next to the bundle
; stays writable.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Ymer
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir={#OutputDir}
OutputBaseFilename=Ymer-Setup-{#AppVersion}
SetupIconFile=..\assets\ymer.ico
UninstallDisplayIcon={app}\ymer.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible arm64

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
Source: "..\assets\ymer.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Ymer\Start Ymer"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\ymerctl.ps1"" launch"; \
  WorkingDir: "{app}"; IconFilename: "{app}\ymer.ico"; Comment: "Start Ymer and open it in the browser"
Name: "{userprograms}\Ymer\Stop Ymer"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\ymerctl.ps1"" stop"; \
  WorkingDir: "{app}"; IconFilename: "{app}\ymer.ico"; Comment: "Stop Ymer (your data is kept)"
Name: "{userprograms}\Ymer\Update Ymer"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\ymerctl.ps1"" update"; \
  WorkingDir: "{app}"; IconFilename: "{app}\ymer.ico"; Comment: "Download this version's images and restart"
Name: "{userprograms}\Ymer\Ymer settings"; Filename: "notepad.exe"; Parameters: """{app}\ymer.env"""; \
  Comment: "Edit Ymer's settings, then Stop and Start Ymer"
Name: "{userprograms}\Ymer\Uninstall Ymer"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Ymer"; Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\ymerctl.ps1"" launch"; \
  WorkingDir: "{app}"; IconFilename: "{app}\ymer.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
; Settings with fresh secrets (kept on upgrades).
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\ymerctl.ps1"" init"; \
  Flags: runhidden waituntilterminated
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\ymerctl.ps1"" launch"; \
  Description: "Start Ymer now (the first start downloads about 1 GB)"; \
  Flags: postinstall nowait skipifsilent; Check: DockerInstalled

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\ymerctl.ps1"" stop"; \
  Flags: runhidden waituntilterminated; RunOnceId: "StopYmer"

; ymer.env is left in place on purpose: the data in Docker volumes is locked with its
; passwords, so a later reinstall picks up where this one left off.

[Code]
function DockerInstalled(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/C docker --version', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if (CurStep = ssPostInstall) and (not DockerInstalled()) and (not WizardSilent()) then
  begin
    if MsgBox('Ymer runs in Docker Desktop, which is not installed yet.' + #13#10#13#10 +
      'Install Docker Desktop (it uses WSL 2), start it, then open "Start Ymer" from the Start menu.' + #13#10#13#10 +
      'Open the Docker Desktop download page now?', mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://www.docker.com/products/docker-desktop/', '', '', SW_SHOWNORMAL,
        ewNoWait, ErrorCode);
  end;
end;
