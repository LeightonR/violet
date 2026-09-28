#define MyAppName "Violet"
#define MyAppVersion "0.1.0"
#define MyAppId "{{1EA81F38-ACB3-4300-9B68-46D1E34D31F5}"
#ifndef BundleDir
  #define BundleDir "..\\dist\\Violet"
#endif
#ifndef OutputDir
  #define OutputDir "..\\dist"
#endif
#ifndef SourceRoot
  #define SourceRoot ".."
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Violet contributors
DefaultDirName={localappdata}\Programs\Violet
DefaultGroupName=Violet
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=Violet-0.1.0-win64-setup
SetupIconFile={#SourceRoot}\Violet.ico
UninstallDisplayIcon={app}\Violet.exe
LicenseFile={#SourceRoot}\LICENSE
InfoBeforeFile={#SourceRoot}\packaging\installer-information.txt
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
VersionInfoVersion=0.1.0.0
VersionInfoProductVersion=0.1.0
DisableWelcomePage=no
DisableDirPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce
Name: "startup"; Description: "Start Violet when I sign in"; GroupDescription: "Background behavior:"; Flags: checkedonce

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\Violet"; Filename: "{app}\Violet.exe"; Parameters: "--control-panel"; WorkingDir: "{app}"; IconFilename: "{app}\Violet.exe"; AppUserModelID: "Violet.DesktopDictation"; Tasks: desktopicon
Name: "{userstartup}\Violet"; Filename: "{app}\Violet.exe"; Parameters: "--background"; WorkingDir: "{app}"; IconFilename: "{app}\Violet.exe"; AppUserModelID: "Violet.DesktopDictation"; Tasks: startup
Name: "{group}\Violet"; Filename: "{app}\Violet.exe"; Parameters: "--control-panel"; WorkingDir: "{app}"; IconFilename: "{app}\Violet.exe"; AppUserModelID: "Violet.DesktopDictation"
Name: "{group}\Uninstall Violet"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\Violet.exe"; Parameters: "--control-panel"; Description: "Open Violet's control panel"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  if not IsX64 then begin
    MsgBox('Violet 0.1.0 requires 64-bit Windows 10 version 1809 or Windows 11.', mbError, MB_OK);
    Result := False;
  end else begin
    Result := True;
  end;
end;
