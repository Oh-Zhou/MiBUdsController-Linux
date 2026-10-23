# MiBuds Controller Linux

小米 / 红米耳机在 Linux 下的电量显示与降噪模式切换工具。
移植自 [FallenLeeee/MiBudsController](https://github.com/FallenLeeee/MiBudsController)（Windows WinUI 3）。

## 功能

- 系统托盘显示左右耳电量图标
- 点击弹出面板：左耳 / 右耳 / 充电盒电量、降噪 / 通透 / 关闭 三键切换
- 断线自动重连，每 30 秒刷新电量
- GNOME / KDE / Xfce 通用

## 安装

从 [Releases](../../releases) 下载对应安装包：

| 安装包 | 适用 |
|---|---|
| `mibuds-linux_1.0.0_all.deb` | Debian / Ubuntu |
| `mibuds-linux-1.0.0-1.noarch.rpm` | Fedora / RHEL / openSUSE |
| `MiBuds-1.0.0-x86_64.AppImage` | 任意发行版，免安装 |

```bash
# Debian / Ubuntu
sudo apt install ./mibuds-linux_1.0.0_all.deb

# Fedora / RHEL / openSUSE
sudo dnf install ./mibuds-linux-1.0.0-1.noarch.rpm

# AppImage（免安装）
chmod +x MiBuds-1.0.0-x86_64.AppImage
./MiBuds-1.0.0-x86_64.AppImage
```

安装后从应用菜单启动「MiBuds 耳机控制」，或终端运行 `mibuds`。

从源码运行：

```bash
./mibuds.sh
```

> **GNOME 用户**：GNOME 默认无托盘区，需安装扩展：`sudo apt install gnome-shell-extension-ubuntu-appindicators`，然后注销重登或在「扩展」里启用 Ubuntu AppIndicators。KDE / Xfce 无需额外操作。

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
3. 右键图标 →「打开面板」「降噪模式」「刷新电量」「退出」
4. 面板内直接点 降噪 / 通透 / 关闭 切换模式

开机自启：

```bash
mkdir -p ~/.config/autostart
cp mibuds-controller.desktop ~/.config/autostart/
```

## 协议要点（Redmi Buds 6 Lite 实测）

- 控制服务：RFCOMM **channel 29**（SDP UUID `0000fd2d-...`）
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

- 蓝牙地址默认写死在 `tray.py` 的 `DEFAULT_ADDR`，可用 `~/.config/mibuds-linux/address` 覆盖
- 同一时间只能一个程序占用 RFCOMM 通道（探针和托盘不要同时跑）
- 充电盒电量、固件版本、空间音频等该型号未上报 / 未验证
- 不保证与未来固件兼容

## 许可证

MIT（与原项目一致）
