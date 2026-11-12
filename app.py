#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用入口：暗色主题、单实例、创建主窗口并进入事件循环。

一键升级的关键在这里：安装包必须等 GUI 事件循环完全退出后才启动，否则
Inno Setup 的 CloseApplications 会和仍在运行的旧 exe 抢文件。
"""
import os
import sys


def _acquire_single_instance():
    """避免多开导致全局热键重复注册、user_config.json 互相覆盖。"""
    from PySide6.QtCore import QLockFile
    global _instance_lock
    try:
        lock = QLockFile(os.path.join(
            os.environ.get("TEMP", os.path.expanduser("~")),
            "TranslationTool.lock"))
        # 先正常抢锁：确有另一实例运行时不去做任何删除动作，避免误删活锁。
        if lock.tryLock(0):
            _instance_lock = lock
            return True
        # 只有抢锁失败才尝试清理崩溃残留的 stale 锁，然后重试一次。
        # removeStaleLockFile 自身会校验 PID/主机名，不会删掉活实例的锁。
        try:
            if lock.removeStaleLockFile() and lock.tryLock(0):
                _instance_lock = lock
                return True
        except Exception:
            pass
        return False
    except Exception:
        return True


_instance_lock = None


def run():
    smoke = "--smoke-test" in sys.argv
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtGui import QIcon

    from theme import apply_theme
    from app_paths import resource_path

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "trans.screenshot.translator")
    except Exception:
        pass

    app = QApplication.instance() or QApplication(sys.argv)
    # 托盘常驻：仅当显式 quit() 才退出，避免主窗口隐藏/销毁后 app 自动退出
    app.setQuitOnLastWindowClosed(False)

    if not smoke and not _acquire_single_instance():
        QMessageBox.information(
            None, "翻译工具",
            "翻译工具已经在运行中。\n请检查系统托盘，或先关闭已有窗口。")
        return 0

    icon_path = resource_path("assets/app_icon.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    start_minimized = "--minimized" in sys.argv

    try:
        from main_window import MainWindow
        window = MainWindow(start_minimized=start_minimized)
        apply_theme(app, window)
    except Exception as exc:
        # --windowed 打包后没有控制台，启动失败必须让用户看得见
        QMessageBox.critical(None, "启动失败",
                             f"程序初始化失败：{exc}\n\n"
                             "可尝试重启程序，或重新安装最新版。")
        return 1

    # 打包自检参数：构造完主窗口就退出并写一份诊断日志，用来验证
    # 冻结后资源路径 / 依赖 / 配置目录都正确（普通用户不会用到）。
    if smoke:
        from PySide6.QtCore import QTimer
        import tempfile
        from pathlib import Path
        import importlib.util
        try:
            modules = ["PySide6", "qfluentwidgets", "faster_whisper",
                       "av", "ctranslate2", "sounddevice", "soundcard",
                       "winrt", "cv2", "PIL", "docx", "pptx", "fitz"]
            available = [m for m in modules if importlib.util.find_spec(m)]
            Path(tempfile.gettempdir(), "TranslationTool-smoke.log").write_text(
                "OK\n"
                f"frozen={getattr(sys, 'frozen', False)}\n"
                f"app_dir={resource_path('assets').parent}\n"
                f"config={os.path.abspath(__import__('config').CONFIG_FILE)}\n"
                f"modules={','.join(available)}\n",
                encoding="utf-8")
        except Exception as exc:
            print(f"smoke log failed: {exc}", file=sys.stderr)
        QTimer.singleShot(1500, app.quit)
    else:
        # 先 show 再按需最小化：托盘不可用时窗口必须留在任务栏
        window.show()

    ret = app.exec()

    # 应用内升级：GUI 事件循环已退出，此时启动安装包最安全。
    # /RESTARTAPP=1 由 installer.iss 检测，静默安装完成后自动拉起新版本。
    try:
        from updater import consume_pending_install, launch_installer
        pending = consume_pending_install()
        if pending:
            launch_installer(pending, restart=True)
    except Exception as exc:
        print(f"启动升级安装包失败: {exc}", file=sys.stderr)

    return ret


if __name__ == "__main__":
    sys.exit(run())
