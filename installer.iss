#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{7083D347-32FB-414E-9944-56DAD3B408CC}
AppName=X Media Saver
AppVersion={#AppVersion}
AppPublisher=X Media Saver contributors
DefaultDirName={localappdata}\Programs\XMediaSaver
DefaultGroupName=X Media Saver
OutputDir=dist\installer
OutputBaseFilename=XMediaSaverSetup
SetupIconFile=web\assets\icons\app-icon.ico
UninstallDisplayIcon={app}\XMediaSaver.exe
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern

[Files]
Source: "dist\XMediaSaver\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\XMediaSaverCLI.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "web\assets\fonts\Paperlogy-OFL.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "licenses\GPL-2.0.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\X Media Saver"; Filename: "{app}\XMediaSaver.exe"

[Run]
Filename: "{app}\XMediaSaver.exe"; Description: "X Media Saver 실행"; Flags: nowait postinstall skipifsilent
