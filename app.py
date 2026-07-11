#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用入口：Fluent 暗色主题、创建主窗口并进入事件循环。"""
import sys


def run():
    from PySide6.QtWidgets import QApplication

    from theme import apply_theme

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("trans.screenshot.translator")
    except Exception:
        pass

    app = QApplication.instance() or QApplication(sys.argv)

    from main_window import MainWindow
    window = MainWindow()
    apply_theme(app, window)
    window.show()
    sys.exit(app.exec())
