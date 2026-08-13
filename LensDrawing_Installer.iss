; LensDrawing Installer Script v5
; 适配重构后 pywebview + Flask 架构
; 配合 build_v5.py 生成的 LensDrawing_v5 目录使用

#define MyAppName "LensDrawing"
#define MyAppVersion "3.5"
#define MyAppPublisher "Lens Drawing Tool Team"
#define MyAppExeName "LensDrawing.exe"

[Setup]
; 基本信息
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion=3.5.0.0
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; 输出设置
OutputDir={#SourcePath}\installer_output
OutputBaseFilename=LensDrawing_{#MyAppVersion}_Setup
; 压缩
Compression=lzma2
SolidCompression=yes
; 界面
WizardStyle=modern
; 图标
SetupIconFile={#SourcePath}\icon.ico
; 权限
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 架构
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 卸载时关闭程序
CloseApplications=force

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 主程序
Source: "{#SourcePath}\dist\LensDrawing\LensDrawing.exe"; DestDir: "{app}"; Flags: ignoreversion
; _internal 目录 (所有依赖)
Source: "{#SourcePath}\dist\LensDrawing\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; 面向软件使用人员的版本说明
Source: "{#SourcePath}\V3.5_版本更新说明.md"; DestDir: "{app}"; Flags: ignoreversion
; VC++ Redistributable
Source: "{#SourcePath}\installer_deps\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
; WebView2 Runtime (如果存在) - 使用实际的 Evergreen Standalone Installer 文件名
Source: "{#SourcePath}\installer_deps\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\V3.5 版本更新说明"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\V3.5_版本更新说明.md"""
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 静默安装 VC++ Redistributable
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "正在安装 Visual C++ 运行时..."; Check: VCRedistNeedsInstall
; 静默安装 WebView2 Runtime (如果系统未安装)
Filename: "{tmp}\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Parameters: "/silent /install"; StatusMsg: "正在安装 WebView2 运行时..."; Check: WebView2NeedsInstall
; 安装完成后启动程序
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Code]
// 检查 VC++ Redistributable 是否已安装
function VCRedistNeedsInstall: Boolean;
begin
  Result := not RegKeyExists(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64');
  if not Result then
  begin
    Result := not RegValueExists(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Version');
  end;
end;

// 检查 WebView2 Runtime 是否已安装
function WebView2NeedsInstall: Boolean;
begin
  // 检查注册表中是否存在 WebView2 Runtime
  Result := not RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BEB-152B52E44B8B}');
  if Result then
  begin
    // 也检查 HKCU
    Result := not RegKeyExists(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BEB-152B52E44B8B}');
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // 安装完成后的额外操作
  end;
end;
