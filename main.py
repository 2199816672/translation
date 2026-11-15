"""
翻译工具 - 主程序
功能：截图 / 实时翻译 / 实时字幕 -> OCR 或语音识别 -> 翻译

除正常 GUI 启动外，还提供一个内部使用的「pip worker」模式：
    TranslationTool.exe --tt-pip-worker install <包...>
安装版没有独立的 python.exe 可用来跑 ``-m pip``，因此由 subprocess_util
构造这样的命令，用同一个 exe（同一套 Python ABI）执行 pip，把可选组件装到
用户数据目录 user_libs。该分支不加载 Qt，也不会弹出第二个窗口。
"""
import io
import os
import sys

PIP_WORKER_FLAG = "--tt-pip-worker"


def _install_third_party_notice_filter():
    """精确静默 qfluentwidgets 在 import 时无条件打印的 Pro 广告。

    这条 print 写在库自身的 config.py 模块顶层，没有开关；它既不是错误，
    也与功能无关，每次启动都占一行控制台。只过滤这一行，其它输出
    （含真正的报错、Warning、程序日志）全部原样转发。
    设环境变量 TT_SHOW_LIB_NOTICES=1 可关闭过滤。
    """
    if os.environ.get("TT_SHOW_LIB_NOTICES") == "1":
        return
    inner = getattr(sys, "stdout", None)
    if inner is None:
        return

    class _NoticeFilter:
        def __init__(self, stream):
            self._stream = stream
            self._skip_blank = False

        def write(self, data):
            # print(ALERT) 会写两次：正文 + 单独的 "\n"。正文被吞掉后，
            # 紧随其后的那次纯空白写入也要吞掉，否则控制台留一个空行。
            if self._skip_blank:
                self._skip_blank = False
                if not data.strip():
                    return len(data)
            if "QFluentWidgets Pro is now released" in data:
                self._skip_blank = True
                return len(data)
            return self._stream.write(data)

        def flush(self):
            return self._stream.flush()

        def __getattr__(self, name):
            return getattr(self._stream, name)

    sys.stdout = _NoticeFilter(inner)


def _prepare_worker_stdio():
    """--windowed 打包的程序没有控制台，sys.stdout/stderr 可能是 None。

    调用方已经把 stdout/stderr 重定向到管道，fd 1/2 是有效的，重新包一层
    文本流即可让 pip 的实时输出回传给设置页的进度条。
    """
    for name, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, name, None) is not None:
            continue
        try:
            raw = io.FileIO(fd, "wb", closefd=False)
            stream = io.TextIOWrapper(raw, encoding="utf-8", errors="replace",
                                      line_buffering=True, write_through=True)
        except OSError:
            stream = io.StringIO()
        setattr(sys, name, stream)


def _run_pip_worker(argv):
    """在子进程里执行 pip，返回进程退出码。"""
    _prepare_worker_stdio()
    # 只放行 install：这个入口是给本程序自己装可选组件用的，不是通用 pip CLI。
    if not argv or argv[0] != "install":
        sys.stderr.write("pip worker 只支持 install 子命令\n")
        return 2
    try:
        from pip._internal.cli.main import main as pip_main
    except Exception as exc:
        sys.stderr.write(
            "本安装包未内置 pip，无法在线安装可选组件：%s\n"
            "请更新到最新版安装包，或改用源码版运行。\n" % exc)
        return 1
    try:
        return int(pip_main(list(argv)) or 0)
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception as exc:
        sys.stderr.write("pip 执行失败：%s\n" % exc)
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == PIP_WORKER_FLAG:
        sys.exit(_run_pip_worker(sys.argv[2:]))
    _install_third_party_notice_filter()
    from app import run
    sys.exit(run())
