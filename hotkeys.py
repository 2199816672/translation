# -*- coding: utf-8 -*-
"""全局快捷键：动作清单、配置读取与注册。

本模块不依赖 Qt 控件，便于离屏测试。真正的按键回调统一通过外部传入的
`emit(action)` 发出 Qt 信号，由主窗口在主线程里分发，避免后台线程碰 UI。

默认所有快捷键为空 —— 用户不设置就绝不会占用系统按键。
"""
import os
from collections import OrderedDict

from config import load_user_config

# action -> (显示名称, 配置键, 说明)
# 'hotkey' 是历史遗留的截图快捷键键名，保留以兼容老用户的配置。
HOTKEY_ACTIONS = OrderedDict([
    ('screenshot', (
        '区域截图', 'hotkey', '框选屏幕区域并识别文字')),
    ('fullscreen', (
        '全屏截图', 'hotkey_fullscreen', '截取整个屏幕并识别文字')),
    ('translate', (
        '翻译识别文本', 'hotkey_translate', '把主页「识别文本」送去翻译')),
    ('live_translate', (
        '实时翻译', 'hotkey_live_translate', '框选区域并持续识别翻译')),
    ('stop_live_translate', (
        '停止实时翻译', 'hotkey_stop_live', '关闭实时翻译浮层')),
    ('subtitle', (
        '实时字幕', 'hotkey_subtitle', '启动实时字幕；运行中则重新显示字幕框')),
    ('toggle_subtitle_overlay', (
        '显示/隐藏字幕框', 'hotkey_toggle_overlay',
        '隐藏字幕框后识别仍继续，结果写到主页')),
    ('stop_subtitle', (
        '停止实时字幕', 'hotkey_stop_subtitle', '彻底停止采集与识别')),
    ('toggle_subtitle_pause', (
        '暂停/继续字幕', 'hotkey_pause_subtitle', '暂停时不采集声音，恢复后继续')),
    ('cycle_subtitle_mode', (
        '切换双语/译文/原文', 'hotkey_cycle_mode', '切换字幕显示模式')),
    ('clear_subtitle', (
        '清空当前字幕', 'hotkey_clear_subtitle', '清空浮层与主页的字幕文本')),
    ('show_main', (
        '显示主窗口', 'hotkey_show_main', '从托盘唤出主窗口')),
])

# 旧版本把截图快捷键硬编码成这个组合并写进了用户配置；
# 新版本默认全空，启动时做一次性迁移（带标记，用户之后仍可主动设回它）。
LEGACY_DEFAULT_HOTKEY = 'ctrl+shift+a'
HOTKEYS_MIGRATION_KEY = 'hotkeys_v2_migrated'

# 默认全部为空：不设置就绝不占用系统按键，也不会误触发
DEFAULT_HOTKEYS = {
    action: '' for action, (_, _, _) in HOTKEY_ACTIONS.items()
}

_ACTION_BY_CONFIG_KEY = {
    cfg_key: action for action, (_, cfg_key, _) in HOTKEY_ACTIONS.items()
}


def config_key(action):
    """返回动作对应的配置键名。"""
    info = HOTKEY_ACTIONS.get(action)
    return info[1] if info else ''


def action_of_config_key(cfg_key):
    return _ACTION_BY_CONFIG_KEY.get(cfg_key)


def get_hotkeys(cfg=None):
    """读取所有快捷键（缺失 / 空白一律视为空 = 未启用）。"""
    cfg = cfg if cfg is not None else load_user_config()
    return {
        action: str(cfg.get(cfg_key) or '').strip().lower()
        for action, (_, cfg_key, _) in HOTKEY_ACTIONS.items()
    }


def normalize(combo):
    """规范化组合字符串：ctrl + shift + a -> ctrl+shift+a。"""
    parts = [p.strip().lower() for p in str(combo or '').split('+')]
    return '+'.join(p for p in parts if p)


def find_conflicts(hotkeys):
    """返回 [(组合, [动作显示名...])]，只列出被两个以上动作占用的组合。"""
    used = {}
    for action, combo in (hotkeys or {}).items():
        combo = normalize(combo)
        if not combo:
            continue
        label = HOTKEY_ACTIONS.get(action, (action,))[0]
        used.setdefault(combo, []).append(label)
    return [(combo, labels) for combo, labels in used.items()
            if len(labels) > 1]


def _offscreen():
    """离屏渲染（自动测试）时不真正劫持系统键盘。"""
    return os.environ.get('QT_QPA_PLATFORM', '').lower() == 'offscreen'


class HotkeyManager:
    """管理全局热键的注册 / 反注册。

    `emit` 是形如 emit(action) 的可调用对象（通常是 Qt Signal.emit）。
    `on_error(msg)` 可选，用于把注册失败反馈到界面。
    """

    def __init__(self, emit, on_error=None, backend=None):
        self._emit = emit
        self._on_error = on_error
        self._backend = backend          # 测试可注入假的 keyboard 模块
        self._handles = []
        self._active = {}                 # action -> combo
        self._last_conflicts = []
        self._last_error = ''

    @property
    def active(self):
        return dict(self._active)

    @property
    def conflicts(self):
        return list(self._last_conflicts)

    @property
    def last_error(self):
        return self._last_error

    def _keyboard(self):
        if self._backend is not None:
            return self._backend
        import keyboard
        return keyboard

    def _report(self, msg):
        self._last_error = str(msg)
        if self._on_error:
            try:
                self._on_error(str(msg))
            except Exception:
                pass

    def unregister(self):
        """只移除自己注册的钩子，不影响其它模块。"""
        kb = None
        for handle in self._handles:
            try:
                if handle is None:
                    continue
                if kb is None:
                    kb = self._keyboard()
                kb.remove_hotkey(handle)
            except Exception:
                pass
        self._handles = []
        self._active = {}

    def reload(self, hotkeys):
        """按当前配置重建全部热键，返回注册成功的数量。

        冲突的组合只注册第一个动作，并通过 on_error 提示用户更换。
        离屏测试环境下只做逻辑校验，不真正安装系统钩子。
        """
        self.unregister()
        keys = get_hotkeys() if hotkeys is None else dict(hotkeys or {})
        keys = {a: normalize(v) for a, v in keys.items()
                if a in HOTKEY_ACTIONS}
        self._last_conflicts = find_conflicts(keys)
        self._last_error = ''

        for combo, labels in self._last_conflicts:
            self._report(
                f"快捷键 {combo.upper()} 被「{'、'.join(labels)}」同时占用，"
                "只有第一个会生效，请在设置里改成不同组合")

        claimed = set()
        pending = []
        for action, combo in keys.items():
            if not combo or combo in claimed:
                continue
            claimed.add(combo)
            pending.append((action, combo))

        if not pending:
            return 0
        if _offscreen() and self._backend is None:
            # 标记成「已按配置生效」，但不碰系统键盘
            self._active.update(pending)
            return len(pending)

        try:
            kb = self._keyboard()
        except Exception as e:
            self._report(f"未安装全局热键组件（keyboard）：{e}")
            return 0

        count = 0
        for action, combo in pending:
            try:
                handle = kb.add_hotkey(
                    combo, lambda a=action: self._emit and self._emit(a),
                    suppress=False)
                self._handles.append(handle)
                self._active[action] = combo
                count += 1
            except Exception as e:
                label = HOTKEY_ACTIONS[action][0]
                self._report(f"「{label}」的 {combo.upper()} 注册失败：{e}")
        return count

    def dispose(self):
        self.unregister()
        self._emit = None
