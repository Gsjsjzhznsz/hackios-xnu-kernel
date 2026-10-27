# 越狱方案（本项目语境）

问题："还有有没有什么越狱方案？"——分两层回答：**本项目（raphael 跑我们自己的 XNU）**与**一般 iPhone**。

## 一、本项目：内核即越狱（已经在越狱状态）

这台 K20 Pro 上的 iOS 不是 Apple 签名的链路在跑——每一层都是我们自己的：

| 层 | 状态 | 说明 |
|---|---|---|
| 内核 | **我们的构建** | XNU 由本树源码 + 钉死工具链产出；没有 Secure Enclave、没有激活锁（logdump 里 `mobileactivationd` 的 lookup 失败即此） |
| 代码签名/AMFI | **不构成障碍** | 系统卷上的原版 iOS 二进制能跑、`libredwood*.dylib` 注入进 backboardd/SpringBoard/mediaserverd 等 Apple 进程内执行（R33 logdump 的 `redwood: window server hooks installed` 就是注入生效的实机证据）——上游移植内核已处理签名强制 |
| root | **有** | LaunchDaemons 的 job 以 root 跑（`/redwood/sbin/init` 拉起全系统） |
| tweak 加载器 | **在卷里** | `libredwood-tweaks.dylib`（`src/inject_tweaks.c`）+ `inject.conf` 机制——注入机器已在运转 |

所以"越狱"在本项目里不是"找一个漏洞利用"，而是**把包管理生态搬进来**。上游用户态已经带了全套（userland 仓库 `tools/mk-jb.sh`、`tools/jb-preinstall.py`、`rc.jobs.jb/jbreg/sileo`，build.sh 的 `JB=1` 镜像模式）：

1. **mk-jb.sh**：拉取 Procursus rootless bootstrap（发行版 1800，dpkg 1.22.6 / apt 2.9.4，iOS 15+）+ Sileo 2.5.1 的 arm64 .deb，解到 `root/var/jb`，**补回 setuid/setgid 位**（login/su/sudo 与 Sileo 的 `giveMeRoot` 4755——普通用户解包会丢位，这是 fork 实测过的坑）。
2. **JB=1 镜像**：`root/var/jb` 进系统镜像（只读 `/jb`），init 每次开机把它拷到 `/private/var/jb`——因为 `/` 是只读 APFS、`/private/var` 是每次开机新建的内存盘，**rootless 前缀是唯一合身形态**；装的东西不跨重启（fork 原话："the phone is yours for the session"）。
3. **rc.jobs.jb**（开机 job）：`prep.sh`（bootstrap 的一次性准备）→ `net_test waitaddr en0 60 resolve apt.procurs.us`（**等 WiFi 有地址 + DNS 能解析**）→ `apt-get update`——之后 Sileo/命令行装包都通。
4. **rc.jobs.jbreg**：本地 .deb 离线安装 + `uicache` 注册进 LaunchServices（fork 用 TrollStore helper 演示过）——**WiFi 前的离线路径**。
5. **rc.jobs.sileo**：全自动 Sileo 流（uiopen `sileo://package/...` + rwtouch 点按 + dpkg-query 验收）。

**依赖顺序**：R34（GL 测试轮）→ R35-R38（WiFi，见 [RAPHAEL-WIFI-PORT-PLAN.md](RAPHAEL-WIFI-PORT-PLAN.md)）→ **R39 候选 = JB 轮**（`JB=1` + `rc.jobs.jb`，apt 直连 Procursus；无 WiFi 的离线先导可用 jbreg 装本地 deb）。

## 二、一般 iPhone（如果问题问的是这个）

与本机无关，仅作澄清：A9-A11（iPhone 5s-X）有 checkra1n / palera1n（bootrom/checkm8，不可修复）；iOS 15-16.2 有 Dopamine/palera1n rootless；TrollStore 走 CoreTrust 漏洞装机不越内核。本机的 iOS 卷**不受这些工具影响也不需要它们**——内核是我们编译的，"越狱"在构建那一刻就完成了。

## 三、纪律与边界

- ⚠️ Apple 版权内容（iOS 系统卷、Sileo 镜像内文件）仅个人研究；不分发。
- `/private/var` 内存盘：装的包不跨重启；fork 的持久化方案（userdata 归档槽）是另一条线，不在本方案内。
- 越狱轮同样走"每轮一个变量 + 判读树预注册 + logdump P3 纪律"——`JB=1` 与 WiFi 的耦合点只在 `rc.jobs.jb` 的 `net_test waitaddr`（WiFi 未就绪时 apt 步骤超时，job 应继续到可判读的失败行）。
