#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主窗口：Fluent 导航 + 主页/设置页。"""
import os
import sys
import threading

from PySide6.QtCore import Qt, Signal, Slot, QTimer, QRect
from PySide6.QtGui import QIcon, QAction, QActionGroup
from qfluentwidgets import FluentIcon, FluentWindow, InfoBar, InfoBarPosition
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication

from config import (
    OCR_LANGUAGES, OCR_ENGINE, SUPPORTED_TARGET_LANGS, SUPPORTED_SOURCE_LANGS,
    load_user_config, APP_VERSION,
)
from app_paths import resource_path
from ocr_recognizer import get_ocr_instance, check_engine_available
from translator import get_translator_instance, TranslationError
from pages.home_page import HomePage
from pages.settings_page import SettingsPage
from pages.doc_page import DocPage
from pages.av_page import AVPage
from screenshot_overlay import ScreenshotOverlay, FullscreenCapture


# 内部标识 / 注册表自启动项名：保持不变，避免改名后旧的自启动项残留
APP_NAME = "截图翻译工具"
# 界面显示名：功能早就不止截图了，统一叫「翻译工具」
APP_DISPLAY_NAME = "翻译工具"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_command():
    """构造开机自启命令。

    旧实现用 ``os.path.abspath(sys.argv[0])``：源码运行时写进去的是
    ``main.py``（Windows 无法直接执行），开机自启静默失效；开机时 cwd 是
    System32，相对路径资源也会落错位置。这里一律用绝对路径。
    """
    from pathlib import Path
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return f'"{exe}" --minimized'
    python = Path(sys.executable).resolve()
    # 源码版用 pythonw 避免开机弹出黑框
    pythonw = python.with_name("pythonw.exe")
    if pythonw.is_file():
        python = pythonw
    script = Path(__file__).resolve().parent / "main.py"
    return f'"{python}" "{script}" --minimized'


def _set_autostart(enable):
    """写入/删除 Windows 注册表实现开机自启。返回是否真的写成功。"""
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                             winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception as exc:
        print(f"设置开机自启失败: {exc}")
        return False


def _get_autostart():
    """读回注册表值，并确认它仍指向当前程序（升级/移动后旧路径会失效）。"""
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                             winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
    except Exception:
        return False
    if not isinstance(value, str) or "--minimized" not in value:
        return False
    import re
    from pathlib import Path
    match = re.search(r'"([^"]+)"', value)
    # 升级 / 移动后旧路径已失效时显示「未开启」，用户重新勾选会写入正确路径
    return bool(match and Path(match.group(1)).is_file())


class MainWindow(FluentWindow):
    _sig_ocr_done = Signal(str, str)
    _sig_ocr_blocks = Signal(object)   # OCR 句子块（归一化坐标）
    _sig_trans_done = Signal(str)
    _sig_doc_trans_done = Signal(str)
    _sig_doc_sub_trans_done = Signal(list)
    _sig_doc_sub_progress = Signal(int, int)
    _sig_av_trans_done = Signal(list)
    _sig_av_trans_progress = Signal(int, int)
    _sig_overlay_trans_done = Signal(list, int)
    _sig_polish_home_done = Signal(str)
    _sig_polish_doc_done = Signal(str)
    _sig_polish_doc_sub_done = Signal(list)
    _sig_polish_overlay_done = Signal(list)
    _sig_polish_av_src_done = Signal(list)
    _sig_polish_av_trans_done = Signal(list)
    _sig_polish_failed = Signal()
    _sig_status = Signal(str, str)
    _sig_error = Signal(str)
    # 实时字幕：采集 / 识别 / 翻译都在后台线程，只通过信号回主线程
    _sig_caption_segment = Signal(int, str)
    _sig_caption_translation = Signal(int, str)
    _sig_caption_status = Signal(str, str)
    _sig_caption_error = Signal(str)
    _sig_caption_fatal = Signal(str)
    _sig_caption_speed = Signal(float)
    # 全局快捷键回调在 keyboard 库的后台线程触发，用信号回主线程分发
    _sig_hotkey = Signal(str)
    # 应用内升级：检查 / 下载都在后台线程，结果一律经信号回主线程
    _sig_update_result = Signal(str, str, str)
    _sig_update_found = Signal(object, bool)
    _sig_update_progress = Signal(int, int)
    _sig_update_finished = Signal(bool, str, str)

    def __init__(self, start_minimized=False):
        super().__init__()
        self.cfg = load_user_config()
        # 必须在各页面读配置之前完成旧快捷键迁移，否则设置页会把旧值写回来
        from config import save_user_config
        from hotkeys import HOTKEYS_MIGRATION_KEY, LEGACY_DEFAULT_HOTKEY
        if (not self.cfg.get(HOTKEYS_MIGRATION_KEY)
                and str(self.cfg.get('hotkey') or '') == LEGACY_DEFAULT_HOTKEY):
            self.cfg['hotkey'] = ''
            self.cfg[HOTKEYS_MIGRATION_KEY] = True
            save_user_config({'hotkey': '', HOTKEYS_MIGRATION_KEY: True})
        self.ocr = None
        self._ocr_loading = False
        self._ocr_reload_pending = False
        self.translator = get_translator_instance()
        self._overlay = None
        self._ocr_target = None
        self._overlay_ocr_text = ""
        self._overlay_blocks = []   # OCR 视觉段落块（归一化坐标），译文按原位置贴回用
        self._restore_after_capture = False
        self._last_trans_text = ""
        self._last_trans_result = ""
        self._last_trans_paras = []   # 覆盖层逐段译文，缓存命中时按原条数复用
        self._live_window = None
        self._live_pending = False
        self._restore_after_live = False
        self._live_src = 'auto'
        self._live_tgt = 'zh-CN'
        self._caption_window = None     # 运行中一直保留，隐藏 != 停止
        self._caption_engine = None
        self._caption_cfg = {}
        self._caption_starting = False  # 启动中：拒绝重复 start，防双启动
        self._caption_stopping = False  # 停止中：拒绝 start，防与残留线程竞争
        self._caption_notice = ''
        self._hotkeys = None
        self._update_thread = None
        self._upgrade_dialog = None
        self._upgrade_cancel = None
        self._upgrade_info = None
        self._update_stop = threading.Event()

        self.home = HomePage(self)
        self.doc = DocPage(self)
        self.av = AVPage(self)
        self.settings = SettingsPage(self)

        self.addSubInterface(self.home, FluentIcon.GAME, "翻译")
        self.addSubInterface(self.doc, FluentIcon.DOCUMENT, "文档处理")
        self.addSubInterface(self.av, FluentIcon.VIDEO, "音视频")
        self.addSubInterface(self.settings, FluentIcon.SETTING, "设置")

        self.setWindowTitle(APP_DISPLAY_NAME)
        # 音视频页内容约 700px 高；默认给足高度，避免结果卡片被迫滚动才看得全
        self.resize(1120, 760)
        # 限制最小尺寸，避免小屏幕下 Qt 强制放大窗口产生 setGeometry 警告
        # （更小的窗口由页面内的滚动区兜底，不会把内容硬裁掉）
        self.setMinimumSize(940, 620)

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
        self.home.liveTranslateRequested.connect(self._start_live_translate)
        self.home.subtitleRequested.connect(self._toggle_subtitle)
        self.home.translateRequested.connect(self._on_home_translate)
        self.home.extractRequested.connect(self._on_home_extract)
        self.home.polishRequested.connect(self._on_home_polish)
        self.settings.saved.connect(self._on_settings_saved)
        self.doc.translateRequested.connect(self._on_doc_translate)
        self.doc.polishRequested.connect(self._on_doc_polish)
        self.av.translateRequested.connect(self._on_av_translate)
        self.av.polishRequested.connect(self._on_av_polish)
        self._sig_ocr_done.connect(self._on_ocr_done)
        self._sig_ocr_blocks.connect(self._on_overlay_blocks)
        self._sig_trans_done.connect(self._on_trans_done)
        self._sig_doc_trans_done.connect(self._on_doc_trans_done)
        self._sig_doc_sub_trans_done.connect(self._on_doc_sub_trans_done)
        self._sig_doc_sub_progress.connect(self.doc.set_sub_progress)
        self._sig_av_trans_done.connect(self.av.set_translations)
        self._sig_av_trans_progress.connect(self.av.set_trans_progress)
        self._sig_overlay_trans_done.connect(self._on_overlay_trans_done)
        self._sig_polish_home_done.connect(self._on_home_polished)
        self._sig_polish_doc_done.connect(self._on_doc_polished)
        self._sig_polish_doc_sub_done.connect(self._on_doc_sub_polished)
        self._sig_polish_overlay_done.connect(self._on_overlay_polished)
        self._sig_polish_av_src_done.connect(self.av.set_polished_sources)
        self._sig_polish_av_trans_done.connect(self.av.set_polished_translations)
        self._sig_polish_failed.connect(self._on_polish_failed)
        self._sig_status.connect(self._on_status)
        self._sig_error.connect(self._on_error)
        self._sig_caption_segment.connect(self._on_caption_segment)
        self._sig_caption_translation.connect(self._on_caption_translation)
        self._sig_caption_status.connect(self._on_caption_status)
        self._sig_caption_error.connect(self._on_caption_error)
        self._sig_caption_fatal.connect(self._on_caption_fatal)
        self._sig_caption_speed.connect(self._on_caption_speed)
        self._sig_hotkey.connect(self._on_hotkey_action)
        self._sig_update_result.connect(self._on_update_result)
        self._sig_update_found.connect(self._on_update_found)
        self._sig_update_progress.connect(self._on_update_progress)
        self._sig_update_finished.connect(self._on_update_finished)

    def _sync_target_lang(self):
        """从主页语言下拉框同步源语言和目标语言到翻译器。"""
        src_text = self.home.src_lang_combo.currentText()
        src_code = SUPPORTED_SOURCE_LANGS.get(src_text, 'auto')
        self.translator.set_source_lang(src_code)
        combo_text = self.home.lang_combo.currentText()
        lang_code = SUPPORTED_TARGET_LANGS.get(combo_text, "zh-CN")
        self.translator.set_target_lang(lang_code)

    # ── 线程安全的 UI 更新 ──────────────────────────────────

    @Slot(str, str)
    def _on_status(self, text, color):
        self.home.set_status(text)
        self.home.status_dot.setStyleSheet(f"color: {color}; font-size: 14px;")

    @Slot(str)
    def _on_error(self, msg):
        """后台任务失败：更新状态栏，并清除截图覆盖层上的等待提示。"""
        self._on_status(msg, "#e5635f")
        if self._overlay and self._overlay.isVisible():
            self._overlay.show_error(msg)

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
            if not text.strip():
                self._on_status("未识别到文字", "#71717a")
                if self._overlay and self._overlay.isVisible():
                    self._overlay.show_error("未识别到文字")
                return
            self._on_status("识别完成，正在翻译...", "#eab308")
            self._do_translate_overlay(text, self._overlay_blocks)
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
            self._auto_copy(text, "ocr")
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
        """OCR 可用性检查与模型加载全部放后台，避免 import winrt/easyocr 卡 UI。

        启动期还没加载完时设置页改引擎不会并发起第二个线程，而是排队重试。
        """
        if self._ocr_loading:
            self._ocr_reload_pending = True
            return
        self._ocr_loading = True
        wanted = self.cfg.get("ocr_engine", OCR_ENGINE)
        self._on_status("正在初始化 OCR 模型...", "#eab308")

        def init():
            try:
                engine = wanted
                if not check_engine_available(engine):
                    engine = ""
                    for fallback in ("windows", "easy"):
                        if fallback != wanted and check_engine_available(fallback):
                            engine = fallback
                            self._sig_status.emit(
                                "OCR引擎未安装，已切换到备用引擎", "#eab308")
                            break
                    if not engine:
                        self._sig_status.emit(
                            "OCR引擎未安装，请前往设置页安装", "#e5635f")
                        return
                self.ocr = get_ocr_instance(OCR_LANGUAGES, engine=engine)
                self._sig_status.emit("就绪", "#4ade80")
            except Exception as exc:
                self._sig_status.emit(f"OCR 初始化失败：{exc}", "#e5635f")
            finally:
                self._ocr_loading = False
                if self._ocr_reload_pending:
                    self._ocr_reload_pending = False
                    QTimer.singleShot(0, self, self._init_ocr_async)

        threading.Thread(target=init, daemon=True).start()

    # ── 快捷键 ──────────────────────────────────────────────

    def _setup_hotkey(self):
        """按设置页的快捷键配置注册全局热键（默认全部为空 = 不占用按键）。"""
        from hotkeys import HotkeyManager, get_hotkeys
        self._hotkeys = HotkeyManager(
            self._sig_hotkey.emit, on_error=self.settings.show_hotkey_error)
        self._hotkeys.reload(get_hotkeys(self.cfg))

    @Slot(str)
    def _on_hotkey_action(self, action):
        """全局热键的唯一分发入口，始终在主线程执行。"""
        if action == 'screenshot':
            self.home.screenshotRequested.emit()
        elif action == 'fullscreen':
            self.home.fullscreenRequested.emit()
        elif action == 'translate':
            self.home.translateRequested.emit(self.home.ocr_text.toPlainText())
        elif action == 'live_translate':
            self.home.liveTranslateRequested.emit()
        elif action == 'stop_live_translate':
            self._close_live_window()
        elif action == 'subtitle':
            self._show_caption_window()
        elif action == 'toggle_subtitle_overlay':
            self._toggle_caption_overlay()
        elif action == 'stop_subtitle':
            self._stop_caption()
        elif action == 'toggle_subtitle_pause':
            self._toggle_caption_pause()
        elif action == 'cycle_subtitle_mode':
            self._cycle_caption_mode()
        elif action == 'clear_subtitle':
            self._clear_caption()
        elif action == 'show_main':
            self._restore_from_tray()

    # ── 检查更新与一键升级 ───────────────────────────────────

    def _auto_check_update(self):
        """启动时的静默检查：失败不打扰，发现新版只给一条可点击提示。"""
        if self.cfg.get("auto_check_update", True):
            self.check_update(silent=True)

    def check_update(self, silent=False):
        """设置页「检查更新」与启动自检共用；重复点击不会叠加网络请求。"""
        thread = self._update_thread
        if thread is not None and thread.is_alive():
            return False

        def _worker():
            try:
                import updater
                info = updater.fetch_latest_release()
                if self._update_stop.is_set():
                    return
                if not updater.is_newer(info.version, APP_VERSION):
                    if not silent:
                        self._emit_update_result(
                            "success", "已是最新版本",
                            f"当前版本 v{APP_VERSION} 已是最新")
                    return
                self._sig_update_found.emit(info, silent)
            except Exception as exc:
                if not silent:
                    self._emit_update_result(
                        "warning", "检查更新失败", str(exc))

        self._update_thread = threading.Thread(target=_worker, daemon=True)
        self._update_thread.start()
        return True

    def _emit_update_result(self, level, title, message):
        """工作线程只 emit 信号，绝不直接碰 Qt 控件。"""
        if not self._update_stop.is_set():
            self._sig_update_result.emit(level, title, message)

    @Slot(str, str, str)
    def _on_update_result(self, level, title, message):
        from qfluentwidgets import InfoBar, InfoBarPosition
        duration = 6000 if level == "warning" else 3500
        fn = {"warning": InfoBar.warning,
              "success": InfoBar.success}.get(level, InfoBar.info)
        fn(title=title, content=message, duration=duration,
           position=InfoBarPosition.TOP, parent=self.home)

    @Slot(object, bool)
    def _on_update_found(self, info, silent=False):
        """自动检查给可点击提示条；手动检查直接弹升级对话框。"""
        dlg = self._upgrade_dialog
        if dlg is not None and dlg.isVisible():
            return
        self._upgrade_info = info
        if silent:
            from qfluentwidgets import InfoBar, InfoBarPosition
            from PySide6.QtWidgets import QPushButton
            from update_dialog import _btn_style
            bar = InfoBar.warning(
                title=f"发现新版本 v{info.version}",
                content="点击「立即升级」下载安装包并覆盖升级，配置与数据会保留。",
                duration=15000, position=InfoBarPosition.TOP, parent=self.home,
            )
            btn = QPushButton("立即升级", bar)
            btn.setStyleSheet(_btn_style("primary"))
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda: self._on_update_found(info, False))
            bar.addWidget(btn)
            return
        self._prompt_upgrade(info)

    def _prompt_upgrade(self, info):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        from update_dialog import UpdateDialog, UPGRADE, OPEN_PAGE
        import updater
        code = UpdateDialog(info, APP_VERSION, self).exec()
        if code == OPEN_PAGE or (code == UPGRADE and not info.has_installer):
            QDesktopServices.openUrl(QUrl(info.page_url or updater.RELEASES_URL))
        elif code == UPGRADE:
            self._start_upgrade(info)

    def _start_upgrade(self, info):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        from update_dialog import DownloadDialog, UPGRADE
        import updater
        if not info.has_installer:
            QDesktopServices.openUrl(QUrl(info.page_url or updater.RELEASES_URL))
            return

        cancel = threading.Event()
        dialog = DownloadDialog(info, cancel, self)
        self._upgrade_cancel = cancel
        self._upgrade_dialog = dialog

        def _worker():
            try:
                dest = updater.safe_download_path(info)
                # 上次下载过且校验值一致就直接复用，避免重复下载上百 MB
                if dest.is_file() and info.sha256 and \
                        updater.verify_download(dest, info.sha256) is True:
                    self._sig_update_finished.emit(True, "verified", str(dest))
                    return
                updater.download_file(
                    updater.installer_url(info), dest,
                    progress_cb=lambda done, total: (
                        self._sig_update_progress.emit(int(done), int(total))
                        if not self._update_stop.is_set() else None),
                    cancel=cancel,
                    headers=updater.download_headers(),
                )
                if cancel.is_set():
                    raise updater.UpdateCancelled()
                verified = updater.verify_download(dest, info.sha256)
                if verified is False:
                    dest.unlink(missing_ok=True)
                    self._sig_update_finished.emit(
                        False, "安装包 SHA256 校验失败，已取消安装", "")
                    return
                self._sig_update_finished.emit(
                    True, "verified" if verified is True else "no-checksum",
                    str(dest))
            except updater.UpdateCancelled:
                self._sig_update_finished.emit(False, "已取消下载", "")
            except Exception as exc:
                self._sig_update_finished.emit(False, f"下载失败：{exc}", "")

        threading.Thread(target=_worker, daemon=True).start()
        code = dialog.exec()
        self._upgrade_dialog = None
        if code == UPGRADE and dialog.installer_path is not None:
            self._queue_install(dialog.installer_path)
        else:
            cancel.set()  # 取消 / 关闭时让下载线程尽快退出

    @Slot(int, int)
    def _on_update_progress(self, done, total):
        dlg = self._upgrade_dialog
        if dlg is not None and dlg.isVisible():
            dlg.set_progress(done, total)

    @Slot(bool, str, str)
    def _on_update_finished(self, ok, message, path):
        dlg = self._upgrade_dialog
        if dlg is None or not dlg.isVisible():
            return
        if ok:
            dlg.set_done(path, True if message == "verified" else None)
        else:
            dlg.show_error(message)

    def _queue_install(self, path):
        """登记安装包并真正退出；app.py 在事件循环结束后拉起静默安装。"""
        import updater
        updater.queue_install(path)
        self._update_stop.set()
        self._quit_app()

    # ── 系统托盘 ────────────────────────────────────────────

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self)
        # 优先用窗口图标，兜底用应用图标
        icon = self.windowIcon()
        if icon.isNull():
            from PySide6.QtGui import QIcon
            icon = QIcon(str(resource_path("assets/app_icon.png")))
        self._tray.setIcon(icon)
        self.setWindowIcon(icon)
        self._tray.setToolTip(APP_DISPLAY_NAME)
        self._tray.activated.connect(self._on_tray_activated)

        menu = QMenu()
        menu.setStyleSheet("QMenu { background: #1a1a1e; color: #e4e4e7; border: 1px solid #333; }"
                           "QMenu::item:selected { background: #3b82f6; }")
        shot_action = QAction("区域截图", menu)
        shot_action.triggered.connect(self._start_screenshot)
        full_action = QAction("全屏截图", menu)
        full_action.triggered.connect(self._start_fullscreen)
        live_action = QAction("实时翻译", menu)
        live_action.triggered.connect(self._start_live_translate)
        stop_live_action = QAction("停止实时翻译", menu)
        stop_live_action.triggered.connect(self._close_live_window)

        caption_action = QAction("实时字幕 / 显示字幕框", menu)
        caption_action.triggered.connect(self._show_caption_window)
        hide_caption_action = QAction("隐藏字幕框（继续识别）", menu)
        hide_caption_action.triggered.connect(self._toggle_caption_overlay)
        stop_caption_action = QAction("停止实时字幕", menu)
        stop_caption_action.triggered.connect(self._stop_caption)
        pause_caption_action = QAction("暂停 / 继续字幕", menu)
        pause_caption_action.triggered.connect(self._toggle_caption_pause)
        clear_caption_action = QAction("清空当前字幕", menu)
        clear_caption_action.triggered.connect(self._clear_caption)

        # 字幕显示模式：互斥勾选，浮层控制条上的「双语/译文/原文」同源
        mode_menu = menu.addMenu("字幕显示模式")
        mode_group = QActionGroup(mode_menu)
        mode_group.setExclusive(True)
        from config import SUBTITLE_DISPLAY_MODES, get_subtitle_settings
        current_mode = str(get_subtitle_settings().get(
            'subtitle_display_mode') or 'both')
        for label, key in SUBTITLE_DISPLAY_MODES.items():
            act = QAction(label, mode_menu)
            act.setCheckable(True)
            act.setChecked(key == current_mode)
            act.triggered.connect(
                lambda _checked=False, k=key: self._set_caption_mode(k))
            mode_group.addAction(act)
            mode_menu.addAction(act)
        self._caption_mode_actions = {
            a.text(): a for a in mode_group.actions()}

        show_action = QAction("显示主窗口", menu)
        show_action.triggered.connect(self._restore_from_tray)
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(shot_action)
        menu.addAction(full_action)
        menu.addAction(live_action)
        menu.addAction(stop_live_action)
        menu.addSeparator()
        menu.addAction(caption_action)
        menu.addAction(hide_caption_action)
        menu.addAction(pause_caption_action)
        menu.addAction(stop_caption_action)
        menu.addMenu(mode_menu)
        menu.addAction(clear_caption_action)
        menu.addSeparator()
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_from_tray()

    def _minimize_to_tray(self):
        # 托盘不可用时绝不能隐藏窗口：用户既看不到窗口也看不到托盘图标，
        # 只能去任务管理器结束进程。
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.hide()

    def _restore_from_tray(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _quit_app(self):
        self._update_stop.set()  # 后台检查线程不要再向已销毁的窗口发信号
        self._restore_after_live = False
        self._close_live_window()
        self._stop_caption()
        if self._hotkeys is not None:
            self._hotkeys.dispose()
        self._tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self.cfg.get("minimize_to_tray", True):
            event.ignore()
            self._minimize_to_tray()
        else:
            # app.setQuitOnLastWindowClosed(False) 是全局设置，仅 accept 关闭事件
            # 不会结束事件循环：进程会变成无窗口、无托盘的僵尸，升级安装包也会
            # 因为 exe 仍被占用而覆盖失败。必须显式 quit。
            self._quit_app()
            event.accept()

    # ── 截图 ────────────────────────────────────────────────

    @Slot()
    def _start_screenshot(self):
        if self._overlay is not None and self._overlay.isVisible():
            return  # 已在截图中，避免重复触发
        self._close_live_window(restore=True)
        self._on_status("请选择截图区域...", "#eab308")
        # 记录截图前窗口是否可见，结束后按原状态恢复
        # （从托盘热键触发时不弹出主窗口）
        self._restore_after_capture = self.isVisible() and not self.isMinimized()
        self.hide()
        QTimer.singleShot(150, self._do_screenshot)

    def _do_screenshot(self):
        auto_translate = self.cfg.get("auto_translate", False)
        self._overlay = ScreenshotOverlay(auto_translate=auto_translate)
        self._overlay.extractRequested.connect(self._on_overlay_extract)
        self._overlay.translateRequested.connect(self._on_overlay_translate)
        self._overlay.polishRequested.connect(self._on_overlay_polish)
        self._overlay.screenshotTaken.connect(self._on_overlay_screenshot)
        self._overlay.finished.connect(self._on_overlay_finished)
        self._overlay.start_capture()

    def _on_overlay_finished(self):
        overlay = self.sender()
        if overlay is not self._overlay:
            # 旧覆盖层的延迟信号，忽略，避免误清当前覆盖层
            return
        self._overlay = None
        if overlay is not None:
            overlay.deleteLater()
        if self._live_pending:
            # 实时翻译选区被取消（ESC / 右键 / 选区太小）
            self._live_pending = False
            self._restore_main_after_live()
            self._on_status("已取消实时翻译", "#71717a")
            return
        if self._restore_after_capture:
            self.showNormal()
            self.activateWindow()
        self._restore_after_capture = False

    # ── 实时翻译 ────────────────────────────────────────────

    @Slot()
    def _start_live_translate(self):
        if self._live_window is not None:
            self._live_window.raise_()
            self._live_window.activateWindow()
            return
        if self._overlay is not None and self._overlay.isVisible():
            return
        if self.ocr is None:
            self._on_status("OCR 未就绪，请稍候...", "#eab308")
            return
        self._live_src = SUPPORTED_SOURCE_LANGS.get(
            self.home.src_lang_combo.currentText(), 'auto')
        self._live_tgt = SUPPORTED_TARGET_LANGS.get(
            self.home.lang_combo.currentText(), 'zh-CN')
        self._restore_after_live = (
            self.isVisible() and not self.isMinimized())
        self._live_pending = True
        self._on_status("请框选实时翻译区域...", "#eab308")
        self.hide()
        QTimer.singleShot(150, self._do_live_select)

    def _do_live_select(self):
        self._overlay = ScreenshotOverlay(live_select=True)
        self._overlay.regionSelected.connect(self._on_live_region_selected)
        self._overlay.finished.connect(self._on_overlay_finished)
        self._overlay.start_capture()

    @Slot(QRect)
    def _on_live_region_selected(self, rect):
        self._live_pending = False
        if rect.width() < 8 or rect.height() < 8:
            self._restore_main_after_live()
            self._on_status("选区太小，无法实时翻译", "#eab308")
            return
        try:
            from translator import Translator
            from realtime_translate import LiveTranslateWindow
            from config import get_live_settings
            live_cfg = get_live_settings()
            live_translator = Translator(self._live_src, self._live_tgt)
            self._live_window = LiveTranslateWindow(
                rect, self.ocr, live_translator,
                interval_ms=live_cfg.get("live_interval_ms", 1200),
                show_source=live_cfg.get("live_show_source", False))
            self._live_window.closed.connect(self._on_live_closed)
            self._live_window.reselectRequested.connect(
                self._on_live_reselect)
            self._live_window.show()
            self._on_status("实时翻译已启动（拖动边缘缩放 / 拖控制条移动）",
                            "#4ade80")
        except Exception as e:
            self._restore_main_after_live()
            self._on_status(f"实时翻译启动失败: {e}", "#e5635f")

    @Slot()
    def _on_live_closed(self):
        self._live_window = None
        self._on_status("实时翻译已停止", "#71717a")
        self._restore_main_after_live()

    @Slot()
    def _on_live_reselect(self):
        """浮窗点「重选」：关闭当前浮窗并重新进入框选。"""
        if self._live_window is None:
            return
        restore = self._restore_after_live
        self._close_live_window(restore=False)
        self._restore_after_live = restore
        self._live_pending = True
        self._on_status("请重新框选实时翻译区域...", "#eab308")
        self.hide()
        QTimer.singleShot(150, self._do_live_select)

    def _restore_main_after_live(self):
        if self._restore_after_live:
            self._restore_after_live = False
            self.showNormal()
            self.activateWindow()
            self.raise_()

    def _close_live_window(self, _checked=False, restore=False):
        if self._live_window is None:
            return
        if not restore:
            self._restore_after_live = False
        self._live_window.close()
        self._live_window = None

    # ── 实时字幕 ────────────────────────────────────────────

    @Slot()
    def _toggle_subtitle(self, _checked=False):
        """主页按钮：运行中再点一次 = 停止实时字幕。"""
        if self._caption_engine is not None:
            self._stop_caption()
        else:
            self._start_subtitle()

    @Slot()
    def _show_caption_window(self, _checked=False):
        """托盘 / 快捷键：运行中重新显示字幕框，未运行则直接启动。"""
        if self._caption_engine is not None:
            if self._caption_window is not None:
                self._caption_window.show_overlay()
            return
        self._start_subtitle()

    def _toggle_caption_overlay(self, _checked=False):
        """只切换字幕框可见性，绝不影响后台采集识别。"""
        if self._caption_engine is None:
            self._start_subtitle()
            return
        win = self._caption_window
        if win is None:
            return
        if win.isVisible():
            win.hide_overlay()
        else:
            win.show_overlay()

    @Slot()
    def _start_subtitle(self, _checked=False):
        # 启动中 / 停止中：拒绝重复触发，避免双启动竞态
        if self._caption_starting or self._caption_stopping:
            return
        if self._caption_engine is not None:
            self._show_caption_window()
            return
        if self._overlay is not None and self._overlay.isVisible():
            return
        self._caption_starting = True
        try:
            from config import get_subtitle_settings
            from live_caption import dependency_status, list_devices
            cfg = get_subtitle_settings()
            cfg['subtitle_audio_source'] = self.home.subtitle_audio_source()
            # 字幕翻译沿用主页「翻译到」，与实时截图翻译保持一致
            cfg['subtitle_target_lang'] = SUPPORTED_TARGET_LANGS.get(
                self.home.lang_combo.currentText(), 'zh-CN')

            deps = dependency_status()
            source = str(cfg['subtitle_audio_source'] or 'mic')
            self._caption_notice = ''
            if source == 'loopback':
                devices = list_devices('loopback')
                if devices:
                    ids = [d['id'] for d in devices]
                    if cfg.get('subtitle_audio_device') not in ids:
                        cfg['subtitle_audio_device'] = ids[0]
                else:
                    # 内录组件没装且系统也没有「立体声混音」：不拦死用户，
                    # 临时回退到麦克风，并在状态栏明确说明怎么开启内录
                    source = 'mic'
                    cfg['subtitle_audio_source'] = 'mic'
                    cfg['subtitle_audio_device'] = ''
                    self._caption_notice = (
                        "（内录未就绪，已临时用麦克风；"
                        "听电脑声音请到设置 → 实时字幕安装组件）")
            if source == 'mic':
                if not deps['mic']:
                    self._on_status(
                        "缺少 sounddevice，无法采集麦克风（pip install sounddevice）",
                        "#e5635f")
                    return
                # 防止上一轮留下的 loopback 设备 id 被误当成麦克风采集器
                if str(cfg.get('subtitle_audio_device') or '').startswith(
                        'loopback:'):
                    cfg['subtitle_audio_device'] = ''

            engine_kind = str(cfg.get('subtitle_engine') or 'local')
            if engine_kind == 'local':
                if not deps['local_asr']:
                    self._on_status("缺少 faster-whisper，请在「音视频」页安装依赖",
                                    "#e5635f")
                    return
                from speech_recognizer import resolve_model_path
                name = str(cfg.get('subtitle_model') or 'tiny')
                if not resolve_model_path(name)[0]:
                    self._on_status(
                        f"本地模型 {name} 未下载：请到「音视频」页下载，"
                        "或在设置里改用在线 API", "#e5635f")
                    return
            elif not str(cfg.get('subtitle_api_url') or '').strip():
                self._on_status("请在「设置 → 实时字幕」填写识别服务地址",
                                "#e5635f")
                return
        except Exception as e:
            self._caption_starting = False
            self._on_status(f"实时字幕启动检查失败: {e}", "#e5635f")
            return

        self._caption_cfg = cfg
        self._launch_caption()
        # _launch_caption 同步创建引擎（含加载模型），无论成功失败都清起始标志
        self._caption_starting = False

    def _new_caption_window(self, cfg, region=None):
        """构造字幕浮层并集中连接信号（重建时也走这里）。"""
        from config import save_user_config
        from subtitle_overlay import CaptionWindow, resolve_caption_region
        win = CaptionWindow(
            region if region is not None else resolve_caption_region(cfg), cfg)
        win.hidden.connect(self._on_caption_hidden)
        win.stopRequested.connect(self._stop_caption)
        win.closed.connect(self._on_caption_window_closed)
        win.regionChanged.connect(self._on_caption_region_changed)
        win.cleared.connect(self._on_caption_cleared)
        win.pausedChanged.connect(self._on_caption_paused)
        win.modeChanged.connect(self._set_caption_mode)
        win.fontSizeChanged.connect(
            lambda size: save_user_config({'subtitle_font_size': size}))
        win.clickThroughChanged.connect(
            lambda on: save_user_config({'subtitle_click_through': on}))
        return win

    def _launch_caption(self):
        """直接给出默认 / 上次保存的区域，不再让用户框选。"""
        win = None
        engine = None
        try:
            from live_caption import LiveCaptionEngine
            from subtitle_overlay import resolve_caption_region
            cfg = dict(self._caption_cfg)
            region = resolve_caption_region(cfg)
            cfg['subtitle_region'] = [region.x(), region.y(),
                                      region.width(), region.height()]
            win = self._new_caption_window(cfg, region)
            self._caption_window = win
            self._caption_engine = LiveCaptionEngine(cfg, {
                'on_segment': self._sig_caption_segment.emit,
                'on_translation': self._sig_caption_translation.emit,
                'on_status': self._sig_caption_status.emit,
                'on_error': self._sig_caption_error.emit,
                'on_fatal': self._sig_caption_fatal.emit,
                'on_speed': self._sig_caption_speed.emit,
            })
            engine = self._caption_engine
            # 新会话从空白开始；之后原文/译文逐条追加到主页两个文本框
            self.home.clear_caption_text()
            self.home.set_subtitle_running(True)
            win.show_overlay()
            engine.start()
            self._on_status(
                "实时字幕已启动：字幕框可拖动/四角缩放；关掉字幕框也会继续显示"
                "在「识别文本 / 翻译结果」" + self._caption_notice, "#4ade80")
        except Exception as e:
            if engine is not None:
                try:
                    engine.stop()
                except Exception:
                    pass
            if win is not None:
                try:
                    win.blockSignals(True)
                    win.close()
                    win.deleteLater()
                except Exception:
                    pass
            self._caption_window = None
            self._caption_engine = None
            self.home.set_subtitle_running(False)
            self._on_status(f"实时字幕启动失败: {e}", "#e5635f")

    @Slot(int, str)
    def _on_caption_segment(self, seq, text):
        if self._caption_window is not None:
            self._caption_window.append_source(seq, text)
        # 字幕框隐藏后仍继续写主页「识别文本」
        self.home.append_caption_source(text)

    @Slot(int, str)
    def _on_caption_translation(self, seq, text):
        if not bool(self._caption_cfg.get('subtitle_translate', True)):
            return  # 用户没开翻译，就绝不往「翻译结果」里写东西
        if self._caption_window is not None:
            self._caption_window.set_translation(seq, text)
        self.home.append_caption_translation(text)

    @Slot(str, str)
    def _on_caption_status(self, text, color):
        win = self._caption_window
        if win is not None:
            win.set_status(text, color)
        if win is None or not win.isVisible():
            self._on_status(text, color)

    @Slot(str)
    def _on_caption_error(self, msg):
        self._on_status(msg, "#e5635f")
        if self._caption_window is not None:
            self._caption_window.set_status(msg, "#e5635f")

    @Slot(str)
    def _on_caption_fatal(self, msg):
        """识别线程已经死掉：复位全部状态，否则界面一直显示「运行中」。"""
        self._stop_caption()
        self._on_status(msg, "#e5635f")

    @Slot(float)
    def _on_caption_speed(self, seconds):
        if self._caption_window is not None:
            self._caption_window.set_speed(seconds)

    @Slot(bool)
    def _on_caption_paused(self, paused):
        if self._caption_engine is not None:
            self._caption_engine.set_paused(paused)
        self._on_status("实时字幕已暂停" if paused else "实时字幕已继续",
                        "#eab308" if paused else "#4ade80")

    @Slot()
    def _on_caption_hidden(self):
        self._on_status(
            "字幕框已隐藏，识别继续；结果同步显示在「识别文本 / 翻译结果」",
            "#71717a")

    @Slot()
    def _on_caption_cleared(self):
        self.home.clear_caption_text()

    @Slot(QRect)
    def _on_caption_region_changed(self, rect):
        """用户拖动 / 缩放后记住位置，下次启动直接用，不用再选。"""
        from config import save_user_config
        value = [rect.x(), rect.y(), rect.width(), rect.height()]
        self._caption_cfg['subtitle_region'] = value
        save_user_config({'subtitle_region': value})

    @Slot()
    def _on_caption_window_closed(self):
        """窗口被意外销毁时重建一个，识别不中断；正常停止走 _stop_caption。"""
        self._caption_window = None
        if self._caption_engine is not None and self._caption_cfg:
            try:
                win = self._new_caption_window(dict(self._caption_cfg))
                self._caption_window = win
                win.show_overlay()
            except Exception:
                pass

    def _set_caption_mode(self, mode, _checked=False):
        from config import SUBTITLE_DISPLAY_MODES, save_user_config
        mode = str(mode)
        save_user_config({'subtitle_display_mode': mode})
        self.cfg['subtitle_display_mode'] = mode
        if self._caption_cfg:
            self._caption_cfg['subtitle_display_mode'] = mode
        if self._caption_window is not None:
            self._caption_window.set_mode(mode)
        label = next((k for k, v in SUBTITLE_DISPLAY_MODES.items()
                      if v == mode), None)
        action = getattr(self, '_caption_mode_actions', {}).get(label)
        if action is not None:
            action.setChecked(True)

    def _cycle_caption_mode(self):
        from config import SUBTITLE_DISPLAY_MODES
        modes = list(SUBTITLE_DISPLAY_MODES.values())
        cur = str(self.cfg.get('subtitle_display_mode') or 'both')
        idx = modes.index(cur) if cur in modes else 0
        self._set_caption_mode(modes[(idx + 1) % len(modes)])

    def _toggle_caption_pause(self, _checked=False):
        win = self._caption_window
        if win is None:
            return
        win.set_paused(not win.is_paused())

    def _clear_caption(self, _checked=False):
        if self._caption_window is not None:
            self._caption_window.clear_lines()  # cleared -> 同步清主页
        else:
            self.home.clear_caption_text()

    @Slot()
    def _stop_caption(self, _checked=False):
        """唯一真正停止采集 / 识别的入口（主页按钮、控制条、托盘、快捷键、退出）。"""
        if self._caption_stopping:
            return
        engine = self._caption_engine
        # 先置空：避免窗口 close 的 closed 信号被误判成「意外销毁」而重建
        self._caption_engine = None
        self._caption_stopping = True
        win = self._caption_window
        self._caption_window = None
        if win is not None:
            try:
                win.blockSignals(True)
                win.close()
                win.deleteLater()
            except Exception:
                pass
        self._caption_cfg = {}
        self.home.set_subtitle_running(False)
        self._on_status("实时字幕已停止", "#71717a")
        # engine.stop() 会 join 音频 / ASR / 翻译三个线程（ASR 可能正卡在 Whisper
        # 推理，单句在普通 CPU 上也常 >3s）。在主线程同步 join 会冻结界面数秒，
        # 放到后台线程执行，保证点「停止」时界面立刻响应；结束后才开放启动，
        # 防止新引擎与尚未退出的旧线程争用同一音频设备 / 模型。
        if engine is not None:
            def _do_stop():
                try:
                    engine.stop()
                finally:
                    self._caption_stopping = False
            threading.Thread(target=_do_stop, daemon=True).start()
        else:
            self._caption_stopping = False

    def _on_overlay_screenshot(self, path):
        self.home.set_image(path)

    @Slot(str)
    def _on_overlay_extract(self, path):
        self._do_ocr_for_overlay(path, "overlay_extract")

    @Slot(str)
    def _on_overlay_translate(self, path):
        self._do_ocr_for_overlay(path, "overlay_translate")

    @Slot(object)
    def _on_overlay_blocks(self, blocks):
        self._overlay_blocks = blocks or []

    def _do_ocr_for_overlay(self, image_path, target):
        if self.ocr is None:
            self._on_status("OCR 未就绪，请稍候...", "#eab308")
            return
        self._on_status("正在识别文字...", "#eab308")

        def run():
            # 翻译路径一次 OCR 同时拿到文本和行位置；其余路径仍用纯文本接口
            groups = []
            text = ""
            used_blocks = False
            if (target == "overlay_translate"
                    and hasattr(self.ocr, "recognize_blocks")):
                try:
                    blocks = self.ocr.recognize_blocks(image_path) or []
                    if blocks:
                        from PySide6.QtGui import QImage
                        from text_segment import (
                            blocks_to_text, group_blocks, normalize_groups)
                        img = QImage(image_path)
                        groups = normalize_groups(
                            group_blocks(blocks), img.width(), img.height())
                        text = blocks_to_text(blocks)
                        used_blocks = True
                except Exception:
                    groups = []
            if not used_blocks:
                try:
                    text = self.ocr.recognize(image_path) or ""
                except Exception as e:
                    self._sig_ocr_blocks.emit([])
                    self._sig_error.emit(str(e))
                    return
            self._sig_ocr_blocks.emit(groups)
            self._sig_ocr_done.emit(text, target)

        threading.Thread(target=run, daemon=True).start()

    def _do_translate_overlay(self, text, blocks=None):
        blocks = list(blocks or [])
        # 缓存逐段列表而不是用 "\n\n" 拆字符串：整段译文里若含空行，
        # 拆出的条数会和坐标块对不上，导致白白回退到整段显示。
        if (text == self._last_trans_text and self._last_trans_paras
                and (not blocks
                     or len(self._last_trans_paras) == len(blocks))):
            self._sig_overlay_trans_done.emit(list(self._last_trans_paras), 0)
            return
        self._on_status("正在翻译...", "#eab308")
        self._sync_target_lang()

        def run():
            try:
                if blocks:
                    # 一组 = 一个原文位置，逐组翻译保证与坐标一一对应
                    texts = [str(b.get("text", "")) for b in blocks]
                else:
                    from text_segment import split_paragraphs
                    texts = split_paragraphs(text)
                    if not texts:
                        texts = [text.strip()] if text.strip() else []
                results = []
                failed = 0
                for para in texts:
                    try:
                        tr = self.translator.translate_paragraph(para)
                        results.append(tr)
                    except Exception:
                        failed += 1
                        results.append(para)
                if texts and failed == len(texts):
                    raise TranslationError(
                        "全部段落翻译失败，请检查网络或更换翻译API")
                self._last_trans_text = text
                self._last_trans_result = "\n\n".join(results)
                self._last_trans_paras = list(results)
                self._sig_overlay_trans_done.emit(results, failed)
            except TranslationError as e:
                self._sig_error.emit(str(e))
            except Exception as e:
                self._sig_error.emit(f"翻译失败: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _apply_overlay_result(self, paragraphs, ocr_text=""):
        """译文优先按 OCR 块的原位置贴回；无坐标或条数不符时回退整段显示。"""
        if not self._overlay:
            return
        blocks = self._overlay_blocks
        if blocks and len(blocks) == len(paragraphs):
            positioned = [dict(b, trans=str(p))
                          for b, p in zip(blocks, paragraphs)]
            self._overlay.show_trans_blocks(positioned, ocr_text)
        else:
            self._overlay.show_trans_paragraphs(paragraphs, ocr_text)

    @Slot(list, int)
    def _on_overlay_trans_done(self, paragraphs, failed=0):
        if self._overlay and self._overlay.isVisible():
            self._apply_overlay_result(paragraphs, self._overlay_ocr_text)
        full_text = "\n\n".join(paragraphs)
        self.home.set_trans_text(full_text)
        if paragraphs and failed == 0:
            self._on_status("翻译完成", "#4ade80")
        elif paragraphs and failed:
            self._on_status(f"翻译完成（{failed} 个段落失败，已保留原文）", "#eab308")
        else:
            self._on_status("翻译结果为空，请检查网络或更换翻译API", "#eab308")
        self._auto_copy(full_text, "trans")

    # ── 全屏截图 ────────────────────────────────────────────

    @Slot()
    def _start_fullscreen(self):
        if self._overlay is not None and self._overlay.isVisible():
            return
        self._close_live_window(restore=True)
        self._on_status("正在全屏截图...", "#eab308")
        # 先隐藏主窗口再抓屏，避免把本程序窗口截进去
        self._restore_after_capture = self.isVisible() and not self.isMinimized()
        self.hide()
        QTimer.singleShot(150, self._do_fullscreen)

    def _do_fullscreen(self):
        path = FullscreenCapture.capture()
        if self._restore_after_capture:
            self.showNormal()
            self.activateWindow()
        self._restore_after_capture = False
        if not path:
            self._on_status("截图失败", "#e5635f")
            return
        self.home.set_image(path)
        self._do_ocr_home(path)

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
                self._sig_error.emit(str(e))

        threading.Thread(target=run, daemon=True).start()

    def _do_translate_home(self, text):
        if text == self._last_trans_text and self._last_trans_result:
            self._sig_trans_done.emit(self._last_trans_result)
            return
        self._on_status("正在翻译...", "#eab308")
        self._sync_target_lang()

        def run():
            try:
                result = self.translator.translate_document(text)
                self._last_trans_text = text
                self._last_trans_result = result
                self._sig_trans_done.emit(result)
            except TranslationError as e:
                self._sig_error.emit(str(e))
            except Exception as e:
                self._sig_error.emit(f"翻译失败: {e}")

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

    @Slot(str)
    def _on_doc_translate(self, text):
        src_text = self.doc.src_lang_combo.currentText()
        src_code = SUPPORTED_SOURCE_LANGS.get(src_text, 'auto')
        self.translator.set_source_lang(src_code)
        combo_text = self.doc.lang_combo.currentText()
        lang_code = SUPPORTED_TARGET_LANGS.get(combo_text, "zh-CN")
        self.translator.set_target_lang(lang_code)

        entries = getattr(self.doc, "subtitle_entries", None)
        if entries:
            self._translate_subtitle_entries(entries)
            return

        def run():
            try:
                result = self.translator.translate_document(text)
                self._sig_doc_trans_done.emit(result)
            except TranslationError as e:
                self._sig_error.emit(str(e))
            except Exception as e:
                self._sig_error.emit(f"翻译失败: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _translate_subtitle_entries(self, entries):
        """字幕逐条翻译：保留时间轴，失败条目保留原文。"""
        def run():
            total = len(entries)
            translated = []
            failed = 0
            last_error = ""
            for i, e in enumerate(entries, 1):
                line = e.get('text', '')
                out = line
                if line.strip():
                    try:
                        out = self.translator.translate(line)
                    except Exception as ex:
                        failed += 1
                        last_error = str(ex)
                    out = out if out is not None else line
                translated.append({
                    'start': e['start'], 'end': e['end'], 'text': out,
                })
                if i % 5 == 0 or i == total:
                    self._sig_doc_sub_progress.emit(i, total)
            if total and failed == total:
                self._sig_error.emit(
                    last_error or "字幕翻译失败，请检查网络或翻译API")
                return
            if failed:
                self._sig_status.emit(
                    f"字幕翻译完成（{failed}/{total} 条失败，已保留原文）",
                    "#eab308")
            self._sig_doc_sub_trans_done.emit(translated)

        threading.Thread(target=run, daemon=True).start()

    @Slot(list)
    def _on_doc_sub_trans_done(self, entries):
        self.doc.set_translated_subtitle(entries)
        self._auto_copy(self.doc.trans_text.toPlainText(), "trans")

    @Slot(list)
    def _on_av_translate(self, texts):
        """音视频字幕逐条翻译（保留条目顺序）。"""
        src_text = self.av.src_lang_combo.currentText()
        src_code = SUPPORTED_SOURCE_LANGS.get(src_text, 'auto')
        self.translator.set_source_lang(src_code)
        combo_text = self.av.lang_combo.currentText()
        lang_code = SUPPORTED_TARGET_LANGS.get(combo_text, "zh-CN")
        self.translator.set_target_lang(lang_code)

        def run():
            total = len(texts)
            results = []
            failed = 0
            last_error = ""
            for i, text in enumerate(texts, 1):
                out = text
                try:
                    out = self.translator.translate(text)
                except Exception as e:
                    failed += 1
                    last_error = str(e)
                    out = text
                results.append(out if out is not None else text)
                if i % 5 == 0 or i == total:
                    self._sig_av_trans_progress.emit(i, total)
            if total and failed == total:
                self._sig_av_trans_done.emit([])
                self._sig_error.emit(
                    last_error or "字幕翻译失败，请检查网络或翻译API")
                return
            if failed:
                self._sig_status.emit(
                    f"翻译完成（{failed}/{total} 条失败，已保留原文）", "#eab308")
            self._sig_av_trans_done.emit(results)

        threading.Thread(target=run, daemon=True).start()

    @Slot(str)
    def _on_doc_trans_done(self, text):
        self.doc.set_translated_text(text)
        self._auto_copy(text, "trans")

    # ── AI 润色 ────────────────────────────────────────────

    @Slot(str, str)
    def _on_home_polish(self, kind, text):
        self._start_polish_text(text, self._sig_polish_home_done)

    @Slot(str, str)
    def _on_doc_polish(self, kind, text):
        entries = getattr(self.doc, "translated_subtitle_entries", None)
        if self.doc.subtitle_entries is not None and entries:
            lines = [e.get('text', '') for e in entries]
            self._start_polish_lines(lines, self._sig_polish_doc_sub_done)
        else:
            self._start_polish_text(text, self._sig_polish_doc_done)

    @Slot(str, str)
    def _on_overlay_polish(self, kind, text):
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return
        self._start_polish_lines(paragraphs, self._sig_polish_overlay_done)

    @Slot(str, list)
    def _on_av_polish(self, kind, lines):
        if kind == "source":
            self._start_polish_lines(lines, self._sig_polish_av_src_done)
        else:
            self._start_polish_lines(lines, self._sig_polish_av_trans_done)

    def _start_polish_text(self, text, done_signal):
        if not text or not text.strip():
            return
        self._on_status("AI 润色中…", "#eab308")

        def run():
            try:
                from ai_polish import polish_text
                from config import get_ai_polish_settings
                result = polish_text(
                    text, settings=get_ai_polish_settings())
                done_signal.emit(result)
            except Exception as e:
                self._sig_polish_failed.emit()
                self._sig_error.emit(f"AI 润色失败：{e}")

        threading.Thread(target=run, daemon=True).start()

    def _start_polish_lines(self, lines, done_signal):
        if not lines:
            return
        self._on_status("AI 润色中…", "#eab308")

        def run():
            try:
                from ai_polish import polish_lines
                from config import get_ai_polish_settings
                result = polish_lines(
                    lines, settings=get_ai_polish_settings(),
                    progress_cb=lambda d, t: self._sig_status.emit(
                        f"AI 润色中 ({d}/{t})…", "#eab308"))
                done_signal.emit(result)
            except Exception as e:
                self._sig_polish_failed.emit()
                self._sig_error.emit(f"AI 润色失败：{e}")

        threading.Thread(target=run, daemon=True).start()

    @Slot()
    def _on_polish_failed(self):
        self.home.finish_polish()
        self.doc.finish_polish()
        self.av.finish_polish()

    @Slot(str)
    def _on_home_polished(self, text):
        self.home.set_polished_text(text)
        self._last_trans_result = text
        self._on_status("AI 润色完成", "#4ade80")

    @Slot(str)
    def _on_doc_polished(self, text):
        self.doc.set_polished_text(text)
        self._on_status("AI 润色完成", "#4ade80")

    @Slot(list)
    def _on_doc_sub_polished(self, lines):
        entries = getattr(self.doc, "translated_subtitle_entries", None) or []
        if len(lines) != len(entries):
            self.doc.finish_polish()
            self._on_status("润色结果条数不一致，已保留原译文", "#e5635f")
            return
        polished = [{
            'start': e['start'], 'end': e['end'], 'text': text,
        } for e, text in zip(entries, lines)]
        self.doc.set_translated_subtitle(polished)
        self.doc.set_status(
            f"字幕润色完成（{len(polished)} 条，已保留时间轴）", "#4ade80")
        self._on_status("AI 润色完成", "#4ade80")

    @Slot(list)
    def _on_overlay_polished(self, paragraphs):
        if self._overlay and self._overlay.isVisible():
            self._apply_overlay_result(paragraphs, self._overlay_ocr_text)
        full_text = "\n\n".join(paragraphs)
        self.home.set_trans_text(full_text)
        self._last_trans_result = full_text
        self._on_status("AI 润色完成", "#4ade80")

    @Slot()
    def _on_settings_saved(self):
        from hotkeys import get_hotkeys
        old_hotkeys = get_hotkeys(self.cfg)
        old_engine = self.cfg.get("ocr_engine", OCR_ENGINE)
        self.cfg = load_user_config()
        # 设置页改了字幕音源 / 显示模式后，同步主页快捷入口与正在运行的浮层
        # 注意：回退值必须与 DEFAULT_SUBTITLE_SETTINGS 的默认值('mic')一致，
        # 否则用户从未设过该项时，主页下拉框会被错误地同步成 'loopback'
        self.home.set_subtitle_audio_source(
            self.cfg.get('subtitle_audio_source', 'mic'))
        if self._caption_window is not None:
            self._caption_window.set_mode(
                self.cfg.get('subtitle_display_mode', 'both'))
        # 配置写盘后全局快照已刷新，重建翻译引擎让新 API / 密钥立即生效
        from config import DEFAULT_TRANSLATION_API
        self.translator.set_api_type(
            self.cfg.get("translation_api", DEFAULT_TRANSLATION_API))
        new_engine = self.cfg.get("ocr_engine", OCR_ENGINE)
        new_hotkeys = get_hotkeys(self.cfg)
        if old_hotkeys != new_hotkeys and self._hotkeys is not None:
            # 只重建本应用注册过的钩子，不碰其它模块的键盘监听
            self._hotkeys.reload(new_hotkeys)
        if old_engine != new_engine:
            self._init_ocr_async()
        # 实时字幕运行中改了相关设置：引擎用的是启动时的配置快照，需停止后
        # 重新开启才生效，给出明确提示，避免用户以为改了没反应
        if self._caption_engine is not None:
            self._on_status(
                "实时字幕设置已保存，部分项需停止后重新开启生效", "#eab308")
