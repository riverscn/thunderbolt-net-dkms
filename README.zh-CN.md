# thunderbolt-net-dkms

[English](README.md)

这是 Linux `thunderbolt_net` 的实验性 DKMS 驱动包。它针对对端发来的超大
TCP 包，先验证数据与校验和，再补充保守的 GSO 分段信息；本机仍接收聚合大包，
转发时由 Linux 出口按需分段。

本项目是独立维护的实验，不代表 Linux、Apple、Intel 或任何发行版的官方修复。
它不处理所有雷电枚举、热插拔、休眠、DHCP 或 TSO 问题。

## 当前状态

- 驱动基于 Linux v7.0，保留原始作者与许可证声明。
- 0.1.1 修正 GRO 的以太网头长度识别，改善同一 TCP 流的收包顺序；
  这项修正不依赖 `rx_segment` 参数，加载本版本后始终生效。
- 实机测试为 Linux 7.0、macOS 开启 TSO、MTU 1500。
- 三组交替短测中，本机 IPv4 吞吐与原驱动相差约 2% 以内。
- 验证了实际 IPv4 Docker 转发及隔离内核的 IPv4/IPv6 网桥、路由路径。
- 纯二层桥接的实机验收和长期稳定性验证仍有待完成。
- 超大 TCP 包的 RX 规范化仍默认关闭，使用 `rx_segment=1` 显式启用。
- 首版面向 x86-64；内核范围和测试层次见 [兼容性](docs/compatibility.md)。

当前版本作为开发基线保存。下一阶段计划更新到明确固定的上游最新稳定版驱动，
集中维护旧内核兼容层，并增加 Linux 7.2 验证与 Arch 打包。
这些工作**尚未在 0.1.1 中实现**，具体范围见 [开发路线](docs/roadmap.md)。

## 安装

**必须先安装前置依赖。** Debian / Ubuntu 使用发行版内核时：

```sh
sudo apt update
sudo apt install build-essential dkms curl ca-certificates coreutils kmod ethtool \
  "linux-headers-$(uname -r)"
```

Proxmox VE 宿主机使用 PVE 内核时，改用以下命令：

```sh
sudo apt update
sudo apt install build-essential dkms curl ca-certificates coreutils kmod ethtool \
  "proxmox-headers-$(uname -r)"
```

已经是 root 时省略 `sudo`。以上安装编译工具、DKMS、内核头文件，以及下载、
校验和检查驱动所需工具。DKMS 必须不低于 3.0.10；头文件必须与当前
`uname -r` 完全匹配，PVE 的头文件不能用通用 Debian / Ubuntu 头文件替代。
依赖检查方法见 [安装前置条件](docs/installation.md#install-prerequisites-first)。

下载 release 包并通过 `SHA256SUMS` 校验后，再安装驱动：

```sh
sudo apt install ./thunderbolt-net-dkms_0.1.1-1_all.deb
dkms status -m thunderbolt-net
modinfo -n thunderbolt_net
```

`.deb` 包内是源码，由 DKMS 在目标机器上编译，不需要替换整个 Linux 内核。
安装过程不请求重载正在使用的网卡。`modinfo` 显示的是磁盘上的模块，不能单靠
它确认内存中正在运行的模块已经切换。

阅读 [安装与回退说明](docs/installation.md) 后，将示例配置安装到
`/etc/modprobe.d/thunderbolt-net-rx.conf`，下次加载模块时生效。只在控制台或独立
管理链路上重载 `thunderbolt_net`，因为这会中断雷电连接。

## 构建与发布

```sh
make check
make KERNELRELEASE="$(uname -r)"
dpkg-buildpackage --build=binary --no-sign
make dist
```

GitHub CI 包含源码隐私检查、三个发行版的编译、隔离 QEMU 内核测试，以及
Debian 安装/卸载/恢复原驱动验证。通过后生成 `.deb`、源码归档和 SHA-256 校验。
推送与 `VERSION` 一致的版本标签时，发布实验性 prerelease。

公开仓库不包含开发环境原始日志、主机名、局域网地址、密钥或预编译模块。
测试地址均使用文档专用网段。提交问题前仍请自行检查诊断内容，参见
[隐私说明](docs/privacy.md)。

维护者：Shun Li <riverscn@gmail.com>。许可证：GPL-2.0-only。
