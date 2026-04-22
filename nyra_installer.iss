; Nyra v2 — Inno Setup installer script
; Build: requires Inno Setup 6+ (https://jrsoftware.org/isinfo.php)
; Run:   iscc nyra_installer.iss
; Output: Output\NyraSetup.exe

#define MyAppName      "Nyra"
#define MyAppVersion   "2.0"
#define MyAppPublisher "Nyra AI"
#define MyAppExeName   "Nyra.exe"
#define MyAppURL       "https://github.com/cemalaysu73-star/nyraai"
#define SourceDir      "dist\Nyra"

[Setup]
AppId={{B4E8C2A1-3F7D-4E9B-A2C5-1D6F8E3B7A04}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=NyraSetup
SetupIconFile=assets\nyra.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardSmallImageFile=assets\nyra_wizard.bmp
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
MinVersion=10.0
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; Main application folder (PyInstaller output)
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove user data folder on uninstall only if user agrees (handled by app)
Type: filesandordirs; Name: "{localappdata}\{#MyAppName}"

[Code]
// Show a "Welcome" page note about the API key requirement
procedure InitializeWizard();
begin
  WizardForm.WelcomeLabel2.Caption :=
    'This will install Nyra AI on your computer.' + #13#10 + #13#10 +
    'On first launch, Nyra will guide you through a quick setup:' + #13#10 +
    '  • Auto-detect your hardware' + #13#10 +
    '  • Recommend the best AI model' + #13#10 +
    '  • Enter your free Groq API key (or use Ollama offline)' + #13#10 + #13#10 +
    'Get a free Groq key at: console.groq.com';
end;
