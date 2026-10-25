# MiBuds Controller Linux

小米 / 红米耳机在 Linux 下的电量显示与降噪模式切换工具。
移植自 [FallenLeeee/MiBudsController](https://github.com/FallenLeeee/MiBudsController)（Windows WinUI 3）。

## 功能

- 系统托盘显示左右耳电量图标
- 点击弹出面板：左耳 / 右耳 / 充电盒电量、降噪 / 通透 / 关闭 三键切换
- 面板内一键开关开机自启（GNOME 风格拨动开关）
- 托盘菜单「检查更新」对比 GitHub 最新版本，有新版本可一键前往下载
- 断线自动重连，每 30 秒刷新电量
- GNOME / KDE / Xfce 通用

## 安装

从 [Releases](../../releases) 下载对应安装包：

| 安装包 | 适用 |
|---|---|
| `MiBuds-1.0.1-all.deb` | Debian / Ubuntu（任意 CPU 架构） |
| `MiBuds-1.0.1-noarch.rpm` | Fedora / RHEL / openSUSE（任意 CPU 架构） |
| `MiBuds-1.0.1-x86_64.AppImage` | Intel / AMD 64 位，免安装 |
| `MiBuds-1.0.1-aarch64.AppImage` | ARM 64 位（树莓派、飞腾等），免安装 |

```bash
# Debian / Ubuntu
sudo apt install ./MiBuds-1.0.1-all.deb

# Fedora / RHEL / openSUSE
sudo dnf install ./MiBuds-1.0.1-noarch.rpm

# AppImage（免安装，按 CPU 架构选择）
chmod +x MiBuds-1.0.1-x86_64.AppImage   # 或 aarch64
./MiBuds-1.0.1-x86_64.AppImage
```

安装后从应用菜单启动「MiBuds 耳机控制」，或终端运行 `mibuds`。

从源码运行：

```bash
./mibuds.sh
```

## 兼容性

**桌面环境**（走 StatusNotifier / AppIndicator 标准接口）：

| 桌面 | 托盘支持 |
|---|---|
| GNOME | 需扩展：`gnome-shell-extension-ubuntu-appindicators`（Ubuntu 自带），装完注销重登 |
| KDE Plasma | 原生支持，无需配置 |
| Xfce | 需 `xfce4-statusnotifier-plugin`（面板右键 → 添加新项目） |
| Cinnamon / MATE / Budgie / LXQt | 原生支持 |
| Sway / Hyprland | waybar 的 `tray` 模块 |
| i3 / dwm / bspwm | 无内置托盘区，需 `stalonetray` 之类的外挂 |

**发行版**：只要 GTK3 + BlueZ + Python3 齐全即可，依赖包名对照：

| 依赖 | Debian/Ubuntu | Fedora | Arch |
|---|---|---|---|
| GTK3 绑定 | `python3-gi` `gir1.2-gtk-3.0` | `python3-gobject` `gtk3` | `python-gobject` `gtk3` |
| 托盘库 | `gir1.2-ayatanaappindicator3-0.1` | `libayatana-appindicator-gtk3` | `libayatana-appindicator` |
| 蓝牙 | `bluez` | `bluez` | `bluez` |

> 程序会自动识别 `AyatanaAppIndicator3` 和旧版 `AppIndicator3`，两种库名都能跑。

**耳机型号**：地址会自动从已配对设备里找（名字含 redmi / xiaomi / buds），控制通道优先用 SDP 查询、查不到再逐个探测，所以同系列其它型号大概率也能用。协议本身是小米通用的 RCSP，但**只在 Redmi Buds 6 Lite 上实测过**。

## 依赖

安装包已声明依赖，`apt` / `dnf` 会自动装齐。手动从源码运行只需：

- Python 3
- GTK 3（`python3-gi`）
- 托盘图标（`gir1.2-ayatanaappindicator3-0.1`）
- 蓝牙（`bluez`）

均为发行版自带组件，无需 .NET / Electron / 任何 pip 包。

## 使用

1. 系统蓝牙设置中连接耳机
2. 启动应用，托盘出现电池图标
3. 右键图标 →「打开面板」「降噪模式」「开机自启」「刷新电量」「检查更新」「退出」
4. 面板内直接点 降噪 / 通透 / 关闭 切换模式

开机自启：面板里「开机自启」右侧的开关拨到开即可；托盘菜单里也有同样的勾选项，两边同步。

## 协议要点（Redmi Buds 6 Lite 实测）

- 控制服务：RFCOMM 通道（SDP UUID `0000fd2d-...`），Buds 6 Lite 实测为 **29**；程序自动发现
- SAFER+ 鉴权：设备不响应 `0x50` 挑战，**跳过鉴权**仍可工作（与原项目注释一致）
- 电量：`0x02 GetDeviceInfo` 触发设备回 `0x0E ReportStatus`（TLV index 0x00 = 左/右/盒），电量变化时也主动推送
- 降噪模式：`0x08 SetAncMode` 下发，设备以 `0xF4 NotifyConfig`（config `0x0B`）回显确认
  - `0x01` = 降噪，`0x02` = 通透，`0x00` = 关闭
- **无读取模式的查询命令**（`0x09 GetRunInfo` 不响应）；初始状态未知，首次切换后才知道当前值
- 充电盒电量该型号不上报（`0xFF`）

CLI 探测工具：

```bash
python3 probe.py        # 读电量、降噪状态
python3 probe.py -v     # 打印协议帧
```

## 文件结构

| 文件 | 说明 |
|---|---|
| `tray.py` | GTK 托盘 + 控制面板 |
| `rcsp.py` | RCSP 帧协议、TLV 解析、RFCOMM 客户端 |
| `saferplus.py` | SAFER+ 128 位加密（移植自 C# `SaferPlus.cs`） |
| `probe.py` | CLI 探测工具 |
| `mibuds.sh` | 启动器 |
| `mibuds-controller.desktop` | 应用菜单 / 自启动项 |

## 已知限制

- 蓝牙地址自动从已配对设备推断，识别不到时回退到内置默认值；也可用 `~/.config/mibuds-linux/address` 手动指定
- 同一时间只能一个程序占用 RFCOMM 通道（探针和托盘不要同时跑）
- 充电盒电量、固件版本、空间音频等该型号未上报 / 未验证
- 不保证与未来固件兼容

## 许可证

MIT（与原项目一致）
