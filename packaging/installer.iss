; Inno Setup turns the folder that PyInstaller built into a setup program:
;
;     iscc /DAppVersion=1.0.0 packaging\installer.iss
;
; The app is installed for the current user, so no administrator rights are
; needed. The settings, the log and the stored speech live elsewhere and stay
; when the app is updated or removed.

#ifndef AppVersion
  #error Pass the version, for example /DAppVersion=1.0.0
#endif

#define AppName "Kleomarcus Announcer"
#define AppExe AppName + ".exe"

[Setup]
; Windows knows the app by this id, so it must never change: a later version
; with the same id replaces the installed one.
AppId={{6D65C92A-80BE-49D3-A0E3-9873DA699B35}
AppName={#AppName}
AppVersion={#AppVersion}
; The running app holds this mutex (announcer.single_instance), so the app
; is neither updated nor removed while it is open.
AppMutex=KleomarcusAnnouncerRunning
AppPublisher=Samet Polat
AppPublisherURL=https://github.com/sametpolat7/kleomarcus-announcer
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; PyInstaller draws this icon into its work folder.
SetupIconFile=..\build\announcer\icons\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=Kleomarcus-Announcer-{#AppVersion}-windows-x64-setup

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Files that the new version no longer has must not stay from the old one.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
