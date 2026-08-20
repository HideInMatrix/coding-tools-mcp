#define MyAppName "MicroMatrix Workbench"
#define MyPublisher "MicroMatrix"
#define MyAppVersion GetEnv("MICROMATRIX_WORKBENCH_INSTALLER_VERSION")
#define MySourceDir GetEnv("MICROMATRIX_WORKBENCH_INSTALLER_SOURCE")
#define MyOutputDir GetEnv("MICROMATRIX_WORKBENCH_INSTALLER_OUTPUT_DIR")
#define MyOutputBaseName GetEnv("MICROMATRIX_WORKBENCH_INSTALLER_OUTPUT_BASE")
#define MyArchitecture GetEnv("MICROMATRIX_WORKBENCH_INSTALLER_ARCH")

[Setup]
AppId=MicroMatrix.Workbench
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyPublisher}
AppPublisherURL=https://micromatrix.org
AppSupportURL=https://micromatrix.org
DefaultDirName={localappdata}\Programs\MicroMatrix\MicroMatrix Workbench
DefaultGroupName={#MyPublisher}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed={#MyArchitecture}
ArchitecturesInstallIn64BitMode={#MyArchitecture}
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyOutputBaseName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\MicroMatrix Workbench.exe

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\MicroMatrix Workbench"; Filename: "{app}\MicroMatrix Workbench.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\MicroMatrix Workbench.exe"; WorkingDir: "{app}"; Flags: nowait
