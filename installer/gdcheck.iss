; Grim Dawn Build Check - Inno Setup script.
; Build: ISCC /DAppVersion=2026.10.3.75 /DStageDir=<staged folder> /O<output folder> gdcheck.iss
; (installer/build_installer.py passes both defines). Needs Inno Setup 6.3 or newer (x64compatible).

#ifndef AppVersion
  #error AppVersion is not defined (pass /DAppVersion=YYYY.M.D.N)
#endif
#ifndef StageDir
  #error StageDir is not defined (pass /DStageDir=<folder from build_installer.py>)
#endif

[Setup]
; AppId identifies the installation for upgrades and the uninstaller. Generated once - never change it.
AppId={{E7663489-E044-4AA3-8D0B-952176784E9D}
AppName=Grim Dawn Build Check
AppVersion={#AppVersion}
AppPublisher=slovi21
AppPublisherURL=https://slovi21.github.io/grim-dawn-build-check/
AppSupportURL=https://github.com/slovi21/grim-dawn-build-check/issues
DefaultDirName={autopf}\Grim Dawn Build Check
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#StageDir}\LICENSE.txt
OutputBaseFilename=GrimDawnBuildCheck-Setup
VersionInfoVersion={#AppVersion}
UninstallDisplayName=Grim Dawn Build Check
; SetupIconFile is relative to the folder of this script (installer/); the same file is staged as {app}\gdcheck.ico.
SetupIconFile=gdcheck.ico
UninstallDisplayIcon={app}\gdcheck.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[InstallDelete]
; An upgrade must not keep modules of the previous version.
Type: filesandordirs; Name: "{app}\gdcheck"
Type: filesandordirs; Name: "{app}\__pycache__"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Grim Dawn Build Check"; Filename: "{app}\start.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\gdcheck.ico"
Name: "{autodesktop}\Grim Dawn Build Check"; Filename: "{app}\start.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\gdcheck.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\start.cmd"; Description: "Start Grim Dawn Build Check"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
; Only what the program owns: the install folder may be a shared one (e.g. C:\Games), so {app} itself
; goes only when empty. The data folder holds what the program created (reports, settings, indexes).
Type: filesandordirs; Name: "{app}\gdcheck"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: dirifempty;     Name: "{app}"
Type: filesandordirs; Name: "{localappdata}\GrimDawnBuildCheck"
