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

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, AyatanaAppIndicator3, GLib

from rcsp import (
    EarbudsClient,
    ANC_OFF,
    ANC_NOISE_CANCELLING,
    ANC_TRANSPARENCY,
    ANC_MODE_NAMES,
)

# ---------- 配置 ----------

DEFAULT_ADDR = "D0:C1:BF:7D:7C:82"
DEFAULT_CHANNEL = 29
REFRESH_INTERVAL = 30
CONNECT_RETRY_INTERVAL = 10

CONFIG_DIR = os.path.expanduser("~/.config/mibuds-linux")
ADDR_FILE = os.path.join(CONFIG_DIR, "address")
try:
    os.makedirs(CONFIG_DIR, exist_ok=True)
except OSError:
    # 沙箱 / 只读 home 下退化到临时目录
    CONFIG_DIR = os.path.join("/tmp", f"mibuds-linux-{os.getuid()}")
    ADDR_FILE = os.path.join(CONFIG_DIR, "address")
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_addr() -> str:
    try:
        with open(ADDR_FILE) as f:
            addr = f.read().strip()
            if addr:
                return addr
    except FileNotFoundError:
        pass
    return DEFAULT_ADDR


# ---------- 蓝牙后端 ----------

class BluetoothBackend:
    def __init__(self) -> None:
        self.addr = load_addr()
        self.channel = DEFAULT_CHANNEL
        self.client: EarbudsClient | None = None
        self.connected = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # UI 回调（均在 GLib 主线程执行）
        self.on_battery = None
        self.on_anc = None
        self.on_status = None

    def start(self) -> None:
        log("backend: starting worker thread")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            self._emit_status("正在连接…")
            try:
                client = EarbudsClient(self.addr, self.channel)
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
            if not self._stop.is_set():
                for _ in range(CONNECT_RETRY_INTERVAL * 10):
                    if self._stop.is_set():
                        break
                    time.sleep(0.1)

    def set_anc(self, mode: int) -> None:
        if self.client and self.connected:
            try:
                self.client.set_anc_mode(mode)
            except Exception:
                pass

    def refresh_battery(self) -> None:
        if self.client and self.connected:
            try:
                self.client.get_device_info()
            except Exception:
                pass

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

    def _emit_status(self, text: str) -> bool:
        log(f"status: {text}")
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
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "mibuds-controller",
            "battery-missing-symbolic",
            AyatanaAppIndicator3.IndicatorCategory.HARDWARE,
        )
        self.indicator.set_title("小米耳机")
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
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

        refresh = Gtk.MenuItem(label="刷新电量")
        refresh.connect("activate", lambda *_: self.backend.refresh_battery())
        self.menu.append(refresh)

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
        win.set_default_size(320, 260)
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

        win.add(vbox)
        return win

    def _on_window_delete(self, *_args) -> bool:
        self.window.hide()
        return True

    # ---- 状态回调 ----

    def _on_battery(self, left, right, case) -> None:
        self.left, self.right, self.case = left, right, case
        icon = battery_icon(left, right)
        self.indicator.set_icon_full(icon, "耳机电量")

        parts = []
        if left is not None:
            parts.append(f"左 {left}%")
        if right is not None:
            parts.append(f"右 {right}%")
        if case is not None:
            parts.append(f"盒 {case}%")
        self._battery_item.set_label("电量：" + ("  ".join(parts) if parts else "—"))

        self._win_left.set_label(f"左耳：{left if left is not None else '—'}%")
        self._win_right.set_label(f"右耳：{right if right is not None else '—'}%")
        self._win_case.set_label(f"充电盒：{case if case is not None else '—'}%")

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
        self.indicator.set_title(f"小米耳机 — {name}")

    def _on_status(self, text: str) -> None:
        self._status_item.set_label(text)
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
