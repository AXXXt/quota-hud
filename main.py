# -*- coding: utf-8 -*-
"""
QuotaHUD 入口（M2）。
  python main.py             # 胶囊窗口模式：常态小窗，悬停展开面板（窗口尺寸自动切换）
  python main.py --no-window # 无头模式（仅本地服务 http://127.0.0.1:15729）
"""
import sys
import threading
import time
import ctypes

PORT = 15729
PILL_W, PILL_H = 420, 56       # 胶囊态窗口大小
PANEL_W, PANEL_H = 420, 730    # 展开态：宽度不变（面板384px窄于胶囊），仅向下增高


# ---------------- 液态玻璃：Windows 系统级特效 ----------------
def find_hwnd(title="QuotaHUD"):
    user32 = ctypes.windll.user32
    return user32.FindWindowW(None, title) or 0


def apply_liquid_glass(hwnd):
    """Win11 22H2+：DWM Acrylic 背景特效 + 系统圆角 + 去边框。成功返回 True"""
    if not hwnd:
        return False
    try:
        dwm = ctypes.windll.dwmapi

        class MARGINS(ctypes.Structure):
            _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                        ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]

        m = MARGINS(-1, -1, -1, -1)
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))

        pref = ctypes.c_int(2)      # DWMWCP_ROUND 系统圆角
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)

        none_color = ctypes.c_uint(0xFFFFFFFE)  # DWMWA_COLOR_NONE：彻底去掉窗口描边
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(none_color), 4)

        backdrop = ctypes.c_int(3)    # DWMSBT_TRANSIENTWINDOW = Acrylic
        return dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), 4) == 0
    except Exception:
        return False


def apply_acrylic_fallback(hwnd):
    """Win10/早期 Win11：SetWindowCompositionAttribute 模糊背景（效果略逊）"""
    if not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32

        class ACCENTPOLICY(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                        ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

        class WINCOMPATTRDATA(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                        ("SizeOfData", ctypes.c_size_t)]

        # ACCENT_ENABLE_ACRYLICBLURBEHIND=4，浅色染色 ABGR=0x99F7F5FC（近似玻璃白）
        policy = ACCENTPOLICY(4, 2, 0x99F7F5FC, 0)
        data = WINCOMPATTRDATA(19, ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
                               ctypes.sizeof(policy))
        return bool(user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
    except Exception:
        return False


def get_window_size(hwnd):
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    rc = RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
    return rc.right - rc.left, rc.bottom - rc.top


def apply_region(hwnd, capsule=None, radius=24):
    """把窗口裁剪成胶囊形或圆角矩形。capsule 缺省时按宽高比自动判断（DPI 免疫）"""
    if not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        w, h = get_window_size(hwnd)
        if w <= 0 or h <= 0:
            return False
        if capsule is None:
            capsule = h < w * 0.6   # 矮窗=胶囊，高窗=圆角矩形
        if capsule:
            ew = eh = h
        else:
            try:
                dpi = user32.GetDpiForWindow(hwnd)
            except Exception:
                dpi = 96
            r = int(radius * (dpi or 96) / 96)
            ew = eh = r * 2
        rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, ew, eh)
        return bool(user32.SetWindowRgn(hwnd, rgn, True))
    except Exception:
        return False


def glass_keeper():
    """常驻自愈：按宽高比重申裁剪形状 + 补回 DWM Acrylic（重设 Region 会打掉玻璃）"""
    n = 0
    while True:
        try:
            hwnd = find_hwnd("QuotaHUD")
            if hwnd:
                apply_region(hwnd)          # 按宽高比自动选形
                if n % 2 == 0:              # 每 4s 补一次玻璃（轻量 API 调用）
                    if not apply_liquid_glass(hwnd):
                        apply_acrylic_fallback(hwnd)
                n += 1
        except Exception:
            pass
        time.sleep(2)


def apply_no_taskbar(hwnd):
    """加 WS_EX_TOOLWINDOW 样式：不在任务栏显示（托盘图标仍在）"""
    if not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW)
        # 重新声明显示使样式生效
        user32.ShowWindow(hwnd, 5)  # SW_SHOW
        return True
    except Exception:
        return False


def glass_worker(attempts=40):
    """webview 启动后找窗口句柄并应用液态玻璃 + 胶囊裁剪 + 隐藏任务栏图标"""
    for _ in range(attempts):
        hwnd = find_hwnd("QuotaHUD")
        if hwnd:
            mode = None
            if apply_liquid_glass(hwnd):
                mode = "dwm-acrylic"
            elif apply_acrylic_fallback(hwnd):
                mode = "accent-blur"
            apply_region(hwnd)
            apply_no_taskbar(hwnd)
            threading.Thread(target=glass_keeper, daemon=True).start()
            if mode:
                return mode
            return "fallback-tint"
        time.sleep(0.5)
    return "no-hwnd"


def run_server(port):
    import uvicorn
    from server_api import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def port_busy(port):
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def kill_stale_instance(port):
    """端口被占时，尝试结束上一个 QuotaHUD 实例（自己人，让位）"""
    if not port_busy(port):
        return
    try:
        out = __import__("subprocess").run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        ).stdout
        pids = set()
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                pids.add(line.split()[-1])
        me = __import__("os").getpid()
        for pid in pids:
            if pid.isdigit() and int(pid) != me:
                __import__("subprocess").run(["cmd", "/c", f"taskkill /F /PID {pid}"],
                                             capture_output=True, timeout=10)
        # 等端口释放
        for _ in range(20):
            if not port_busy(port):
                return
            time.sleep(0.3)
    except Exception:
        pass


def wait_http(url, timeout=15):
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def make_tray():
    """托盘图标（失败不阻塞主流程）"""
    try:
        import pystray
        from PIL import Image, ImageDraw

        tray_state = {"hidden": False}   # 自己记状态，不依赖 webview 的 hidden 属性

        def on_exit(icon, item):
            icon.stop()
            import os
            os._exit(0)

        def force_show(hwnd):
            """Win32 层强制显示并还原（webview.show 恢复不了最小化的窗口）"""
            SW_RESTORE = 9
            SW_SHOW = 5
            user32 = ctypes.windll.user32
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            else:
                user32.ShowWindow(hwnd, SW_SHOW)
            user32.SetForegroundWindow(hwnd)

        def on_toggle(icon, item):
            try:
                import webview
                hwnd = find_hwnd("QuotaHUD")
                if tray_state["hidden"]:
                    # 当前是隐藏态 → 显示
                    for w in webview.windows:
                        w.show()
                    if hwnd:
                        force_show(hwnd)
                    tray_state["hidden"] = False
                else:
                    # 当前是显示态 → 隐藏
                    for w in webview.windows:
                        w.hide()
                    tray_state["hidden"] = True
            except Exception:
                pass

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([4, 4, 60, 60], radius=16, fill=(62, 46, 208, 255))
        d.rectangle([16, 30, 21, 48], fill=(255, 255, 255, 255))
        d.rectangle([25, 22, 30, 48], fill=(255, 255, 255, 255))
        d.rectangle([34, 26, 39, 48], fill=(245, 166, 35, 255))
        d.rectangle([43, 16, 48, 48], fill=(255, 255, 255, 255))
        menu = pystray.Menu(
            pystray.MenuItem("显示 / 隐藏", on_toggle, default=True),
            pystray.MenuItem("退出", on_exit),
        )
        icon = pystray.Icon("QuotaHUD", img, "QuotaHUD 额度悬浮窗", menu)
        threading.Thread(target=icon.run, daemon=True).start()
    except Exception:
        pass


def set_autostart(enable=True):
    """开机自启（注册表 HKCU Run），指向当前 exe"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, "QuotaHUD", 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(key, "QuotaHUD")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


class Api:
    """暴露给前端 JS 的桥（window.pywebview.api.xxx）"""
    def __init__(self, window):
        self.window = window

    def _win(self):
        if self.window is not None:
            return self.window
        try:
            import webview
            return webview.windows[0] if webview.windows else None
        except Exception:
            return None

    def resize_panel(self, expanded):
        def work():
            try:
                w = self._win()
                if not w:
                    return
                target_w, target_h = (PANEL_W, PANEL_H) if expanded else (PILL_W, PILL_H)
                w.resize(target_w, target_h)
                # 等尺寸稳定（连续两次相同），再按宽高比裁剪 + 补玻璃
                hwnd = find_hwnd("QuotaHUD")
                last = None
                for _ in range(20):
                    if hwnd:
                        cw, ch = get_window_size(hwnd)
                        if last and abs(cw - last[0]) <= 1 and abs(ch - last[1]) <= 1:
                            break
                        last = (cw, ch)
                    time.sleep(0.1)
                apply_region(hwnd)               # 按宽高比自动
                apply_liquid_glass(hwnd) or apply_acrylic_fallback(hwnd)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def set_autostart(self, on):
        ok = set_autostart(bool(on))
        return {"ok": ok, "enabled": bool(on)}


def main():
    global PORT
    args = sys.argv[1:]
    no_window = "--no-window" in args
    if "--port" in args:
        PORT = int(args[args.index("--port") + 1])

    # 端口被上一个实例占用：让它退位，避免"双击没反应"
    kill_stale_instance(PORT)

    import quota_hud as Q
    if not Q.stations():
        Q.save_stations(Q.default_stations())
    Q.start_poller(interval=180)

    t = threading.Thread(target=run_server, args=(PORT,), daemon=True)
    t.start()
    if not wait_http(f"http://127.0.0.1:{PORT}/api/state"):
        print("[QuotaHUD] 服务启动失败", file=sys.stderr)
        sys.exit(1)

    if no_window:
        print(f"[QuotaHUD] running at http://127.0.0.1:{PORT}")
        while True:
            time.sleep(3600)

    import webview
    win = webview.create_window(
        "QuotaHUD",
        f"http://127.0.0.1:{PORT}/",
        js_api=Api(None),
        width=PILL_W, height=PILL_H,
        min_size=(PILL_W, PILL_H),
        resizable=True,
        frameless=True,
        on_top=True,
        shadow=False,
        transparent=True,      # 透明窗口：桌面透过玻璃可见
    )
    threading.Thread(target=glass_worker, daemon=True).start()
    make_tray()
    webview.start(func=None, gui="edgechromium", debug=False)


if __name__ == "__main__":
    main()
