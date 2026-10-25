#!/usr/bin/env python3
"""MiBuds Controller Linux — GTK 托盘小面板。

在 GNOME / KDE / Xfce 下通过 StatusNotifier（AppIndicator）显示耳机电量，
点击弹出小窗切换降噪 / 通透 / 关闭模式。
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------- 日志 ----------
LOG_PATH = os.environ.get("MIBUDS_LOG", "/tmp/mibuds-tray.log")


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def safe_text(value) -> str:
    """GTK/Pango 只吃合法 UTF-8；异常信息可能带 surrogate 转义字符，先清洗再上屏。"""
    text = value if isinstance(value, str) else str(value)
    return text.encode("utf-8", "replace").decode("utf-8", "replace")

import gi
gi.require_version("Gtk", "3.0")
# 不同发行版提供的 indicator 库名字不一样：Debian/Ubuntu 是 Ayatana 分支，
# 部分发行版只有老版 AppIndicator3。两个都试，谁在就用谁。
AppIndicator = None
for _ns in ("AyatanaAppIndicator3", "AppIndicator3"):
    try:
        gi.require_version(_ns, "0.1")
        _mod = __import__("gi.repository", fromlist=[_ns])
        AppIndicator = getattr(_mod, _ns)
        break
    except (ValueError, ImportError, AttributeError):
        continue
from gi.repository import Gtk, GLib

if AppIndicator is None:
    sys.stderr.write(
        "缺少托盘库。请安装 gir1.2-ayatanaappindicator3-0.1（Debian/Ubuntu）"
        "或 libayatana-appindicator-gtk3（Fedora/Arch）。\n"
    )
    sys.exit(1)

from rcsp import (
    EarbudsClient,
    ANC_OFF,
    ANC_NOISE_CANCELLING,
    ANC_TRANSPARENCY,
    ANC_MODE_NAMES,
    discover_channel,
    device_connected,
    find_earbuds_address,
)

# ---------- 配置 ----------

APP_VERSION = "1.0.1"
GITHUB_REPO = "Oh-Zhou/MiBUdsController-Linux"
GITHUB_LATEST_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"

DEFAULT_ADDR = "D0:C1:BF:7D:7C:82"
DEFAULT_CHANNEL = 29
REFRESH_INTERVAL = 30
CONNECT_RETRY_INTERVAL = 10

_XDG_CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
CONFIG_DIR = os.path.join(_XDG_CONFIG, "mibuds-linux")
ADDR_FILE = os.path.join(CONFIG_DIR, "address")
AUTOSTART_FILE = os.path.join(_XDG_CONFIG, "autostart", "mibuds-controller.desktop")
try:
    os.makedirs(CONFIG_DIR, exist_ok=True)
except OSError:
    # 沙箱 / 只读 home 下退化到临时目录
    CONFIG_DIR = os.path.join("/tmp", f"mibuds-linux-{os.getuid()}")
    ADDR_FILE = os.path.join(CONFIG_DIR, "address")
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_addr() -> str | None:
    """读取耳机地址：先看配置，再自动从已配对设备里找，最后才用内置默认值。"""
    try:
        with open(ADDR_FILE) as f:
            addr = f.read().strip()
            if addr:
                return addr
    except FileNotFoundError:
        pass
    found = find_earbuds_address()
    if found:
        log(f"自动发现已配对耳机：{found}")
        return found
    return DEFAULT_ADDR


# ---------- 开机自启 ----------

def _self_exec_command() -> str:
    """返回用于 .desktop Exec 的启动命令。

    优先用 PATH 里的 mibuds（deb/rpm 安装），否则用源码路径的 mibuds.sh。
    """
    import shutil as _shutil
    exe = _shutil.which("mibuds")
    if exe:
        return exe
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "mibuds.sh")


def autostart_enabled() -> bool:
    return os.path.isfile(AUTOSTART_FILE)


def set_autostart(enabled: bool) -> bool:
    """写入 / 删除 autostart/*.desktop。返回写入后实际是否处于启用状态。"""
    if enabled:
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=MiBuds 耳机控制\n"
            "Comment=Xiaomi/Redmi earbuds battery and ANC controller\n"
            f"Exec={_self_exec_command()}\n"
            "Icon=battery-good-symbolic\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        target_dir = os.path.dirname(AUTOSTART_FILE)
        tmp = AUTOSTART_FILE + ".tmp"
        try:
            os.makedirs(target_dir, exist_ok=True)
            with open(tmp, "w") as f:
                f.write(content)
            os.replace(tmp, AUTOSTART_FILE)
        except OSError as e:
            log(f"autostart write failed: {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return autostart_enabled()
        log(f"autostart enabled → {AUTOSTART_FILE}")
        return True
    try:
        os.remove(AUTOSTART_FILE)
        log("autostart disabled")
    except FileNotFoundError:
        pass
    except OSError as e:
        log(f"autostart remove failed: {e}")
    return autostart_enabled()


# ---------- 检查更新 ----------

def _parse_version(tag: str) -> tuple[int, ...]:
    """把 'v1.0.1' / '1.0.0' 解析成 (1,0,1)；解析不了返回空元组。"""
    import re as _re
    m = _re.search(r"(\d+(?:\.\d+)*)", tag or "")
    if not m:
        return ()
    try:
        return tuple(int(x) for x in m.group(1).split("."))
    except ValueError:
        return ()


def check_for_update() -> tuple[bool, str]:
    """查询 GitHub 最新 Release。返回 (是否有更新, 提示文字)。"""
    import json
    import urllib.request

    current = _parse_version(APP_VERSION)
    req = urllib.request.Request(
        GITHUB_LATEST_API,
        headers={
            "User-Agent": f"mibuds-linux/{APP_VERSION}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except Exception as e:
        return False, f"检查失败：{e}"

    tag = data.get("tag_name", "")
    latest = _parse_version(tag)
    if not latest:
        return False, f"无法解析最新版本号（{tag!r}）"
    if latest > current:
        return True, f"发现新版本 {tag}（当前 {APP_VERSION}）"
    return False, f"已是最新版本（{APP_VERSION}）"


def open_url(url: str) -> bool:
    """用系统默认浏览器打开 URL。"""
    import subprocess
    import shutil as _shutil

    for opener in (("xdg-open",), ("gio", "open"), ("gnome-open",)):
        if _shutil.which(opener[0]):
            try:
                subprocess.Popen(
                    [*opener, url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except OSError:
                continue
    return False


# ---------- 蓝牙后端 ----------

class BluetoothBackend:
    def __init__(self) -> None:
        self.addr = load_addr()
        self.channel: int | None = None
        self.client: EarbudsClient | None = None
        self.connected = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # UI 回调（均在 GLib 主线程执行）
        self.on_battery = None
        self.on_anc = None
        self.on_status = None
        self._last_status: str | None = None
        self._beat = time.time()

    def start(self) -> None:
        log("backend: starting worker thread")
        self._beat = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass

    def _watchdog(self) -> None:
        """工作线程卡死（例如底层 socket 半死）时的兜底：关掉旧连接让它重新来。"""
        while not self._stop.is_set():
            time.sleep(10)
            if time.time() - self._beat <= 60:
                continue
            log("watchdog: 工作线程 60 秒无进展，强制重连")
            self._beat = time.time()
            self.connected = False
            client = self.client
            self.client = None
            if client:
                try:
                    client.disconnect()
                except Exception:
                    pass

    def _run(self) -> None:
        while not self._stop.is_set():
            self._beat = time.time()
            # 只在系统蓝牙已经连上耳机后才去开控制通道。
            # 否则每次 RFCOMM connect 都会触发一次寻呼，和 GNOME/BlueZ 自己的
            # 重连互相撞车，耳机容易被反复踢掉。
            if not device_connected(self.addr):
                self.channel = None
                self._emit_status("耳机未连接，等待系统蓝牙连接…")
                self._wait_retry()
                continue
            self._emit_status("正在连接…")
            try:
                channel = self.channel
                if channel is None:
                    self._emit_status("正在查找控制通道…")
                    channel = discover_channel(self.addr)
                    if channel is not None:
                        self.channel = channel
                        log(f"控制通道：{channel}")
                    else:
                        # 耳机可能还没连上（SDP 需要链路），本轮先用默认通道试
                        channel = DEFAULT_CHANNEL
                        log(f"未发现服务，先用默认通道 {channel} 尝试")
                client = EarbudsClient(self.addr, channel)
                client._authenticate = lambda: None
                client._sync_state = lambda: None
                client.on_snapshot = self._handle_snapshot
                client.on_anc = self._handle_anc
                client.on_disconnected = self._handle_disconnect
                client.connect()
                self.client = client
                self.connected = True
                self._emit_status("已连接")
                client.get_device_info()
                last_refresh = time.time()
                while not self._stop.is_set() and self.connected:
                    self._beat = time.time()
                    time.sleep(1)
                    if time.time() - last_refresh >= REFRESH_INTERVAL:
                        last_refresh = time.time()
                        try:
                            client.get_device_info()
                        except Exception:
                            break
            except Exception as e:
                self.connected = False
                self._emit_status(f"连接失败：{e}")
            self._wait_retry()

    def _wait_retry(self) -> None:
        for _ in range(CONNECT_RETRY_INTERVAL * 10):
            if self._stop.is_set():
                return
            time.sleep(0.1)

    def set_anc(self, mode: int) -> None:
        if not (self.client and self.connected):
            # 没连上就明确说出来，别让按钮点了没反应
            self.emit_status_toast("耳机未连接，无法切换模式")
            return
        def _worker() -> None:
            try:
                self.client.set_anc_mode(mode)
            except Exception as e:
                log(f"set_anc({mode}) failed: {e}")
                self._emit_status(f"切换失败：{e}")
        threading.Thread(target=_worker, daemon=True).start()

    def emit_status_toast(self, text: str) -> None:
        """临时状态提示（不改变去重基准）。"""
        log(f"status: {text}")
        GLib.idle_add(self._apply_status, text)

    def refresh_battery(self) -> None:
        if self.client and self.connected:
            try:
                self.client.get_device_info()
            except Exception as e:
                log(f"refresh_battery failed: {e}")

    def _handle_snapshot(self, snap) -> None:
        left = snap.left.level if snap.left else None
        right = snap.right.level if snap.right else None
        case = snap.case.level if snap.case else None
        GLib.idle_add(self._emit_battery, left, right, case)

    def _handle_anc(self, mode: int) -> None:
        GLib.idle_add(self._emit_anc, mode)

    def _handle_disconnect(self, reason: str) -> None:
        self.connected = False
        GLib.idle_add(self._emit_status, f"已断开：{reason}")

    def _emit_battery(self, left, right, case) -> bool:
        log(f"battery update: L={left} R={right} C={case}")
        if self.on_battery:
            self.on_battery(left, right, case)
        return False

    def _emit_anc(self, mode: int) -> bool:
        log(f"anc update: {mode}")
        if self.on_anc:
            self.on_anc(mode)
        return False

    def _emit_status(self, text: str) -> None:
        # 可能从蓝牙工作线程调用 —— GTK 只能在主线程碰，统一丢回主循环
        if text == self._last_status:
            return  # 同样的状态别刷屏
        self._last_status = text
        log(f"status: {text}")
        GLib.idle_add(self._apply_status, text)

    def _apply_status(self, text: str) -> bool:
        if self.on_status:
            self.on_status(text)
        return False


# ---------- 图标 ----------

def battery_icon(left, right) -> str:
    levels = [v for v in (left, right) if v is not None]
    if not levels:
        return "battery-missing-symbolic"
    avg = min(levels)
    if avg >= 80:
        return "battery-full-symbolic"
    if avg >= 60:
        return "battery-good-symbolic"
    if avg >= 40:
        return "battery-low-symbolic"
    if avg >= 20:
        return "battery-caution-symbolic"
    return "battery-empty-symbolic"


# ---------- 主应用 ----------

class App:
    def __init__(self) -> None:
        self.backend = BluetoothBackend()
        self.backend.on_battery = self._on_battery
        self.backend.on_anc = self._on_anc
        self.backend.on_status = self._on_status

        self.left: int | None = None
        self.right: int | None = None
        self.case: int | None = None
        self.anc: int | None = None
        self._updating_ui = False

        # 托盘
        log("creating AppIndicator…")
        self.indicator = AppIndicator.Indicator.new(
            "mibuds-controller",
            "battery-missing-symbolic",
            AppIndicator.IndicatorCategory.HARDWARE,
        )
        self.indicator.set_title("小米耳机")
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        log("indicator created, status=ACTIVE")

        # 菜单
        self.menu = Gtk.Menu()

        self._open_item = Gtk.MenuItem(label="打开面板")
        self._open_item.connect("activate", lambda *_: self.window.show_all())
        self.menu.append(self._open_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        self._status_item = Gtk.MenuItem(label="启动中…")
        self._status_item.set_sensitive(False)
        self.menu.append(self._status_item)

        self._battery_item = Gtk.MenuItem(label="电量：—")
        self._battery_item.set_sensitive(False)
        self.menu.append(self._battery_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        mode_label = Gtk.MenuItem(label="降噪模式")
        self.mode_menu = Gtk.Menu()
        mode_label.set_submenu(self.mode_menu)
        self.menu.append(mode_label)

        self._mode_items: dict[int, Gtk.CheckMenuItem] = {}
        for mode in (ANC_NOISE_CANCELLING, ANC_TRANSPARENCY, ANC_OFF):
            item = Gtk.CheckMenuItem(label=ANC_MODE_NAMES[mode])
            item.connect("activate", self._on_mode_menu_toggled, mode)
            self.mode_menu.append(item)
            self._mode_items[mode] = item

        self.menu.append(Gtk.SeparatorMenuItem())

        self._autostart_item = Gtk.CheckMenuItem(label="开机自启")
        self._autostart_item.set_active(autostart_enabled())
        self._autostart_item.connect("toggled", self._on_autostart_toggled)
        self.menu.append(self._autostart_item)

        refresh = Gtk.MenuItem(label="刷新电量")
        refresh.connect("activate", lambda *_: self.backend.refresh_battery())
        self.menu.append(refresh)

        self._update_item = Gtk.MenuItem(label=f"检查更新…（当前 {APP_VERSION}）")
        self._update_item.connect("activate", self._on_check_update)
        self.menu.append(self._update_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="退出")
        quit_item.connect("activate", self._on_quit)
        self.menu.append(quit_item)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)
        log("indicator menu attached")

        # 小面板窗口
        self.window = self._build_window()
        log("window built")

    def _build_window(self) -> Gtk.Window:
        win = Gtk.Window(title="小米耳机控制")
        win.set_default_size(320, 330)
        win.set_resizable(False)
        win.connect("delete-event", self._on_window_delete)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)

        title = Gtk.Label(label="<span size='large'><b>小米耳机</b></span>")
        title.set_use_markup(True)
        vbox.pack_start(title, False, False, 0)

        self._win_status = Gtk.Label(label="启动中…")
        vbox.pack_start(self._win_status, False, False, 0)

        # 电量
        batt_frame = Gtk.Frame(label="电量")
        batt_grid = Gtk.Grid()
        batt_grid.set_column_spacing(8)
        batt_grid.set_row_spacing(4)
        batt_grid.set_margin_top(8)
        batt_grid.set_margin_bottom(8)
        batt_grid.set_margin_start(8)
        batt_grid.set_margin_end(8)

        self._win_left = Gtk.Label(label="左耳：—")
        self._win_right = Gtk.Label(label="右耳：—")
        self._win_case = Gtk.Label(label="充电盒：—")
        for i, lbl in enumerate((self._win_left, self._win_right, self._win_case)):
            lbl.set_halign(Gtk.Align.START)
            batt_grid.attach(lbl, 0, i, 1, 1)
        batt_frame.add(batt_grid)
        vbox.pack_start(batt_frame, False, False, 0)

        # 模式
        mode_frame = Gtk.Frame(label="降噪模式")
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mode_box.set_margin_top(8)
        mode_box.set_margin_bottom(8)
        mode_box.set_margin_start(8)
        mode_box.set_margin_end(8)

        self._mode_buttons: dict[int, Gtk.ToggleButton] = {}
        for mode, name in (
            (ANC_NOISE_CANCELLING, "降噪"),
            (ANC_TRANSPARENCY, "通透"),
            (ANC_OFF, "关闭"),
        ):
            btn = Gtk.ToggleButton(label=name)
            btn.connect("toggled", self._on_mode_button_toggled, mode)
            mode_box.pack_start(btn, True, True, 0)
            self._mode_buttons[mode] = btn
        mode_frame.add(mode_box)
        vbox.pack_start(mode_frame, False, False, 0)

        # 刷新
        refresh_btn = Gtk.Button(label="刷新电量")
        refresh_btn.connect("clicked", lambda *_: self.backend.refresh_battery())
        vbox.pack_start(refresh_btn, False, False, 0)

        # 开机自启：GNOME 风格开关行（左文字 + 右拨动开关）
        self._win_autostart_switch = Gtk.Switch()
        self._win_autostart_switch.set_active(autostart_enabled())
        self._win_autostart_switch.set_valign(Gtk.Align.CENTER)
        self._win_autostart_switch.connect("notify::active", self._on_autostart_switch_toggled)
        vbox.pack_start(self._make_switch_row("开机自启", self._win_autostart_switch), False, False, 0)

        win.add(vbox)
        return win

    @staticmethod
    def _make_switch_row(text: str, switch: Gtk.Switch) -> Gtk.Widget:
        """一行「左标签 + 右开关」，外层加边框，接近 GNOME 设置页样式。"""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_top(6)
        row.set_margin_bottom(6)
        row.set_margin_start(10)
        row.set_margin_end(10)

        label = Gtk.Label(label=text)
        label.set_halign(Gtk.Align.START)
        row.pack_start(label, True, True, 0)
        row.pack_start(switch, False, False, 0)

        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.add(row)
        return frame

    def _update_menu_item(self, attr: str, text: str, sensitive: bool = False) -> None:
        """更新托盘菜单里的动态文字。

        libayatana-appindicator 只在菜单结构变化时才重新导出 dbusmenu，
        单纯 set_label() 界面上不会刷新，所以这里整项替换。
        """
        old = getattr(self, attr)
        try:
            idx = self.menu.get_children().index(old)
        except ValueError:
            return
        new = Gtk.MenuItem(label=safe_text(text))
        new.set_sensitive(sensitive)
        self.menu.insert(new, idx)
        self.menu.remove(old)
        setattr(self, attr, new)
        self.menu.show_all()

    def _on_window_delete(self, *_args) -> bool:
        self.window.hide()
        return True

    # ---- 状态回调 ----

    def _on_battery(self, left, right, case) -> None:
        self.left, self.right, self.case = left, right, case
        try:
            self.indicator.set_icon_full(battery_icon(left, right), "耳机电量")

            parts = []
            if left is not None:
                parts.append(f"左 {left}%")
            if right is not None:
                parts.append(f"右 {right}%")
            if case is not None:
                parts.append(f"盒 {case}%")
            self._update_menu_item(
                "_battery_item", "电量：" + ("  ".join(parts) if parts else "—")
            )
            self._win_left.set_label(f"左耳：{left if left is not None else '—'}%")
            self._win_right.set_label(f"右耳：{right if right is not None else '—'}%")
            self._win_case.set_label(f"充电盒：{case if case is not None else '—'}%")
        except Exception as e:  # 窗口已销毁等情况不该带崩整个程序
            log(f"battery UI update failed: {e}")

    def _on_anc(self, mode: int) -> None:
        self.anc = mode
        self._updating_ui = True
        try:
            for m, item in self._mode_items.items():
                if item.get_active() != (m == mode):
                    item.set_active(m == mode)
            for m, btn in self._mode_buttons.items():
                if btn.get_active() != (m == mode):
                    btn.set_active(m == mode)
        finally:
            self._updating_ui = False
        name = ANC_MODE_NAMES.get(mode, f"0x{mode:02X}")
        try:
            self.indicator.set_title(safe_text(f"小米耳机 — {name}"))
        except Exception as e:
            log(f"title update failed: {e}")

    def _on_status(self, text: str) -> None:
        text = safe_text(text)
        self._update_menu_item("_status_item", text)
        if self._win_status is not None:
            self._win_status.set_label(text)

    # ---- 用户操作 ----

    def _on_mode_menu_toggled(self, item: Gtk.CheckMenuItem, mode: int) -> None:
        if self._updating_ui:
            return
        if item.get_active():
            self.backend.set_anc(mode)
            # 取消其他
            self._updating_ui = True
            try:
                for m, other in self._mode_items.items():
                    if m != mode and other.get_active():
                        other.set_active(False)
            finally:
                self._updating_ui = False
        else:
            # 防止取消当前选中项
            if self.anc == mode:
                self._updating_ui = True
                try:
                    item.set_active(True)
                finally:
                    self._updating_ui = False

    def _on_mode_button_toggled(self, btn: Gtk.ToggleButton, mode: int) -> None:
        if self._updating_ui:
            return
        if btn.get_active():
            self.backend.set_anc(mode)
            self._updating_ui = True
            try:
                for m, other in self._mode_buttons.items():
                    if m != mode and other.get_active():
                        other.set_active(False)
            finally:
                self._updating_ui = False
        else:
            if self.anc == mode:
                self._updating_ui = True
                try:
                    btn.set_active(True)
                finally:
                    self._updating_ui = False

    def _on_autostart_toggled(self, item: Gtk.CheckMenuItem) -> None:
        if self._updating_ui:
            return
        actual = set_autostart(item.get_active())
        self._sync_autostart_ui(actual)

    def _on_autostart_switch_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._updating_ui:
            return
        actual = set_autostart(switch.get_active())
        self._sync_autostart_ui(actual)
        if actual != switch.get_active() and not self._updating_ui:
            self._show_autostart_error()

    def _show_autostart_error(self) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text="无法写入开机自启配置",
        )
        dialog.format_secondary_text(
            f"请确认该目录可写：\n{os.path.dirname(AUTOSTART_FILE)}"
        )
        dialog.run()
        dialog.destroy()

    def _sync_autostart_ui(self, enabled: bool) -> None:
        """把托盘勾选项和面板开关统一到 enabled。"""
        self._updating_ui = True
        try:
            if self._autostart_item.get_active() != enabled:
                self._autostart_item.set_active(enabled)
            if self._win_autostart_switch.get_active() != enabled:
                self._win_autostart_switch.set_active(enabled)
        finally:
            self._updating_ui = False

    def _on_check_update(self, *_args) -> None:
        """后台查询 GitHub 最新 Release，完成后弹窗显示结果。"""
        self._update_menu_item("_update_item", "检查更新中…")

        def _worker() -> None:
            has_update, text = check_for_update()
            GLib.idle_add(self._on_update_result, has_update, text)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_result(self, has_update: bool, text: str) -> bool:
        self._update_menu_item("_update_item", f"检查更新…（当前 {APP_VERSION}）", True)

        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.NONE,
            text="检查更新",
        )
        dialog.format_secondary_text(text)
        dialog.add_button("关闭", Gtk.ResponseType.CLOSE)
        if has_update:
            dialog.add_button("前往下载", Gtk.ResponseType.OK)
        resp = dialog.run()
        dialog.destroy()
        if resp == Gtk.ResponseType.OK:
            open_url(GITHUB_RELEASES_URL)
        return False

    def _on_quit(self, *_args) -> None:
        self.backend.stop()
        Gtk.main_quit()

    def run(self) -> None:
        log("starting backend + GTK main loop")
        self.backend.start()
        if os.environ.get("MIBUDS_SHOW_WINDOW") == "1":
            self.window.show_all()
        if os.environ.get("MIBUDS_TEST_ANC") == "1":
            # 调试：依次切换模式并观察 0xF4 确认
            def _cycle():
                for mode in (ANC_NOISE_CANCELLING, ANC_TRANSPARENCY, ANC_OFF):
                    log(f"TEST set_anc({mode})")
                    self.backend.set_anc(mode)
                    time.sleep(2)
                log("TEST anc cycle done")
                return False
            threading.Thread(target=_cycle, daemon=True).start()
        Gtk.main()
        log("GTK main loop exited")


def main() -> None:
    log("=== MiBuds tray starting ===")
    log(f"DISPLAY={os.environ.get('DISPLAY')} WAYLAND={os.environ.get('WAYLAND_DISPLAY')}")
    log(f"DBUS={os.environ.get('DBUS_SESSION_BUS_ADDRESS')}")
    signal.signal(signal.SIGINT, lambda *_: Gtk.main_quit())
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
    app = App()
    app.run()


if __name__ == "__main__":
    main()
