; Inno Setup script — produces MiddlewareMonitorSetup-<version>.exe
; Self-contained: bundles Python embeddable + wheels + app code + NSSM.
; Build:  iscc /DAppVersion=2.0.0 MiddlewareMonitor.iss
;
; The CI workflow (.github/workflows/release.yml) prepares ./payload before
; calling iscc. Layout expected inside ./payload:
;   python\               (extracted Python embeddable)
;   wheels\               (all pip wheels)
;   app\<version>\        (application source)
;   nssm.exe              (service supervisor)
;   scripts\postinstall.ps1
;   scripts\uninstall.ps1

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{A8C2C3B0-5B5C-4E0F-8E54-MIDDLEWAREMON1}}
AppName=Middleware USCall Monitor
AppVersion={#AppVersion}
AppPublisher=USCall
AppPublisherURL=https://github.com/luizsteinbach30/middleware-ramais
DefaultDirName={autopf}\MiddlewareMonitor
DefaultGroupName=Middleware USCall Monitor
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
OutputBaseFilename=MiddlewareMonitorSetup-{#AppVersion}
OutputDir=.
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
SetupIconFile=
UninstallDisplayName=Middleware USCall Monitor

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "payload\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "payload\wheels\*"; DestDir: "{app}\wheels"; Flags: ignoreversion recursesubdirs
Source: "payload\app\*";    DestDir: "{app}\app";    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "payload\nssm.exe"; DestDir: "{app}\bin";   Flags: ignoreversion
Source: "payload\scripts\postinstall.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "payload\scripts\uninstall.ps1";   DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "payload\scripts\service-wrapper.cmd"; DestDir: "{app}\scripts"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\MiddlewareMonitor"
Name: "{commonappdata}\MiddlewareMonitor\db"
Name: "{commonappdata}\MiddlewareMonitor\backups"
Name: "{commonappdata}\MiddlewareMonitor\tmp"
Name: "{commonappdata}\MiddlewareMonitor\logs"

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\postinstall.ps1"" -InstallDir ""{app}"" -DataDir ""{commonappdata}\MiddlewareMonitor"" -AppVersion ""{#AppVersion}"""; \
  StatusMsg: "Configurando Python, banco de dados e serviço..."; \
  Flags: runhidden waituntilterminated
Filename: "http://127.0.0.1:8080/login"; \
  Description: "Abrir o painel no navegador"; \
  Flags: postinstall shellexec skipifsilent nowait unchecked

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\uninstall.ps1"" -InstallDir ""{app}"" -DataDir ""{commonappdata}\MiddlewareMonitor"""; \
  Flags: runhidden waituntilterminated

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
