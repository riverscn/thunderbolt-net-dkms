# thunderbolt-net-dkms

[English](README.md)

这是 Linux `thunderbolt_net` 的实验性 DKMS 驱动包。它针对对端发来的超大
TCP 包，先验证数据与校验和，再补充保守的 GSO 分段信息；本机仍接收聚合大包，
转发时由 Linux 出口按需分段。

本项目是独立维护的实验，不代表 Linux、Apple、Intel 或任何发行版的官方修复。
它不处理所有雷电枚举、热插拔、休眠、DHCP 或 TSO 问题。

## 开发分支：跟踪上游

本分支将驱动基线更新到 Linux 7.2.9，保留现有 RX 修复，增加可复核的上游
更新流程与固定版本内核 CI。它不包含自动 MTU 实验，尚未发布。
参见[维护流程](docs/upstream-tracking.md)与[兼容性范围](docs/compatibility.md)。

尚未证实这个分支比 0.2.0 有实机性能收益：128µs 是旧控制器驱动已有的节流值；
TX E2E 调整主要修复部分控制器的发送停滞，RX 统计修正也不等于吞吐提升。
合并前建议完成同条件的吞吐、重传、CPU/中断、延迟、桥接和重连回归，详见
[性能预期与验证](docs/upstream-tracking.md#performance-expectations-and-validation)。

## 已发布的 0.2.0 版本

本次迭代增加可选的 RX 页面回收，串行化接收启动与停止流程，并回移三项上游
连接清理修复。保留 0.1.1 的 GRO 顺序修正和保守的超大 TCP 包分段信息。

- `rx_page_pool=0`、`rx_segment=0` 仍为默认值，两项功能分别启用。
- 上游驱动基线仍为 Linux v7.0；本次不增加 Linux 7.2 支持或 Arch 打包。
- 设计见 [RX 页面回收](docs/rx-page-pool.md)，实测结果和覆盖范围见
  [当前验证报告](docs/validation.md)，内核范围见 [兼容性](docs/compatibility.md)。

首次物理重连在观察窗口内超时，手动重试后已恢复连接和双向传输。重连的可重复性
仍待验证；两次观察均记录在 0.2.0 验证报告中，不能作为本分支的实机验证结果。

软件包仍为实验性项目。重新加载时应保留独立管理通路；需要完整恢复原行为时
[回退到 0.1.1](docs/installation.md#return-to-the-011-baseline)。本 PR 不代表稳定版发布。

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
sudo apt install ./thunderbolt-net-dkms_0.2.0-1_all.deb
dkms status -m thunderbolt-net
modinfo -n thunderbolt_net
```

`.deb` 包内是源码，由 DKMS 在目标机器上编译，不需要替换整个 Linux 内核。
安装过程不请求重载正在使用的网卡。`modinfo` 显示的是磁盘上的模块，不能单靠
它确认内存中正在运行的模块已经切换。

两个可选配置文件可以同时放在 `/etc/modprobe.d/` 中：

| 文件 | 参数 | 用途 |
| --- | --- | --- |
| `thunderbolt-net-rx.conf` | `rx_segment=1 rx_segment_mtu=1500` | macOS TSO 转发修正 |
| `thunderbolt-net-page-pool.conf` | `rx_page_pool=1` | RX 页面回收优化 |

它们都配置 `thunderbolt_net`，`modprobe` 会合并不同的参数，第二个文件不会覆盖
第一个。可以分别启用；如需同时启用，阅读 [安装与回退说明](docs/installation.md) 后执行：

```sh
sudo install -m 644 \
  /usr/share/doc/thunderbolt-net-dkms/examples/thunderbolt-net-rx.conf.example \
  /etc/modprobe.d/thunderbolt-net-rx.conf
sudo install -m 644 \
  /usr/share/doc/thunderbolt-net-dkms/examples/thunderbolt-net-page-pool.conf.example \
  /etc/modprobe.d/thunderbolt-net-page-pool.conf
```

以上示例由 `.deb` 安装；源码仓库中也可以在 `packaging/` 找到。两个文件一起使用
等价于在一个配置文件中写入：

```conf
options thunderbolt_net rx_segment=1 rx_segment_mtu=1500 rx_page_pool=1
```

选择一种布局即可，避免重复或冲突地设置同一参数。分文件便于单独关闭页面回收。
配置在下次加载模块时生效；若涉及 initramfs，应先更新它。只在控制台或独立管理
链路上重载，因为这会中断雷电连接。重载或重启后检查实际参数：

```sh
cat /sys/module/thunderbolt_net/parameters/rx_segment
cat /sys/module/thunderbolt_net/parameters/rx_segment_mtu
cat /sys/module/thunderbolt_net/parameters/rx_page_pool
```

同时启用两个示例时，结果应依次为 `Y`、`1500`、`Y`。

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
