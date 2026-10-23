Name:           mibuds-linux
Version:        1.0.0
Release:        1%{?dist}
Summary:        Xiaomi/Redmi earbuds battery and ANC controller for Linux
License:        MIT
URL:            https://github.com/FallenLeeee/MiBudsController
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
Requires:       python3, python3-gtk3, python3-gobject, bluez

%description
StatusNotifier tray app showing left/right/case battery and switching
noise-cancelling modes (ANC / transparency / off) on Xiaomi and Redmi
earbuds over Bluetooth RFCOMM (RCSP protocol).
Ported from the Windows MiBudsController project.

%prep
# 手动解包（避免依赖 /usr/bin/rpmuncompress）
rm -rf mibuds-linux-1.0.0
tar -xzf %{SOURCE0}
cd mibuds-linux-1.0.0

%build
# nothing to build (pure Python)

%install
cd mibuds-linux-1.0.0
rm -rf %{buildroot}
mkdir -p %{buildroot}/opt/mibuds-linux
mkdir -p %{buildroot}/usr/bin
mkdir -p %{buildroot}/usr/share/applications
mkdir -p %{buildroot}/usr/share/doc/mibuds-linux

install -m 644 tray.py rcsp.py saferplus.py probe.py %{buildroot}/opt/mibuds-linux/
install -m 755 mibuds.sh %{buildroot}/opt/mibuds-linux/
install -m 755 mibuds %{buildroot}/usr/bin/mibuds
install -m 644 mibuds-controller.desktop %{buildroot}/usr/share/applications/
install -m 644 README.md %{buildroot}/usr/share/doc/mibuds-linux/

%files
/opt/mibuds-linux/
/usr/bin/mibuds
/usr/share/applications/mibuds-controller.desktop
/usr/share/doc/mibuds-linux/README.md

%changelog
* Fri Oct 23 2026 oh_zhou <oh_zhou@localhost> - 1.0.0-1
- Initial package
