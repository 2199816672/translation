#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用入口：Fluent 暗色主题、创建主窗口并进入事件循环。"""
import os
import sys


def run():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from PySide6.QtCore import QSize

    from theme import apply_theme

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("trans.screenshot.translator")
    except Exception:
        pass

    app = QApplication.instance() or QApplication(sys.argv)

    # 加载应用图标
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app_icon.png")
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)

    start_minimized = "--minimized" in sys.argv

    from main_window import MainWindow
    window = MainWindow(start_minimized=start_minimized)
    apply_theme(app, window)
    if not start_minimized:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
