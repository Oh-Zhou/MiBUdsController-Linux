"""RCSP 帧协议编解码 + 蓝牙传输 + SAFER+ 鉴权 + 设备状态（移植自原项目）。"""
from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from saferplus import SaferPlus

HEADER = b"\xFE\xDC\xBA"
TRAILER = 0xEF

# 小米耳机控制服务的 SDP UUID（RCSP over RFCOMM）
SDP_UUID = "0000fd2d-0000-1000-8000-00805f9b34fb"
SDP_UUID16 = "0xfd2d"

# 常见候选通道：先试已知的 29，再扫一遍常见 SPP 通道
CANDIDATE_CHANNELS = [29, 30, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 31]

# 单次读写超时（秒）
IO_TIMEOUT = 5.0

# 消息类型
TYPE_PHONE_REQUEST = 0x40
TYPE_EARBUDS_REQUEST = 0x80
TYPE_EARBUDS_NOTIFY = 0xC0
TYPE_RESPONSE = 0x00  # type & 0x40 == 0 -> response

# 操作码
OP_GET_DEVICE_INFO = 0x02
OP_SET_ANC = 0x08
OP_GET_RUN_INFO = 0x09
OP_REPORT_STATUS = 0x0E
OP_AUTH_CHALLENGE = 0x50
OP_AUTH_CONFIRM = 0x51
OP_SET_CONFIG = 0xF2
OP_GET_CONFIG = 0xF3
OP_NOTIFY_CONFIG = 0xF4

# ANC 模式
ANC_OFF = 0x00
ANC_NOISE_CANCELLING = 0x01
ANC_TRANSPARENCY = 0x02

ANC_MODE_NAMES = {ANC_OFF: "关闭", ANC_NOISE_CANCELLING: "降噪", ANC_TRANSPARENCY: "通透"}


@dataclass
class RcspMessage:
    msg_type: int
    opcode: int
    seq: int
    payload: bytes

    @property
    def is_request(self) -> bool:
        return (self.msg_type & 0x40) != 0

    @property
    def is_response(self) -> bool:
        return (self.msg_type & 0x40) == 0

    def encode(self) -> bytes:
        is_req = self.is_request
        payload_len = len(self.payload) + (1 if is_req else 2)
        frame = bytearray(8 + payload_len)
        frame[0:3] = HEADER
        frame[3] = self.msg_type
        frame[4] = self.opcode
        frame[5] = (payload_len >> 8) & 0xFF
        frame[6] = payload_len & 0xFF
        offset = 7
        if not is_req:
            frame[offset] = 0x00
            offset += 1
        frame[offset] = self.seq
        offset += 1
        frame[offset:offset + len(self.payload)] = self.payload
        frame[-1] = TRAILER
        return bytes(frame)

    @staticmethod
    def decode(frame: bytes) -> "RcspMessage":
        if len(frame) < 9:
            raise ValueError("frame too short")
        if frame[0:3] != HEADER:
            raise ValueError("bad header")
        if frame[-1] != TRAILER:
            raise ValueError("bad trailer")
        msg_type = frame[3]
        is_req = (msg_type & 0x40) != 0
        payload_len = (frame[5] << 8) | frame[6]
        if len(frame) != 8 + payload_len:
            raise ValueError("length mismatch")
        payload_off = 7 + (1 if is_req else 2)
        seq = frame[payload_off - 1]
        payload = frame[payload_off:-1]
        return RcspMessage(msg_type, frame[4], seq, bytes(payload))


class FrameParser:
    """RCSP 帧解析器：处理粘包/半包。"""

    def __init__(self) -> None:
        self._buf = bytearray()

    def append(self, data: bytes) -> None:
        self._buf.extend(data)
        if len(self._buf) > 4096:
            self._buf = self._buf[-4096:]

    def drain(self) -> list[RcspMessage]:
        messages: list[RcspMessage] = []
        while True:
            start = self._find_header()
            if start < 0:
                self._buf.clear()
                break
            if start > 0:
                del self._buf[:start]
            if len(self._buf) < 7:
                break
            payload_len = (self._buf[5] << 8) | self._buf[6]
            total = 8 + payload_len
            if total > 4096:
                del self._buf[0]
                continue
            if len(self._buf) < total:
                break
            if self._buf[total - 1] != TRAILER:
                del self._buf[0]
                continue
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            messages.append(RcspMessage.decode(frame))
        return messages

    def _find_header(self) -> int:
        for i in range(len(self._buf) - 2):
            if self._buf[i] == 0xFE and self._buf[i + 1] == 0xDC and self._buf[i + 2] == 0xBA:
                return i
        return -1


def tlv_walk(payload: bytes, index_offset: int) -> list[tuple[int, bytes]]:
    """遍历小米 RCSP TLV 流。"""
    entries: list[tuple[int, bytes]] = []
    i = 0
    while i + 1 < len(payload):
        length = payload[i]
        if length <= index_offset:
            break
        idx_pos = i + index_offset
        if idx_pos >= len(payload):
            break
        data_start = idx_pos + 1
        data_len = length - index_offset
        if data_start + data_len > len(payload):
            break
        entries.append((payload[idx_pos], payload[data_start:data_start + data_len]))
        i = length + 1
    return entries


@dataclass
class BatteryReading:
    level: Optional[int]
    charging: bool

    @staticmethod
    def from_byte(value: int) -> Optional["BatteryReading"]:
        if value == 0xFF:
            return None
        return BatteryReading(value & 0x7F, (value & 0x80) != 0)


@dataclass
class DeviceSnapshot:
    left: Optional[BatteryReading] = None
    right: Optional[BatteryReading] = None
    case: Optional[BatteryReading] = None
    firmware1: Optional[str] = None
    firmware2: Optional[str] = None
    vid_pid: Optional[str] = None
    anc_mode: int = ANC_OFF


def _nibble_version(high: int, low: int) -> str:
    return f"{(high >> 4) & 0xF}.{high & 0xF}.{(low >> 4) & 0xF}.{low & 0xF}"


# ---------- 服务发现 ----------

def sdp_lookup_channel(addr: str, timeout: float = 8.0) -> Optional[int]:
    """用 sdptool 查 fd2d 服务的 RFCOMM 通道号；失败（无 sdptool / 无结果）返回 None。"""
    import re
    import shutil
    import subprocess

    exe = shutil.which("sdptool")
    if not exe:
        return None
    for args in (
        [exe, "browse", "--uuid", SDP_UUID16, addr],
        [exe, "browse", addr],
    ):
        try:
            out = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if SDP_UUID16 not in out.lower() and "fd2d" not in out.lower():
            continue
        # 只取 fd2d 那一段里的 Channel
        idx = max(out.lower().find("fd2d"), 0)
        seg = out[idx:]
        m = re.search(r"Channel:\s*(\d+)", seg, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def device_connected(addr: str) -> bool:
    """问一下 BlueZ 这台设备当前是否已连接（连接前做 SDP/探通道没有意义）。"""
    import shutil
    import subprocess

    exe = shutil.which("bluetoothctl")
    if not exe:
        return True  # 没工具就不拦，直接试
    try:
        out = subprocess.run(
            [exe, "info", addr], capture_output=True, text=True, timeout=8
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return "Connected: yes" in out


def probe_channel(addr: str, channels: Optional[list[int]] = None,
                  timeout: float = 1.5) -> Optional[int]:
    """逐个尝试连接候选 RFCOMM 通道，返回第一个能连上的。"""
    for ch in (channels or CANDIDATE_CHANNELS):
        try:
            s = socket.socket(
                socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM
            )
            s.settimeout(timeout)
            s.connect((addr, ch))
            s.close()
            return ch
        except OSError:
            continue
    return None


def discover_channel(addr: str, timeout: float = 1.5) -> Optional[int]:
    """先走 SDP，查不到再扫候选通道。设备没连上时直接返回 None。"""
    if not device_connected(addr):
        return None
    ch = sdp_lookup_channel(addr)
    if ch is not None:
        return ch
    return probe_channel(addr, timeout=timeout)


def find_earbuds_address() -> Optional[str]:
    """从已配对设备里找名字像小米/红米耳机的那个。"""
    import re
    import shutil
    import subprocess

    exe = shutil.which("bluetoothctl")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "devices", "Paired"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    kw = ("redmi", "xiaomi", "mi buds", "mibuds", "buds")
    for line in out.splitlines():
        m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line)
        if not m:
            continue
        name = m.group(2).lower()
        if any(k in name for k in kw):
            return m.group(1).upper()
    return None


class EarbudsClient:
    """RFCOMM 会话：连接、SAFER+ 鉴权、状态同步、指令分发。"""
    RESPONSE_TIMEOUT = 5.0

    def __init__(self, addr: str, channel: int = 29) -> None:
        self.addr = addr
        self.channel = channel
        self._sock: Optional[socket.socket] = None
        self._parser = FrameParser()
        self._send_lock = threading.Lock()
        self._pending: dict[int, tuple[byte, threading.Event, Optional[RcspMessage]]] = {}
        self._pending_lock = threading.Lock()
        self._seq = 0
        self._connected = False
        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._safer = SaferPlus()
        self.snapshot = DeviceSnapshot()
        self.on_snapshot = None       # callback(DeviceSnapshot)
        self.on_anc = None            # callback(int)
        self.on_config = None         # callback(config_id, values)
        self.on_disconnected = None   # callback(reason)
        self.on_log = None            # callback(direction, RcspMessage)
        self._pending_inbound: dict[int, tuple[int, threading.Event]] = {}

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self._sock.settimeout(5)
        self._sock.connect((self.addr, self.channel))
        # 不用 settimeout(None)：链路半死时 sendall 会永久阻塞，
        # 而且此时发送锁被占住，后续所有指令都跟着卡死。
        self._sock.settimeout(IO_TIMEOUT)
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        # 官方客户端在 SPP 套接字打开后短暂等待
        time.sleep(0.5)
        try:
            self._authenticate()
            print("[鉴权] SAFER+ 握手完成")
        except Exception as e:
            print(f"[鉴权] 未完成，继续尝试连接：{e}")
        self._connected = True
        self._sync_state()

    def disconnect(self) -> None:
        self._running = False
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ---- 公开指令 ----

    def set_anc_mode(self, mode: int) -> None:
        self._send_no_wait(OP_SET_ANC, bytes([0x02, 0x04, mode]))

    def get_device_info(self) -> None:
        self._send_no_wait(OP_GET_DEVICE_INFO, b"\xFF\xFF\xFF\xFF")

    def get_run_info(self) -> None:
        self._send_no_wait(OP_GET_RUN_INFO, b"\xFF\xFF\xFF\xFF")

    def get_config(self, config_id: int) -> None:
        self._send_no_wait(OP_GET_CONFIG, bytes([0x00, config_id]))

    def set_config(self, config_id: int, values: bytes) -> None:
        payload = bytes([len(values) + 2, 0x00, config_id]) + values
        self._send_no_wait(OP_SET_CONFIG, payload)

    # ---- 鉴权 ----

    def _authenticate(self) -> None:
        random = self._safer.generate_challenge()
        print("[鉴权] 发送挑战 0x50")
        resp = self._send_request(OP_AUTH_CHALLENGE, bytes([0x01]) + random)
        if resp.opcode != OP_AUTH_CHALLENGE or resp.msg_type == TYPE_EARBUDS_REQUEST \
                or resp.msg_type == TYPE_EARBUDS_NOTIFY or len(resp.payload) < 16:
            raise RuntimeError("鉴权响应格式不正确")
        answer = resp.payload[-16:] if len(resp.payload) >= 17 else resp.payload
        expected = self._safer.compute_challenge_response(random)
        if answer != expected:
            print("[鉴权] 设备返回自有挑战，回以 SAFER+ 响应")
            response = self._safer.compute_challenge_response(answer)
            self._send_response(OP_AUTH_CHALLENGE, resp.seq, bytes([0x01]) + response)
        else:
            print("[鉴权] 挑战校验通过")
        print("[鉴权] 发送确认 0x51")
        self._send_request(OP_AUTH_CONFIRM, bytes([0x01, 0x00]))
        print("[鉴权] 等待耳机发起的 0x51 确认")
        self._wait_inbound(OP_AUTH_CONFIRM)
        print("[鉴权] 收到耳机 0x51")

    def _sync_state(self) -> None:
        self._send_no_wait(OP_GET_DEVICE_INFO, b"\xFF\xFF\xFF\xFF")
        self._send_no_wait(OP_GET_RUN_INFO, b"\xFF\xFF\xFF\xFF")
        for cid in (0x01, 0x0B, 0x1D, 0x1E, 0x2F, 0x36, 0x66, 0x68):
            self._send_no_wait(OP_GET_CONFIG, bytes([0x00, cid]))

    # ---- 底层发送 ----

    def _send_request(self, opcode: int, payload: bytes) -> RcspMessage:
        with self._send_lock:
            seq = self._next_seq()
            event = threading.Event()
            with self._pending_lock:
                self._pending[seq] = (opcode, event, None)
            try:
                self._write(RcspMessage(TYPE_PHONE_REQUEST, opcode, seq, payload))
                if not event.wait(self.RESPONSE_TIMEOUT):
                    raise TimeoutError(f"等待耳机响应超时（opcode 0x{opcode:02X}）")
                with self._pending_lock:
                    _, _, msg = self._pending[seq]
                if msg is None:
                    raise TimeoutError("pending cleared without result")
                return msg
            finally:
                with self._pending_lock:
                    self._pending.pop(seq, None)

    def _send_response(self, opcode: int, seq: int, payload: bytes) -> None:
        with self._send_lock:
            self._write(RcspMessage(TYPE_RESPONSE, opcode, seq, payload))

    def _send_no_wait(self, opcode: int, payload: bytes) -> None:
        def _run() -> None:
            if not self._connected:
                return
            with self._send_lock:
                seq = self._next_seq()
                self._write(RcspMessage(TYPE_PHONE_REQUEST, opcode, seq, payload))
        threading.Thread(target=_run, daemon=True).start()

    def _wait_inbound(self, opcode: int) -> RcspMessage:
        event = threading.Event()
        with self._pending_lock:
            key = self._next_seq_locked()
            self._pending_inbound[key] = (opcode, event)
        try:
            if not event.wait(self.RESPONSE_TIMEOUT):
                raise TimeoutError(f"等待耳机发起的鉴权确认超时（opcode 0x{opcode:02X}）")
        finally:
            with self._pending_lock:
                self._pending_inbound.pop(key, None)

    def _next_seq(self) -> int:
        with self._pending_lock:
            return self._next_seq_locked()

    def _next_seq_locked(self) -> int:
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return seq

    def _write(self, msg: RcspMessage) -> None:
        if not self._sock:
            raise RuntimeError("尚未连接")
        data = msg.encode()
        if self.on_log:
            self.on_log("TX", msg)
        try:
            self._sock.sendall(data)
        except (socket.timeout, OSError) as e:
            # 链路已死：立刻收摊，避免发送锁被永久占住
            self._running = False
            self._connected = False
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            raise ConnectionError(f"发送失败：{e}")

    # ---- 接收循环 ----

    def _read_loop(self) -> None:
        while self._running and self._sock:
            try:
                data = self._sock.recv(2048)
                if not data:
                    break
                self._parser.append(data)
                for msg in self._parser.drain():
                    if self.on_log:
                        self.on_log("RX", msg)
                    self._handle(msg)
            except socket.timeout:
                continue          # 空闲超时，连接还在
            except OSError:
                break
        if self.on_disconnected and self._running:
            self._connected = False
            self.on_disconnected("蓝牙连接已断开")

    def _handle(self, msg: RcspMessage) -> None:
        # 完成 pending 请求
        completed = False
        with self._pending_lock:
            for seq, (opcode, event, _) in list(self._pending.items()):
                if opcode != msg.opcode:
                    continue
                # response 只完成普通请求；request/notify 只完成 inbound 等待
                if msg.is_response:
                    # 普通请求匹配
                    self._pending[seq] = (opcode, event, msg)
                    event.set()
                    completed = True
                    break
        if not completed:
            with self._pending_lock:
                for key, (opcode, event) in list(self._pending_inbound.items()):
                    if opcode == msg.opcode and not msg.is_response:
                        event.set()
                        completed = True
                        break

        if msg.is_response:
            self._parse_response(msg)
        else:
            self._handle_inbound(msg)

    def _parse_response(self, msg: RcspMessage) -> None:
        if msg.opcode == OP_GET_DEVICE_INFO:
            self._parse_device_info(msg.payload)
        elif msg.opcode == OP_GET_RUN_INFO:
            self._parse_run_info(msg.payload)
        elif msg.opcode == OP_GET_CONFIG:
            if len(msg.payload) >= 3:
                cid = msg.payload[2]
                values = msg.payload[3:]
                if self.on_config:
                    self.on_config(cid, values)

    def _handle_inbound(self, msg: RcspMessage) -> None:
        if msg.opcode == OP_AUTH_CHALLENGE:
            if len(msg.payload) >= 16:
                challenge = msg.payload[-16:] if len(msg.payload) >= 17 else msg.payload
                response = self._safer.compute_challenge_response(challenge)
                self._send_response(OP_AUTH_CHALLENGE, msg.seq, bytes([0x01]) + response)
        elif msg.opcode == OP_AUTH_CONFIRM:
            self._send_response(OP_AUTH_CONFIRM, msg.seq, bytes([0x01]))
        elif msg.opcode == OP_REPORT_STATUS:
            self._parse_device_update(msg.payload)
            self._send_response(OP_REPORT_STATUS, msg.seq, b"")
        elif msg.opcode == OP_NOTIFY_CONFIG:
            self._parse_notify_config(msg.payload)
            self._send_response(OP_NOTIFY_CONFIG, msg.seq, b"")

    def _parse_device_info(self, payload: bytes) -> None:
        for idx, data in tlv_walk(payload, 1):
            if idx == 0x01 and len(data) >= 4:
                self.snapshot.firmware1 = _nibble_version(data[0], data[1])
                self.snapshot.firmware2 = _nibble_version(data[2], data[3])
            elif idx == 0x03 and len(data) >= 4:
                self.snapshot.vid_pid = f"VID: 0x{data[0]:02X}{data[1]:02X}, PID: 0x{data[2]:02X}{data[3]:02X}"
            elif idx == 0x07 and len(data) >= 3:
                self.snapshot.left = BatteryReading.from_byte(data[0])
                self.snapshot.right = BatteryReading.from_byte(data[1])
                self.snapshot.case = BatteryReading.from_byte(data[2])
        if self.on_snapshot:
            self.on_snapshot(self.snapshot)

    def _parse_run_info(self, payload: bytes) -> None:
        for idx, data in tlv_walk(payload, 1):
            if idx == 0x09 and len(data) >= 1:
                self.snapshot.anc_mode = data[0]
                if self.on_anc:
                    self.on_anc(data[0])

    def _parse_device_update(self, payload: bytes) -> None:
        for idx, data in tlv_walk(payload, 1):
            if idx == 0x00 and len(data) >= 3:
                self.snapshot.left = BatteryReading.from_byte(data[0])
                self.snapshot.right = BatteryReading.from_byte(data[1])
                self.snapshot.case = BatteryReading.from_byte(data[2])
                if self.on_snapshot:
                    self.on_snapshot(self.snapshot)
            elif idx == 0x04 and len(data) >= 1:
                self.snapshot.anc_mode = data[0]
                if self.on_anc:
                    self.on_anc(data[0])

    def _parse_notify_config(self, payload: bytes) -> None:
        for idx, data in tlv_walk(payload, 2):
            if idx == 0x0B and len(data) >= 2:
                mode = data[0]
                self.snapshot.anc_mode = mode
                if self.on_anc:
                    self.on_anc(mode)
                if self.on_config:
                    target = 0x01 if mode == ANC_NOISE_CANCELLING else 0x02
                    self.on_config(0x0B, bytes([target, data[1]]))
            else:
                if self.on_config:
                    self.on_config(idx, data)
