# 截图翻译工具 v1.1.1

基于 PySide6 + EasyOCR 的截图翻译工具，支持区域截图、OCR 识别、多语言翻译。

## 功能

- **区域截图** — 鼠标框选区域，右键/ESC 取消
- **全屏截图** — 一键截取全屏
- **提取文字** — 对截图区域 OCR 识别，弹窗可自由选中复制
- **翻译** — 选区后直接翻译，叠加显示译文；点击切换原文/译文
- **自动翻译** — 开启后选区完成自动翻译，无需手动点菜单
- **复制/保存** — 选区后一键复制或保存截图
- **编辑** — 选区内画笔、橡皮、文字标注，撤销/重做
- **主页操作** — 提取文字、翻译、复制原文/译文、清空
- **快捷键** — 默认 `Ctrl+Shift+A` 唤起截图（按键录入，可自定义）
- **多 API** — 7 个翻译引擎（3 免费 + 4 付费），免费 API 也可填自己的密钥
- **自动复制** — OCR 完成后自动复制原文或译文到剪贴板
- **错误诊断** — 翻译失败时自动诊断网络问题并给出中文提示
- **系统托盘** — 关闭窗口最小化到托盘，托盘右键可快速截图/恢复/退出
- **开机自启** — 可设置开机自动启动并最小化到托盘待命
- **自动更新检测** — 启动时自动检查 GitHub 最新版本，发现新版本弹窗提示前往B站动态更新
- **手动检查更新** — 设置页一键检测是否为最新版本

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

## 安装

```bash
pip install -r requirements.txt
python app.py
```

首次运行 EasyOCR 会自动下载模型，需联网。

## 项目结构

```
trans/
├── main.py                # 入口
├── app.py                 # QApplication 启动 + 主题 + 图标
├── main_window.py         # FluentWindow 主窗口 + 信号调度 + 系统托盘
├── theme.py               # Palette + QSS 样式（Zinc 暗色）
├── config.py              # 配置管理（API、语言、用户配置）
├── free_translator.py     # 免费翻译引擎（Bing/MyMemory/Google）
├── translator.py          # 多翻译 API 封装 + 错误诊断
├── ocr_recognizer.py      # EasyOCR 封装
├── screenshot_overlay.py  # 全屏覆盖层（选区、菜单、提取弹窗、翻译叠加）
├── widgets.py             # 自定义控件（HotKeyInput、FlatCheckBox）
├── assets/
│   ├── app_icon.png       # 应用图标
│   └── check.svg          # 勾选框图标
├── pages/
│   ├── home_page.py       # 主页（截图、预览、OCR、翻译）
│   └── settings_page.py   # 设置（API、密钥、快捷键、自动操作）
├── user_config.json       # 用户配置（自动生成）
└── requirements.txt       # 依赖
```

## 技术栈

| 层 | 技术 |
|---|---|
| GUI | PySide6 + PySide6-Fluent-Widgets |
| OCR | EasyOCR |
| 翻译 | Bing 逆向 / MyMemory API / Google (deep_translator) + DeepL / 百度 / 腾讯 / 火山 |
| 主题 | 自定义 Zinc 暗色 QSS |

## 配置

设置页可配置：
- 翻译 API（免费 API 可选填自己的密钥）
- 截图快捷键（按键录入）
- 自动翻译（选区后自动翻译）
- 自动复制（OCR 后自动复制原文/译文）
- 关闭行为（最小化到托盘 / 直接退出）
- 开机自启动
- 启动时自动检测更新
- 手动检查更新

## 许可证

MIT License
