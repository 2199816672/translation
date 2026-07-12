#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主窗口：Fluent 导航 + 主页/设置页。"""
import os
import sys
import threading

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QIcon, QAction
from qfluentwidgets import FluentIcon, FluentWindow, InfoBar, InfoBarPosition
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication

from config import (
    OCR_LANGUAGES, SUPPORTED_TARGET_LANGS, load_user_config,
)
from ocr_recognizer import get_ocr_instance
from translator import get_translator_instance, TranslationError
from pages.home_page import HomePage
from pages.settings_page import SettingsPage
from screenshot_overlay import ScreenshotOverlay, FullscreenCapture


APP_NAME = "截图翻译工具"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _set_autostart(enable):
    """写入/删除 Windows 注册表实现开机自启。"""
    import winreg
    exe = os.path.abspath(sys.argv[0])
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                             winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe}" --minimized')
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def _get_autostart():
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                             winreg.KEY_READ)
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


class MainWindow(FluentWindow):
    _sig_ocr_done = Signal(str, str)
    _sig_trans_done = Signal(str)
    _sig_status = Signal(str, str)

    def __init__(self, start_minimized=False):
        super().__init__()
        self.cfg = load_user_config()
        self.ocr = None
        self.translator = get_translator_instance()
        self._overlay = None
        self._ocr_target = None

        self.home = HomePage(self)
        self.settings = SettingsPage(self)

        self.addSubInterface(self.home, FluentIcon.GAME, "翻译")
        self.addSubInterface(self.settings, FluentIcon.SETTING, "设置")

        self.setWindowTitle(APP_NAME)
        self.resize(1000, 700)

        self._setup_tray()
        self._wire()
        self._init_ocr_async()
        self._setup_hotkey()
        self._auto_check_update()

        if start_minimized or self.cfg.get("start_minimized", False):
            QTimer.singleShot(0, self._minimize_to_tray)

    def _wire(self):
        self.home.screenshotRequested.connect(self._start_screenshot)
        self.home.fullscreenRequested.connect(self._start_fullscreen)
        self.home.translateRequested.connect(self._on_home_translate)
        self.home.extractRequested.connect(self._on_home_extract)
        self.settings.saved.connect(self._on_settings_saved)
        self._sig_ocr_done.connect(self._on_ocr_done)
        self._sig_trans_done.connect(self._on_trans_done)
        self._sig_status.connect(self._on_status)

    def _sync_target_lang(self):
        """从主页语言下拉框同步目标语言到翻译器。"""
        combo_text = self.home.lang_combo.currentText()
        lang_code = SUPPORTED_TARGET_LANGS.get(combo_text, "zh-CN")
        self.translator.set_target_lang(lang_code)

    # ── 线程安全的 UI 更新 ──────────────────────────────────

    @Slot(str, str)
    def _on_status(self, text, color):
        self.home.set_status(text)
        self.home.status_dot.setStyleSheet(f"color: {color}; font-size: 14px;")

    def _auto_copy(self, text, stage):
        """根据设置自动复制到剪贴板。stage='ocr' 或 'trans'。"""
        if not self.cfg.get("auto_copy"):
            return
        target = self.cfg.get("auto_copy_target", "source")
        if target == "source" and stage == "ocr":
            if text and text.strip():
                from PySide6.QtWidgets import QApplication
                QApplication.clipboard().setText(text)
                self._on_status("已自动复制原文", "#4ade80")
        elif target == "translation" and stage == "trans":
            if text and text.strip():
                from PySide6.QtWidgets import QApplication
                QApplication.clipboard().setText(text)
                self._on_status("已自动复制译文", "#4ade80")

    @Slot(str, str)
    def _on_ocr_done(self, text, target):
        if target == "overlay_extract":
            if self._overlay:
                self._overlay.show_ocr_result(text)
            self.home.set_ocr_text(text)
            self._on_status("识别完成", "#4ade80")
        elif target == "overlay_translate":
            self._overlay_ocr_text = text
            self.home.set_ocr_text(text)
            self._on_status("识别完成，正在翻译...", "#eab308")
            self._do_translate_overlay(text)
        elif target == "home":
            self.home.set_ocr_text(text)
            self._auto_copy(text, "ocr")
            if text.strip():
                self._on_status("识别完成，正在翻译...", "#eab308")
                self._do_translate_home(text)
            else:
                self._on_status("未识别到文字", "#71717a")
        elif target == "home_extract":
            self.home.set_ocr_text(text)
            self._on_status("识别完成" if text.strip() else "未识别到文字",
                            "#4ade80" if text.strip() else "#71717a")

    @Slot(str)
    def _on_trans_done(self, text):
        if self._overlay and self._overlay.isVisible():
            self._overlay.show_trans_result(text)
        self.home.set_trans_text(text)
        if text and text.strip():
            self._on_status("翻译完成", "#4ade80")
        else:
            self._on_status("翻译结果为空，请检查网络或更换翻译API", "#eab308")
        self._auto_copy(text, "trans")

    # ── OCR 初始化 ──────────────────────────────────────────

    def _init_ocr_async(self):
        self._on_status("正在初始化 OCR 模型...", "#eab308")

        def init():
            ocr = get_ocr_instance(OCR_LANGUAGES)
            self.ocr = ocr
            self._sig_status.emit("就绪", "#4ade80")

        threading.Thread(target=init, daemon=True).start()

    # ── 快捷键 ──────────────────────────────────────────────

    def _setup_hotkey(self):
        hotkey = self.cfg.get("hotkey", "ctrl+shift+a")
        try:
            import keyboard
            keyboard.add_hotkey(hotkey, lambda: self.home.screenshotRequested.emit())
        except Exception as e:
            self.settings.show_hotkey_error(str(e))

    def _auto_check_update(self):
        if not self.cfg.get("auto_check_update", True):
            return
        from check_update import UpdateChecker, BILIBILI_DYNAMIC_URL

        def _on_result(result):
            if result["error"]:
                from qfluentwidgets import InfoBar, InfoBarPosition
                InfoBar.warning(
                    "版本检测失败",
                    result["error"],
                    duration=4000,
                    position=InfoBarPosition.TOP, parent=self.home,
                )
                return
            if result["has_update"]:
                self._show_update_dialog(result["current"], result["latest"],
                                         BILIBILI_DYNAMIC_URL)
            else:
                from qfluentwidgets import InfoBar, InfoBarPosition
                InfoBar.success(
                    "已是最新版本",
                    f"当前版本 v{result['current']} 已是最新",
                    duration=3000,
                    position=InfoBarPosition.TOP, parent=self.home,
                )

        checker = UpdateChecker()
        self._update_checker = checker
        checker.check(_on_result)

    def _show_update_dialog(self, current, latest, url):
        from qfluentwidgets import MessageBoxBase, SubtitleLabel, PrimaryPushButton
        from PySide6.QtWidgets import QLabel
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        dialog = MessageBoxBase(self)
        dialog.setWindowTitle("发现新版本")

        title = SubtitleLabel(f"新版本 {latest} 可用", dialog)
        dialog.viewLayout.addWidget(title)

        info_label = SubtitleLabel(
            f"当前版本 v{current} → 最新 {latest}",
            dialog,
        )
        dialog.viewLayout.addWidget(info_label)

        link_label = QLabel(dialog)
        link_label.setText(
            f'<a href="{url}" style="color: #3b82f6; text-decoration: underline;">'
            '点击前往作者B站动态下载最新版</a>'
        )
        link_label.setOpenExternalLinks(True)
        link_label.setStyleSheet("QLabel { font-size: 14px; }")
        dialog.viewLayout.addWidget(link_label)

        dialog.yesButton.setText("我知道了")
        go_btn = PrimaryPushButton("立刻前往", dialog.buttonGroup)
        dialog.buttonLayout.insertWidget(0, go_btn)
        dialog.cancelButton.hide()

        go_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))

        dialog.exec()

    # ── 系统托盘 ────────────────────────────────────────────

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self)
        # 优先用窗口图标，兜底用应用图标
        icon = self.windowIcon()
        if icon.isNull():
            from PySide6.QtGui import QIcon
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "assets", "app_icon.png")
            icon = QIcon(icon_path)
        self._tray.setIcon(icon)
        self.setWindowIcon(icon)
        self._tray.setToolTip(APP_NAME)
        self._tray.activated.connect(self._on_tray_activated)

        menu = QMenu()
        menu.setStyleSheet("QMenu { background: #1a1a1e; color: #e4e4e7; border: 1px solid #333; }"
                           "QMenu::item:selected { background: #3b82f6; }")
        show_action = QAction("显示主窗口", menu)
        show_action.triggered.connect(self._restore_from_tray)
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_from_tray()

    def _minimize_to_tray(self):
        self.hide()
        self._tray.showMessage(APP_NAME, "已最小化到系统托盘", QSystemTrayIcon.MessageIcon.Information, 1500)

    def _restore_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _quit_app(self):
        self._tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self.cfg.get("minimize_to_tray", True):
            event.ignore()
            self._minimize_to_tray()
        else:
            self._tray.hide()
            event.accept()

    # ── 截图 ────────────────────────────────────────────────

    @Slot()
    def _start_screenshot(self):
        self._on_status("请选择截图区域...", "#eab308")
        self.showMinimized()
        QTimer.singleShot(300, self._do_screenshot)

    def _do_screenshot(self):
        auto_translate = self.cfg.get("auto_translate", False)
        self._overlay = ScreenshotOverlay(auto_translate=auto_translate)
        self._overlay.extractRequested.connect(self._on_overlay_extract)
        self._overlay.translateRequested.connect(self._on_overlay_translate)
        self._overlay.screenshotTaken.connect(self._on_overlay_screenshot)
        self._overlay.finished.connect(self._on_overlay_finished)
        self._overlay.start_capture()

    def _on_overlay_finished(self):
        self.showNormal()
        self.activateWindow()
        self._overlay = None

    def _on_overlay_screenshot(self, path):
        self.home.set_image(path)

    @Slot(str)
    def _on_overlay_extract(self, path):
        self._do_ocr_for_overlay(path, "overlay_extract")

    @Slot(str)
    def _on_overlay_translate(self, path):
        self._do_ocr_for_overlay(path, "overlay_translate")

    def _do_ocr_for_overlay(self, image_path, target):
        if self.ocr is None:
            self._on_status("OCR 未就绪，请稍候...", "#eab308")
            return
        self._on_status("正在识别文字...", "#eab308")

        def run():
            try:
                text = self.ocr.recognize(image_path)
                self._sig_ocr_done.emit(text, target)
            except Exception as e:
                msg = str(e)
                self._sig_status.emit(msg, "#e5635f")

        threading.Thread(target=run, daemon=True).start()

    def _do_translate_overlay(self, text):
        self._on_status("正在翻译...", "#eab308")
        self._sync_target_lang()

        def run():
            try:
                result = self.translator.translate(text)
                self._sig_trans_done.emit(result)
            except TranslationError as e:
                self._sig_status.emit(str(e), "#e5635f")
            except Exception as e:
                self._sig_status.emit(f"翻译失败: {e}", "#e5635f")

        threading.Thread(target=run, daemon=True).start()

    # ── 全屏截图 ────────────────────────────────────────────

    @Slot()
    def _start_fullscreen(self):
        self._on_status("正在全屏截图...", "#eab308")

        def capture():
            path = FullscreenCapture.capture()
            if path:
                self.home.set_image(path)
                self._do_ocr_home(path)
            else:
                self._sig_status.emit("截图失败", "#e5635f")

        threading.Thread(target=capture, daemon=True).start()

    # ── 主页 OCR + 翻译 ────────────────────────────────────

    def _do_ocr_home(self, image_path, target="home"):
        if self.ocr is None:
            self._on_status("OCR 未就绪，请稍候...", "#eab308")
            return
        self._on_status("正在识别文字...", "#eab308")

        def run():
            try:
                text = self.ocr.recognize(image_path)
                self._sig_ocr_done.emit(text, target)
            except Exception as e:
                self._sig_status.emit(str(e), "#e5635f")

        threading.Thread(target=run, daemon=True).start()

    def _do_translate_home(self, text):
        self._on_status("正在翻译...", "#eab308")
        self._sync_target_lang()

        def run():
            try:
                result = self.translator.translate(text)
                self._sig_trans_done.emit(result)
            except TranslationError as e:
                self._sig_status.emit(str(e), "#e5635f")
            except Exception as e:
                self._sig_status.emit(f"翻译失败: {e}", "#e5635f")

        threading.Thread(target=run, daemon=True).start()

    @Slot(str)
    def _on_home_translate(self, text):
        if not text or not text.strip():
            self._on_status("没有需要翻译的文本", "#71717a")
            return
        self._do_translate_home(text)

    @Slot()
    def _on_home_extract(self):
        if not self.home._current_image_path:
            self._on_status("请先截图", "#71717a")
            return
        self._do_ocr_home(self.home._current_image_path, target="home_extract")

    # ── 设置保存 ────────────────────────────────────────────

    @Slot()
    def _on_settings_saved(self):
        old_hotkey = self.cfg.get("hotkey", "ctrl+shift+a")
        self.cfg = load_user_config()
        new_hotkey = self.cfg.get("hotkey", "ctrl+shift+a")
        if old_hotkey != new_hotkey:
            try:
                import keyboard
                keyboard.unhook_all_hotkeys()
                keyboard.add_hotkey(new_hotkey, lambda: self.home.screenshotRequested.emit())
            except Exception as e:
                self.settings.show_hotkey_error(str(e))
