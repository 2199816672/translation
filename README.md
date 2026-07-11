# 截图翻译工具 v1.0.0

基于 PySide6 + EasyOCR 的截图翻译工具，支持区域截图、OCR 识别、多语言翻译。

## 功能

- **区域截图** — 鼠标框选区域，右键/ESC 取消
- **全屏截图** — 一键截取全屏
- **提取文字** — 对截图区域 OCR 识别，弹窗可自由选中复制
- **翻译** — 选区后直接翻译，叠加显示原文/译文，点击切换
- **复制/保存** — 选区后一键复制或保存截图
- **主页操作** — 提取文字、翻译、复制原文/译文、清空
- **快捷键** — 默认 `Ctrl+Shift+A` 唤起截图（可配置）
- **多 API** — 支持 Google / DeepL / 百度 / MyMemory 翻译

## 截图

启动后主界面为深色主题，包含：
- 控制栏：区域截图、全屏截图、语言选择、提取文字、翻译
- 左侧：截图预览
- 右侧：识别文本 + 翻译结果

## 安装

```bash
pip install -r requirements.txt
python main.py
```

首次运行 EasyOCR 会自动下载模型，需联网。

## 项目结构

```
trans/
├── main.py                # 入口
├── app.py                 # QApplication 启动 + 主题
├── main_window.py         # FluentWindow 主窗口
├── theme.py               # Palette + QSS 样式
├── config.py              # 配置管理
├── pages/
│   ├── home_page.py       # 主页（截图、预览、OCR、翻译）
│   └── settings_page.py   # 设置（API、密钥、快捷键）
├── screenshot_overlay.py  # 全屏覆盖层（选区、菜单、提取弹窗、翻译叠加）
├── ocr_recognizer.py      # EasyOCR 封装
├── translator.py          # 多翻译 API 封装
├── widgets.py             # 复用控件
└── requirements.txt       # 依赖
```

## 技术栈

| 层 | 技术 |
|---|---|
| GUI | PySide6 + PySide6-Fluent-Widgets |
| OCR | EasyOCR |
| 翻译 | deep-translator (Google/MyMemory)、DeepL、百度 |
| 主题 | 自定义 Zinc 暗色 QSS |

## 配置

设置页可配置：
- 翻译 API（Google / DeepL / 百度 / MyMemory）
- API 密钥
- 截图快捷键

## 许可证

MIT License
