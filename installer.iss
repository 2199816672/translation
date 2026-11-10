; 翻译工具 一键安装脚本（Inno Setup 6.5+）
;
; 版本号由 build_installer.py 通过 /dMyAppVersion 注入，唯一来源是
; config.py 的 APP_VERSION，避免脚本与实际程序版本不同步。
; 本文件必须保存为 UTF-8 with BOM，否则中文会乱码。

#ifndef MyAppVersion
  #define MyAppVersion "1.5.0"
#endif
; 待打包的程序目录。手工编译时使用默认 dist\TranslationTool；
; build_installer.py 会通过 /dSourceDir 指向每次构建的全新暂存目录。
#ifndef SourceDir
  #define SourceDir "dist\TranslationTool"
#endif
#define MyAppName "翻译工具"
#define MyAppExe "TranslationTool.exe"
#define MyAppPublisher "2199816672"
#define MyAppURL "https://github.com/2199816672/translation"

[Setup]
; 固定 AppId 是「覆盖升级」的关键：相同 ID 的已安装版本会被识别为升级，
; 自动沿用原安装目录、保留用户数据，并写入新的卸载信息。
AppId={{8C3E5F1A-6B27-4D09-9E4A-2F71D0B6C5E3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}.0
VersionInfoDescription={#MyAppName} 安装程序

; 用户级安装：双击即可安装，不弹 UAC；目录始终可写，配置 / 截图 /
; 模型能直接保存在程序目录（与免安装版行为一致）。
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\TranslationTool
DefaultGroupName=翻译工具
DisableDirPage=auto
DisableProgramGroupPage=yes
AllowNoIcons=yes

SetupIconFile=assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName} v{#MyAppVersion}
WizardStyle=modern
ShowLanguageDialog=no

Compression=lzma2/max
SolidCompression=yes
OutputDir=dist\installer
OutputBaseFilename=TranslationTool-Setup-v{#MyAppVersion}

; 升级时自动关闭正在运行的旧版本，避免 DLL 被占用导致覆盖失败
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
; 简体中文语言包由 kira-96 维护（MIT），许可见 packaging/ChineseSimplified.LICENSE.txt
Name: "chinesesimplified"; MessagesFile: "packaging\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
; 只打包程序本体。运行时生成的 user_config.json / screenshots / models
; 绝不能进入安装清单：一旦被登记，覆盖升级会用包内默认配置覆盖用户设置
; （ignoreversion），卸载时还会被当作安装文件删掉。
; build_installer.py 会先把这些文件移出源目录；这里的 Excludes 是第二道保险。
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "user_config.json, user_config.json.*, screenshots\*, models\*, converted\*, *.log, 使用说明.txt.bak"

; 面向普通用户的简短说明由 build_exe.py 复制进程序目录，随上面这条一起安装。

[Icons]
Name: "{group}\翻译工具"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\卸载 翻译工具"; Filename: "{uninstallexe}"
Name: "{autodesktop}\翻译工具"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "立即启动翻译工具"; Flags: nowait postinstall skipifsilent

[Code]
// 应用内一键升级启动安装包时带 /RESTARTAPP=1；静默安装完成后自动拉起新版本。
// 用 ssDone + Exec 而不是 [Run] 的 postinstall：后者在静默模式下不保证执行。
function RestartAfterUpgrade(): Boolean;
begin
  Result := CompareText(ExpandConstant('{param:RESTARTAPP|0}'), '1') = 0;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if (CurStep = ssDone) and RestartAfterUpgrade() then
    Exec(ExpandConstant('{app}\{#MyAppExe}'), '',
         ExpandConstant('{app}'), SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

// 卸载后清掉开机自启注册表项，避免残留一条指向已删除 exe 的启动命令。
// 使用 Inno Setup 原生注册表 API，不依赖外部 reg.exe（部分安全策略会拦截它）。
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Run', '截图翻译工具');
end;
