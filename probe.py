#!/usr/bin/env python3
"""MiBuds Linux 探测：连接 fd2d 控制服务，SAFER+ 鉴权，读取电量与降噪状态。"""
import sys
import time
import argparse

sys.path.insert(0, "/home/oh_zhou/dsh/mibuds-linux")
from rcsp import EarbudsClient, ANC_MODE_NAMES, BatteryReading


def fmt_battery(b: BatteryReading | None) -> str:
    if b is None:
        return "—"
    level = b.level if b.level is not None else "?"
    return f"{level}%{'⚡' if b.charging else ''}"


def main() -> int:
    ap = argparse.ArgumentParser(description="MiBuds Linux 探测")
    ap.add_argument("--addr", default="D0:C1:BF:7D:7C:82", help="蓝牙地址")
    ap.add_argument("--channel", type=int, default=29, help="RFCOMM 通道（fd2d 默认 29）")
    ap.add_argument("--timeout", type=float, default=6.0, help="读取超时秒数")
    ap.add_argument("--verbose", "-v", action="store_true", help="打印协议帧")
    args = ap.parse_args()

    client = EarbudsClient(args.addr, args.channel)
    if args.verbose:
        def on_log(direction, msg):
            print(f"  {direction} type=0x{msg.msg_type:02X} opcode=0x{msg.opcode:02X} "
                  f"seq={msg.seq} payload={msg.payload.hex()}")
        client.on_log = on_log

    state = {"snapshot": None, "anc": None, "configs": {}, "disconnected": None}
    client.on_snapshot = lambda snap: state.__setitem__("snapshot", snap)
    client.on_anc = lambda mode: state.__setitem__("anc", mode)
    client.on_config = lambda cid, vals: state["configs"].__setitem__(cid, vals)
    client.on_disconnected = lambda reason: state.__setitem__("disconnected", reason)

    print(f"[*] 连接 {args.addr} RFCOMM ch{args.channel} …")
    try:
        client.connect()
    except Exception as e:
        print(f"[✗] 连接失败: {e}")
        return 1
    print("[✓] 已连接")

    # 等待状态同步回包
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        snap = state["snapshot"]
        if snap and (snap.left or snap.right or snap.firmware1) and state["anc"] is not None:
            break
        time.sleep(0.2)

    snap = state["snapshot"]
    if snap:
        print(f"\n── 设备状态 ──")
        print(f"  左耳: {fmt_battery(snap.left)}   右耳: {fmt_battery(snap.right)}   充电盒: {fmt_battery(snap.case)}")
        if snap.firmware1:
            print(f"  固件: {snap.firmware1} / {snap.firmware2 or '?'}")
        if snap.vid_pid:
            print(f"  {snap.vid_pid}")
    else:
        print("[✗] 未收到设备信息")

    anc = state["anc"]
    if anc is not None:
        print(f"  降噪模式: {ANC_MODE_NAMES.get(anc, f'未知(0x{anc:02X})')}")
    else:
        print("[!] 未收到降噪状态（可能需等待或固件不支持）")

    if state["configs"]:
        print(f"\n── 配置回读 ──")
        for cid, vals in sorted(state["configs"].items()):
            print(f"  0x{cid:02X}: {vals.hex()}")

    if state["disconnected"]:
        print(f"\n[!] 断开: {state['disconnected']}")

    client.disconnect()
    print("\n[完成]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
