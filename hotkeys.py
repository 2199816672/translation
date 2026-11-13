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

# 应用内（窗口级）快捷键：只在程序窗口有焦点时生效，不劫持系统按键，
# 最小化到托盘也不会触发。与全局快捷键共用录入控件和冲突检测，但由
# QShortcut 注册，不交给 HotkeyManager（否则会全局拦截，影响别的软件）。
LOCAL_HOTKEY_ACTIONS = OrderedDict([
    ('translate_home', (
        '翻译（主页 · 窗口内）', 'hotkey_local_translate',
        '在「翻译」页直接把识别文本送去翻译；仅本程序窗口内生效')),
])

# 程序里已经被固定占用的窗口级组合。用户把自定义快捷键设成这些会与
# 搜索 / 全选 / 复制等基础操作互相打架，保存时必须提示（但不阻止保存）。
RESERVED_LOCAL_HOTKEYS = {
    'ctrl+f': '设置页搜索',
    'ctrl+a': '全选文本',
    'ctrl+c': '复制文本',
    'ctrl+v': '粘贴文本',
    'ctrl+x': '剪切文本',
    'ctrl+z': '撤销',
    'ctrl+s': '保存',
    'ctrl+w': '关闭窗口',
    'ctrl+o': '打开文件',
    'ctrl+p': '打印',
}

# 旧版本把截图快捷键硬编码成这个组合并写进了用户配置；
# 新版本默认全空，启动时做一次性迁移（带标记，用户之后仍可主动设回它）。
LEGACY_DEFAULT_HOTKEY = 'ctrl+shift+a'
HOTKEYS_MIGRATION_KEY = 'hotkeys_v2_migrated'

# 全局默认全部为空：不设置就绝不占用系统按键，也不会误触发
DEFAULT_HOTKEYS = {
    action: '' for action, (_, _, _) in HOTKEY_ACTIONS.items()
}

# 窗口内快捷键不占用系统按键，因此可以给一个开箱即用的默认值；
# 用户清空输入框 = 主动禁用，配置里显式写 '' 时不会被默认值覆盖。
DEFAULT_LOCAL_HOTKEYS = {
    'translate_home': 'ctrl+enter',
}

_ACTION_BY_CONFIG_KEY = {
    cfg_key: action
    for action, (_, cfg_key, _) in list(HOTKEY_ACTIONS.items())
    + list(LOCAL_HOTKEY_ACTIONS.items())
}

_ALL_LABELS = {
    action: info[0]
    for action, info in list(HOTKEY_ACTIONS.items())
    + list(LOCAL_HOTKEY_ACTIONS.items())
}


def config_key(action):
    """返回动作对应的配置键名（全局与窗口内通用）。"""
    info = HOTKEY_ACTIONS.get(action) or LOCAL_HOTKEY_ACTIONS.get(action)
    return info[1] if info else ''


def action_of_config_key(cfg_key):
    return _ACTION_BY_CONFIG_KEY.get(cfg_key)


def get_hotkeys(cfg=None):
    """读取所有【全局】快捷键（缺失 / 空白一律视为空 = 未启用）。

    窗口内快捷键刻意不在这里返回：HotkeyManager 会把它注册成系统级钩子，
    那样用户在别的软件里按同一个组合也会触发本程序。
    """
    cfg = cfg if cfg is not None else load_user_config()
    return {
        action: str(cfg.get(cfg_key) or '').strip().lower()
        for action, (_, cfg_key, _) in HOTKEY_ACTIONS.items()
    }


def get_local_hotkeys(cfg=None):
    """读取【窗口内】快捷键：配置里没这个键才用默认值，显式 '' 表示禁用。"""
    cfg = cfg if cfg is not None else load_user_config()
    result = {}
    for action, (_, cfg_key, _) in LOCAL_HOTKEY_ACTIONS.items():
        if cfg_key in cfg:
            result[action] = str(cfg.get(cfg_key) or '').strip().lower()
        else:
            result[action] = DEFAULT_LOCAL_HOTKEYS.get(action, '')
    return result


def all_hotkeys(cfg=None):
    """全局 + 窗口内合并，供冲突检测和保存校验使用。"""
    cfg = cfg if cfg is not None else load_user_config()
    merged = get_hotkeys(cfg)
    merged.update(get_local_hotkeys(cfg))
    return merged


def default_hotkeys():
    """恢复默认时使用的合并默认值（全局空 + 窗口内默认组合）。"""
    merged = dict(DEFAULT_HOTKEYS)
    merged.update(DEFAULT_LOCAL_HOTKEYS)
    return merged


def normalize(combo):
    """规范化组合字符串：ctrl + shift + a -> ctrl+shift+a。"""
    parts = [p.strip().lower() for p in str(combo or '').split('+')]
    return '+'.join(p for p in parts if p)


def find_conflicts(hotkeys):
    """返回 [(组合, [动作显示名...])]，只列出被两个以上动作占用的组合。

    传入 :func:`all_hotkeys` 的结果时，全局快捷键与窗口内快捷键之间的冲突
    也能查出来（同一个人按一次键会触发两个动作）。
    """
    used = {}
    for action, combo in (hotkeys or {}).items():
        combo = normalize(combo)
        if not combo:
            continue
        used.setdefault(combo, []).append(_ALL_LABELS.get(action, action))
    return [(combo, labels) for combo, labels in used.items()
            if len(labels) > 1]


def reserved_conflicts(hotkeys):
    """返回 [(组合, 已占用用途, [动作显示名...])]，只查程序内固定组合。"""
    used = {}
    for action, combo in (hotkeys or {}).items():
        combo = normalize(combo)
        if combo in RESERVED_LOCAL_HOTKEYS:
            used.setdefault(combo, []).append(_ALL_LABELS.get(action, action))
    return [(combo, RESERVED_LOCAL_HOTKEYS[combo], labels)
            for combo, labels in used.items()]


def display_hotkey(combo):
    """把配置值转成界面文案：ctrl+enter -> Ctrl + Enter。"""
    parts = [p for p in normalize(combo).split('+') if p]
    names = {'ctrl': 'Ctrl', 'shift': 'Shift', 'alt': 'Alt', 'win': 'Win',
             'enter': 'Enter', 'return': 'Enter', 'esc': 'Esc',
             'space': 'Space', 'tab': 'Tab', 'delete': 'Delete',
             'backspace': 'Backspace', 'up': '↑', 'down': '↓',
             'left': '←', 'right': '→'}
    return ' + '.join(names.get(p, p.upper()) for p in parts)


def qt_key_sequence_text(combo):
    """把配置值转成 QKeySequence 认识的字符串。

    录入控件把主回车记成 ``enter``，而 Qt 的 ``Enter`` 专指小键盘回车、
    主回车叫 ``Return``。不映射的话默认的 Ctrl+Enter 在普通键盘上按不动。
    """
    # Qt 把 Windows 键叫 Meta，录入控件记的是 win；esc / del 之类也统一成
    # QKeySequence 认识的写法，避免用户设完发现快捷键静默失效。
    aliases = {'enter': 'return', 'win': 'meta', 'esc': 'escape',
               'del': 'delete'}
    parts = [aliases.get(p, p)
             for p in normalize(combo).split('+') if p]
    return '+'.join(parts)


def _offscreen():
    """离屏渲染（自动测试）时不真正劫持系统键盘。"""
    return os.environ.get('QT_QPA_PLATFORM', '').lower() == 'offscreen'


class HotkeyManager:
    """管理全局热键的注册 / 反注册。

    `emit` 是形如 emit(action) 的可调用对象（通常是 Qt Signal.emit）。
    `on_error(msg)` 可选，用于把注册失败反馈到界面。
    """

    def __init__(self, emit, on_error=None, backend=None, on_warning=None):
        self._emit = emit
        self._on_error = on_error
        self._on_warning = on_warning    # 配置冲突等非故障提示
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
            # 这是「本程序配置里两个动作撞车」，不是「热键被其它程序占用」。
            # 走警告通道，别再弹红色的「快捷键注册失败」误导用户。
            msg = (f"快捷键 {combo.upper()} 被「{'、'.join(labels)}」同时占用，"
                   "只有第一个会生效，请在设置里改成不同组合")
            self._last_error = msg
            if self._on_warning:
                try:
                    self._on_warning(msg)
                except Exception:
                    pass
            else:
                self._report(msg)

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
