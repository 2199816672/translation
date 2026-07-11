#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主窗口：Fluent 导航 + 主页/设置页。"""
import os
import sys
import threading

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QIcon
from qfluentwidgets import FluentIcon, FluentWindow, InfoBar, InfoBarPosition

from config import (
    OCR_LANGUAGES, SUPPORTED_TARGET_LANGS, load_user_config,
)
from ocr_recognizer import get_ocr_instance
from translator import get_translator_instance
from pages.home_page import HomePage
from pages.settings_page import SettingsPage
from screenshot_overlay import ScreenshotOverlay, FullscreenCapture


class MainWindow(FluentWindow):
    _sig_ocr_done = Signal(str, str)
    _sig_trans_done = Signal(str)
    _sig_status = Signal(str, str)

    def __init__(self):
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

        self.setWindowTitle("截图翻译工具")
        self.resize(1000, 700)

        self._wire()
        self._init_ocr_async()
        self._setup_hotkey()

    def _wire(self):
        self.home.screenshotRequested.connect(self._start_screenshot)
        self.home.fullscreenRequested.connect(self._start_fullscreen)
        self.home.translateRequested.connect(self._on_home_translate)
        self.home.extractRequested.connect(self._on_home_extract)
        self.settings.saved.connect(self._on_settings_saved)
        self._sig_ocr_done.connect(self._on_ocr_done)
        self._sig_trans_done.connect(self._on_trans_done)
        self._sig_status.connect(self._on_status)

    # ── 线程安全的 UI 更新 ──────────────────────────────────

    @Slot(str, str)
    def _on_status(self, text, color):
        self.home.set_status(text)
        self.home.status_dot.setStyleSheet(f"color: {color}; font-size: 14px;")

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
        self._on_status("翻译完成", "#4ade80")

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
            print(f"快捷键注册失败: {e}")

    # ── 截图 ────────────────────────────────────────────────

    @Slot()
    def _start_screenshot(self):
        self._on_status("请选择截图区域...", "#eab308")
        self.showMinimized()
        QTimer.singleShot(300, self._do_screenshot)

    def _do_screenshot(self):
        self._overlay = ScreenshotOverlay()
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
                self._sig_status.emit(f"OCR 失败: {e}", "#e5635f")

        threading.Thread(target=run, daemon=True).start()

    def _do_translate_overlay(self, text):
        self._on_status("正在翻译...", "#eab308")

        def run():
            try:
                result = self.translator.translate(text)
                self._sig_trans_done.emit(result)
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
                self._sig_status.emit(f"OCR 失败: {e}", "#e5635f")

        threading.Thread(target=run, daemon=True).start()

    def _do_translate_home(self, text):
        self._on_status("正在翻译...", "#eab308")

        def run():
            try:
                result = self.translator.translate(text)
                self._sig_trans_done.emit(result)
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
        self.cfg = load_user_config()
        hotkey = self.cfg.get("hotkey", "ctrl+shift+a")
        try:
            import keyboard
            keyboard.unhook_all_hotkeys()
            keyboard.add_hotkey(hotkey, lambda: self.home.screenshotRequested.emit())
        except Exception as e:
            print(f"快捷键更新失败: {e}")
