# MiBuds Controller Linux

小米 / 红米耳机（Redmi Buds 6 Lite 已验证）在 Linux 下的电量显示与降噪模式切换工具。
移植自 [FallenLeeee/MiBudsController](https://github.com/FallenLeeee/MiBudsController)（Windows WinUI 3）。

## 功能

- 系统托盘（StatusNotifier / AppIndicator）显示左右耳电量图标
- 弹出小面板：左耳 / 右耳 / 充电盒电量、降噪 / 通透 / 关闭 三键切换
- 断线自动重连，每 30 秒刷新电量
- GNOME / KDE / Xfce 通用（走 org.kde.StatusNotifierWatcher 标准）

## 依赖

全部系统自带（Ubuntu 26.10 验证）：

- `python3`
- `python3-gi` + GTK 3
- `gir1.2-ayatanaappindicator3-0.1`（托盘图标）
- `bluez`（蓝牙）
- `python3-dbus`（可选，探测用）

无需安装 .NET / Electron / 任何 pip 包。

## 使用

```bash
# 启动（托盘图标出现在顶栏/面板）
./mibuds.sh

# 面板启动即显示窗口（调试）
MIBUDS_SHOW_WINDOW=1 ./mibuds.sh

# 启动后自动循环切换降噪模式（调试协议）
MIBUDS_TEST_ANC=1 ./mibuds.sh
```

右键托盘图标 →「打开面板」「降噪模式」「刷新电量」「退出」。

## 开机自启

```bash
mkdir -p ~/.config/autostart
cp mibuds-controller.desktop ~/.config/autostart/
```

## CLI 探测

```bash
python3 probe.py              # 读电量、降噪状态、配置回读
python3 probe.py -v           # 打印协议帧
```

## 协议要点（Buds 6 Lite 实测）

- 控制服务：RFCOMM **channel 29**（SDP UUID `0000fd2d-...`）
- SAFER+ 鉴权：设备不响应 `0x50` 挑战，**跳过鉴权**仍可工作（与原项目注释一致）
- 电量：`0x02 GetDeviceInfo` 触发设备回 `0x0E ReportStatus`（TLV index 0x00 = 左/右/盒）
  - 也会在电量变化时主动推送
- 降噪模式：`0x08 SetAncMode` 下发，设备以 `0xF4 NotifyConfig`（config `0x0B`）回显确认
  - `0x01` = 降噪，`0x02` = 通透，`0x00` = 关闭
- **无读取模式的查询命令**（`0x09 GetRunInfo` 不响应）；初始状态未知，首次切换后才知道当前值
- 充电盒电量该型号不上报（`0xFF`）

## 文件

| 文件 | 说明 |
|---|---|
| `saferplus.py` | SAFER+ 128 位加密（移植自 C# `SaferPlus.cs`） |
| `rcsp.py` | RCSP 帧协议、TLV 解析、RFCOMM 客户端 |
| `probe.py` | CLI 探测工具 |
| `tray.py` | GTK 托盘 + 控制面板 |
| `mibuds.sh` | 启动器 |
| `mibuds-controller.desktop` | 自启动/应用菜单项 |

## 已知限制

- 蓝牙地址写死在 `tray.py` 的 `DEFAULT_ADDR`（可用 `~/.config/mibuds-linux/address` 覆盖）
- 同一时间只能一个程序占用 RFCOMM 通道（探针和托盘不要同时跑）
- 充电盒电量、固件版本、空间音频等该型号未上报 / 未验证

## 许可证

MIT（与原项目一致）

---

## 安装（打包版本）

### Debian / Ubuntu（.deb）

```bash
sudo apt install ./dist/mibuds-linux_1.0.0_all.deb
# 或
sudo dpkg -i dist/mibuds-linux_1.0.0_all.deb && sudo apt -f install
```

卸载：`sudo apt remove mibuds-linux`

### Fedora / RHEL / openSUSE（.rpm）

```bash
sudo rpm -ivh dist/mibuds-linux-1.0.0-1.noarch.rpm
# 或
sudo dnf install ./dist/mibuds-linux-1.0.0-1.noarch.rpm
```

卸载：`sudo rpm -e mibuds-linux`

### AppImage（免安装，双击即用）

```bash
chmod +x dist/MiBuds-1.0.0-x86_64.AppImage
./dist/MiBuds-1.0.0-x86_64.AppImage
```

依赖宿主机已装 BlueZ + GTK3 + AppIndicator（Ubuntu/Fedora 桌面默认都有）。


## 从源码运行

无需打包，直接：

```bash
./mibuds.sh
```

## 校验下载

```bash
cd dist && sha256sum -c SHA256SUMS
```
