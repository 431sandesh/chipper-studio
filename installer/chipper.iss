; Chipper Studio - Windows installer (Inno Setup 6). Built by .github/workflows/windows-installer.yml
#define AppVer GetEnv("CHIPPER_VERSION")
#if AppVer == ""
  #define AppVer "26.0.0"
#endif

[Setup]
AppId={{6D1B7F3A-8C21-4E5B-9A5E-C41F0E2D7B19}
AppName=Chipper Studio
AppVersion={#AppVer}
AppVerName=Chipper Studio {#AppVer}
AppPublisher=Nepcon Labs
AppPublisherURL=https://chipperstudio.nepconlabs.com
AppSupportURL=https://github.com/431sandesh/chipper-studio
DefaultDirName={localappdata}\Programs\Chipper Studio
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ChipperStudioSetup-{#AppVer}
SetupIconFile=chipper.ico
UninstallDisplayIcon={app}\chipper.ico
UninstallDisplayName=Chipper Studio
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=force
RestartApplications=no

[Types]
Name: "full"; Description: "Everything (PCs with an NVIDIA RTX graphics card)"
Name: "standard"; Description: "Standard (any PC)"
Name: "custom"; Description: "Let me choose"; Flags: iscustom

[Components]
Name: "core"; Description: "Chipper Studio"; Types: full standard custom; Flags: fixed
Name: "tiktok"; Description: "TikTok posting browser (about 150 MB download)"; Types: full standard
Name: "nvidia"; Description: "NVIDIA RTX AI pack: AI Enhance (sharper video) + AI captions (Whisper large-v3). Only for NVIDIA RTX 20/30/40/50 cards. About 6 GB download, 15-40 minutes"; Types: full; ExtraDiskSpaceRequired: 7000000000

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
Source: "..\build\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Chipper Studio"; Filename: "{app}\python\python.exe"; Parameters: """{app}\app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\chipper.ico"; Comment: "Start Chipper Studio"
Name: "{autodesktop}\Chipper Studio"; Filename: "{app}\python\python.exe"; Parameters: """{app}\app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\chipper.ico"; Comment: "Start Chipper Studio"; Tasks: desktopicon

[Run]
Filename: "{app}\python\python.exe"; Parameters: """{app}\addons.py"" tiktok"; WorkingDir: "{app}"; StatusMsg: "Installing the TikTok posting browser..."; Components: tiktok; Flags: runhidden
Filename: "{app}\python\python.exe"; Parameters: """{app}\addons.py"" nvidia --pause-on-error"; WorkingDir: "{app}"; StatusMsg: "Installing the NVIDIA RTX AI pack (about 6 GB). Progress shows in the black window, keep it open..."; Components: nvidia
Filename: "{app}\python\python.exe"; Parameters: """{app}\app.py"""; WorkingDir: "{app}"; Description: "Start Chipper Studio now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; the program and the big NVIDIA downloads go; your clips and settings stay unless you delete the folder yourself
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\nvvfx"
Type: filesandordirs; Name: "{app}\bin"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\work"

[Messages]
WelcomeLabel2=This installs Chipper Studio [ver] on your computer.%n%nEverything it needs (Python, FFmpeg, cloudflared) comes with it. Your clips and settings are kept when you update.
