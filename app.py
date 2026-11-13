#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用入口：暗色主题、单实例、创建主窗口并进入事件循环。

一键升级的关键在这里：安装包必须等 GUI 事件循环完全退出后才启动，否则
Inno Setup 的 CloseApplications 会和仍在运行的旧 exe 抢文件。
"""
import atexit
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

# qInstallMessageHandler 不会替 Python 持有回调引用，必须留一个模块级变量
_qt_message_handler = None
_qt_filter_installed = False


def _uninstall_qt_log_filter():
    """事件循环结束后卸掉进程级钩子。

    Qt 静态析构阶段仍可能再发 qWarning，那时 Python 解释器 / GIL 可能已在
    finalize，回调进来会直接崩。除了在 exec() 返回后主动卸载，再用 atexit
    覆盖「单实例拦截 / 启动失败 / smoke-test」这些提前 return 的路径。
    """
    global _qt_message_handler, _qt_filter_installed
    if not _qt_filter_installed:
        return
    _qt_filter_installed = False
    try:
        from PySide6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(None)
    except Exception:
        pass
    _qt_message_handler = None


def _install_qt_log_filter():
    """只静默 Qt 自身的一条无害字体警告，其它日志原样转发。

    Qt 在弹出 QComboBox 下拉框时会把 pointSizeF() 得到的 -1 / -0.75 再传回
    setPointSize / setPointSizeF，控制台于是刷
    ``QFont::setPointSize: Point size <= 0 (-1), must be greater than 0``。
    用一个不带任何样式表的裸 QComboBox 也能复现，与程序里的字体设置无关，
    也不会影响文字显示或功能。这里精确过滤这一条，避免刷屏吓到用户；
    真正的 Qt 错误照常输出。设环境变量 TT_SHOW_QT_WARNINGS=1 可关闭过滤。
    """
    global _qt_message_handler, _qt_filter_installed
    if os.environ.get('TT_SHOW_QT_WARNINGS') == '1':
        return
    try:
        from PySide6.QtCore import qInstallMessageHandler
    except Exception:
        return

    def handler(mode, context, message):
        try:
            if ('QFont::setPointSize' in message
                    and 'Point size <= 0' in message):
                return
            stream = sys.stderr
            if stream is not None:
                stream.write(str(message) + '\n')
                stream.flush()
        except Exception:
            pass

    _qt_message_handler = handler
    try:
        qInstallMessageHandler(handler)
        _qt_filter_installed = True
        atexit.register(_uninstall_qt_log_filter)
    except Exception:
        _qt_message_handler = None


def _report_pending_install_failure(path, exc):
    """升级安装包启动失败时给用户可见的提示。

    冻结态 --windowed 下 sys.stdout / sys.stderr 可能是 None，直接
    print(..., file=sys.stderr) 会在 except 里再抛 AttributeError：用户既
    看不到原因，也不知道该手动装哪个包。这里用系统弹窗 + 本地日志兜底，
    两条通道都失败也不能影响退出流程。
    """
    msg = (f"升级安装包启动失败，请手动运行安装程序或到发布页重新下载。\n\n"
           f"安装包：{path or '未知'}\n原因：{exc}")
    try:
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "翻译工具 - 升级", 0x10)
    except Exception:
        pass
    try:
        log = os.path.join(
            os.environ.get('TEMP') or os.path.expanduser('~'),
            'TranslationTool-upgrade.log')
        with open(log, 'a', encoding='utf-8') as f:
            f.write(msg.replace('\n', ' ') + '\n')
    except Exception:
        pass


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
    _install_qt_log_filter()
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

    # 启动期要 import winrt / 探测 OCR / 建 4 个页面，慢机器上会有几秒空白，
    # 用户容易以为没点开而重复双击（又被单实例拦下）。给一条即时反馈。
    splash = None
    if not smoke and not start_minimized:
        try:
            from PySide6.QtWidgets import QSplashScreen
            from PySide6.QtGui import QPixmap, QPainter, QFont, QColor
            from PySide6.QtCore import Qt
            pix = QPixmap(460, 140)
            pix.fill(QColor("#1a1a1e"))
            painter = QPainter(pix)
            painter.setPen(QColor("#e4e4e7"))
            font = QFont()
            font.setPointSize(13)
            painter.setFont(font)
            painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter,
                             "翻译工具\n正在准备识别引擎…")
            painter.end()
            splash = QSplashScreen(pix)
            splash.show()
            app.processEvents()
        except Exception:
            splash = None

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
        if splash is not None:
            splash.finish(window)

    ret = app.exec()
    # 事件循环已结束：先卸掉进程级 Qt 日志钩子，避免静态析构阶段回调
    # 一个正在 finalize 的 Python 解释器（提前 return 的路径由 atexit 兜底）。
    _uninstall_qt_log_filter()

    # 应用内升级：GUI 事件循环已退出，此时启动安装包最安全。
    # /RESTARTAPP=1 由 installer.iss 检测，静默安装完成后自动拉起新版本。
    pending = None
    try:
        from updater import consume_pending_install, launch_installer
        pending = consume_pending_install()
        if pending:
            launch_installer(pending, restart=True)
    except Exception as exc:
        # 不能只 print 到 stderr：冻结态 windowed 下 stderr 可能是 None，
        # 而且 GUI 已退出，用户根本看不到控制台。必须给可见提示。
        _report_pending_install_failure(pending, exc)

    return ret


if __name__ == "__main__":
    sys.exit(run())
