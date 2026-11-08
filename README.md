# 截图翻译工具 v1.3.0

基于 PySide6 的截图翻译工具，支持区域截图、多引擎 OCR 识别、多语言翻译、文档格式转换与字幕处理。

## 功能

- **区域截图** — 鼠标框选区域，右键/ESC 取消
- **全屏截图** — 一键截取全屏
- **提取文字** — 对截图区域 OCR 识别
- **翻译** — 选区后直接翻译，叠加显示译文；点击切换原文/译文
- **自动翻译** — 开启后选区完成自动翻译，无需手动点菜单
- **OCR 引擎切换** — 支持 Windows 系统自带 OCR 和 EasyOCR，设置页一键切换
- **主页操作** — 提取文字、翻译、复制原文/译文、清除
- **快捷键** — 默认 `Ctrl+Shift+A` 唤起截图（设置页按键录入，可自定义）
- **7 个翻译 API** — 3 免费 + 4 付费，免费 API 也可填自己的密钥
- **自动复制** — OCR 完成后自动复制原文或译文到剪贴板
- **错误诊断** — 翻译失败时自动诊断网络问题并给出中文提示
- **系统托盘** — 关闭窗口最小化到托盘，托盘右键可快速截图/恢复/退出
- **开机自启动** — 可设置开机自动启动并最小化到托盘待命
- **自动更新检测** — 启动时自动检测 GitHub 最新版本，发现新版本弹窗提示
- **手动检查更新** — 设置页一键检测是否为最新版
- **文档处理** — 文档/字幕翻译、格式转换、翻译后一键导出目标格式
- **格式转换** — PDF / Word / TXT / 图片 / Excel / CSV 之间互转
- **字幕互转** — SRT · VTT · ASS · LRC 任意互转，翻译保留时间轴
- **中文繁体** — 目标语言支持「中文(繁体)」，繁体源文本选「中文」即转简体

## OCR 引擎

| 引擎 | 说明 | 打包自带 | 首次使用 |
|------|------|---------|---------|
| **Windows 系统自带** (默认) | 开箱即用，无需下载模型，速度快 | ✅ | 直接使用 |
| **EasyOCR** | 识别更准确，支持更多语言 | ❌ | 需下载约 100MB 模型 |

可在设置页切换 OCR 引擎，未安装的引擎会提示一键安装。

## 翻译 API

| 引擎 | 类型 | 说明 |
|------|------|------|
| Bing 微软 | 免费 | 国内可用，可选填 Azure 密钥 |
| MyMemory | 免费 | 全球可用，可选填授权密钥提升额度 |
| Google | 免费 | 需 VPN，可选填 Google Cloud 密钥 |
| DeepL | 付费 | 需 deepl.com/pro-api 密钥 |
| 百度翻译 | 付费 | 需 fanyi-api.com AppID + SecretKey |
| 腾讯翻译 | 付费 | 需 console.cloud.tencent.com SecretId + SecretKey |
| 火山翻译 | 付费 | 需 console.volcengine.com AppID + Token |

## 文档处理（翻译 + 格式转换 + 字幕）

「文档处理」页可加载文档或字幕：**只翻译**、**直接转格式**、或**翻译并转换**（翻译完成自动把译文存为目标格式）。

| 源格式 | 可转换为 |
|--------|---------|
| PDF | Word(docx) / PNG 图片 / JPG 图片 / TXT |
| Word (docx) | PDF / TXT |
| TXT | Word(docx) / PDF |
| 图片 (png/jpg/bmp/gif) | PDF |
| Excel (xlsx) | CSV |
| CSV | Excel (xlsx) |
| 字幕 (srt/vtt/ass/lrc) | SRT / VTT / ASS / LRC / TXT |

说明：
- **Word → PDF** 优先使用本机 Microsoft Word，未安装时自动回退 LibreOffice（两者均无时会给出提示）。
- **PDF → Word** 由 `pdf2docx` 完成；扫描版（图片型）PDF 无法直接转换。
- **字幕翻译**逐条进行并保留时间轴，失败条目保留原文。
- **TXT → PDF** 使用系统微软雅黑字体，支持中文排版。
- 输出文件保存在源文件同目录，命名 `原名_converted.<ext>` / `原名_译文.<ext>`，同名自动加序号。

## 安装

```bash
pip install -r requirements.txt
python main.py
```

## 打包

```bash
pip install pyinstaller
pyinstaller --noconfirm --onedir --windowed --name "ScreenshotTranslator_v1.3.0" --icon "assets/app_icon.png" --add-data "assets;assets" --add-data "pages;pages" --exclude-module torch --exclude-module easyocr --exclude-module scipy --exclude-module matplotlib --exclude-module pandas --hidden-import keyboard --hidden-import winrt --hidden-import winrt.windows.media.ocr --hidden-import winrt.windows.globalization --hidden-import winrt.windows.graphics.imaging --hidden-import winrt.windows.storage.streams main.py
```

打包后默认使用 Windows OCR，体积约 139MB (ZIP)。用户可在设置中切换到 EasyOCR 并安装。

## 项目结构

```
trans/
├── main.py                # 入口
├── app.py                 # QApplication 启动 + 主题 + 图标
├── main_window.py         # FluentWindow 主窗口 + 信号调度 + 系统托盘
├── theme.py               # Palette + QSS 样式（Zinc 暗色）
├── config.py              # 配置管理（API、语言、OCR引擎、用户配置）
├── check_update.py        # 版本更新检测（gh CLI + HTTP）
├── free_translator.py     # 免费翻译引擎（Bing/MyMemory/Google）
├── translator.py          # 多翻译 API 封装 + 错误诊断
├── ocr_recognizer.py      # OCR 多引擎封装（Windows OCR + EasyOCR）
├── screenshot_overlay.py  # 全屏覆盖层（选区、菜单、提取弹窗、翻译叠加）
├── doc_parser.py          # 文档/字幕文本提取
├── subtitle.py            # 字幕解析与生成（SRT/VTT/ASS）
├── format_convert.py      # 格式转换调度（校验 + 写出）
├── convert_libs.py        # 格式转换纯计算（返回字节）
├── office_bridge.py       # PDF⇄Word（pdf2docx / Word COM / LibreOffice）
├── widgets.py             # 自定义控件（HotKeyInput、FlatCheckBox）
├── assets/
│   └── app_icon.png       # 应用图标
├── pages/
│   ├── home_page.py       # 主页（截图、预览、OCR、翻译）
│   ├── doc_page.py        # 文档处理（翻译、格式转换、字幕）
│   └── settings_page.py   # 设置（API、密钥、OCR引擎、快捷键、自动操作）
├── user_config.json       # 用户配置（自动生成）
└── requirements.txt       # 依赖
```

## 技术栈

| 类别 | 技术 |
|---|---|
| GUI | PySide6 + PySide6-Fluent-Widgets |
| OCR | Windows Media OCR / EasyOCR (可选) |
| 翻译 | Bing 逆向 / MyMemory / Google + DeepL / 百度 / 腾讯 / 火山 |
| 主题 | 自定义 Zinc 暗色 QSS |

## 许可证

MIT License
