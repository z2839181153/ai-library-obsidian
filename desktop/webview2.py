r"""桌面壳 · WebView2 运行时检测（P5-1）。

Windows 上 pywebview 依赖 WebView2 Runtime（Win11 自带）。
检测方法（任一命中即视为可用）：
1. 注册表 `EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`（WebView2 Runtime 的 pv 版本键）
2. 尝试导入 pywebview（依赖是否安装由 pip 保证，此处不判）

缺失时返回 False，由 launcher 决定降级（提示下载 / 浏览器打开）。
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger("ai_library.desktop.webview2")

# WebView2 Runtime 注册表 GUID
_WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
# 常见注册表路径（64 位系统下 WOW6432Node 是 WebView2 的常规位置）
_REG_PATHS = [
    r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients",
    r"SOFTWARE\Microsoft\EdgeUpdate\Clients",
    r"SOFTWARE\WOW6432Node\Microsoft\EdgeWebView\Application",
    r"SOFTWARE\Microsoft\EdgeWebView\Application",
]
_HKCU_PATHS = [
    r"SOFTWARE\Microsoft\EdgeUpdate\Clients",
    r"SOFTWARE\Microsoft\EdgeWebView\Application",
]


def webview2_available() -> bool:
    """检测本机是否安装了 WebView2 Runtime。"""
    if sys.platform != "win32":
        return True  # 非 Windows 平台 pywebview 用其他渲染器
    try:
        import winreg  # noqa: F401
    except ImportError:
        return False
    return _check_registry()


def _check_registry() -> bool:
    import winreg

    for hive, paths in ((winreg.HKEY_LOCAL_MACHINE, _REG_PATHS), (winreg.HKEY_CURRENT_USER, _HKCU_PATHS)):
        for base in paths:
            try:
                with winreg.OpenKey(hive, base) as key:
                    # 枚举子键找 GUID / WebView2 版本键
                    try:
                        i = 0
                        while True:
                            sub = winreg.EnumKey(key, i)
                            if _WEBVIEW2_GUID.lower() in sub.lower() or "EdgeWebView" in sub:
                                return True
                            i += 1
                    except OSError:
                        pass
                    # 部分系统直接把 GUID 作为键名读取 pv
                    try:
                        with winreg.OpenKey(key, _WEBVIEW2_GUID) as guid_key:
                            winreg.QueryValueEx(guid_key, "pv")
                            return True
                    except OSError:
                        pass
            except OSError:
                continue
    return False


def webview2_download_url() -> str:
    """WebView2 Runtime 下载页。"""
    return "https://developer.microsoft.com/microsoft-edge/webview2/"
