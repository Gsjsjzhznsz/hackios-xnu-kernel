# HackiOS · 小米 Redmi K20 Pro（raphael）XNU 内核移植

**iOS 16 用户态 + XNU（Darwin 22.5）内核，运行于小米 Redmi K20 Pro（代号 `raphael`，骁龙 855 / SM8150）。**

> 本仓库是 [oosexgang/hackios-xnu-kernel](https://github.com/oosexgang/hackios-xnu-kernel) 的 **raphael 机型适配线**（fork）：
> 把 HackiOS 从 POCO X5 Pro（redwood）带到 Redmi K20 Pro 的机型分支。
> 中文为默认语言，English section follows below.
>
> 姊妹仓库：[Gsjsjzhznsz/ios-raphael](https://github.com/Gsjsjzhznsz/ios-raphael)（t 系早期移植线，已并入本仓库的 R 系）。

## 项目简介

HackiOS 让 Apple 开源的 XNU 内核在 Android 手机硬件上启动，并在其上运行**未修改的 iOS 16.5 用户态**（launchd → backboardd → SpringBoard → 应用）。本仓库把这条路线带到了 Redmi K20 Pro：

- **启动链**：手机自己的 XBL/ABL（UEFI）→ 我们的 `xnuloader`（看护装载器，v7）→ XNU 内核（内嵌 raphael 驱动树：触摸/按键/显示/电池/USB）
- **会话式研究台，不是 ROM**：系统卷住在 RAM、不写手机闪存（logdump 取证分区除外），重启即回 Android
- **每轮一个变量**：R 系轮次（R28d → R28i → R28j…）用外科手术式打包——底板镜像只换内核条目，装载器/卷/cmdline 逐字节原样，机器可证明

## 当前状态（2026-10 · R28 系列）

| 已工作 | 未工作 / 卡点 |
|---|---|
| 锁屏 → 桌面 → 应用（R28i 真机点亮 SpringBoard） | **GPU zap shader**：TZ secboot 拒绝（`-3145797`，签名/身份阶段；T2 已证 XNU→Hyp→TZ 链路无罪 → T3 逆向中） |
| 触摸（Goodix GT9886，含冷启动重试梯）、电源/音量键 | Metal / GPU 硬件加速（等 zap 之墙） |
| 八核（`cpus=8`，Kryo 485） | 蜂窝、蓝牙 |
| 显示 + 亮度通道（IOMFB sel 50 → DSI DCS 0x51） | 完整充电路径（电池链与充电监视在，端到端未验证） |
| 电池服务（AppleSmartBattery）+ 5s 充电输入监视 | App Store / Apple ID（无 Secure Enclave） |
| USB ECM 网络（`ue0` 192.168.7.2） | |
| logdump 分区取证 + R26 flush 通道（可能复位的调用前整日志落盘） | |

**当前轮 R28j（同 boot 序贯对照）**：`adsp 尝试 → give-up → zap 尝试 → give-up`——R28i 的 T2 判决 `ACCEPTED (0)` 证明链路无罪，但 zap 的 `-3145797` 出自另一次 boot；R28j 在同一次 boot 里背靠背完成两次尝试，把对照闭环。判读树 J1–J5 与 logdump 读取纪律见 [r28j-springboard Release](https://github.com/Gsjsjzhznsz/hackios-xnu-kernel/releases) 的 `README.txt`。

**下一步（T3）**：离线逆向 secapp `cmd 0x49` 的拒绝分支——为什么 TZ 拒绝本机原厂签名的 stock zap（信任根已证同为 Xiaomi Root CA 链）。

## 快速开始（手机端，四步）

需要：解锁 bootloader 的 Redmi K20 Pro、`fastboot`、Release 里的两个文件（镜像 + 启动卷）。**不写手机闪存。**

```sh
fastboot boot  raphael-xnu-r28j-flash.img      # 或当前 Release 的最新日常镜像
fastboot oem  ramdisk
fastboot stage raphael-r28j-flashboot.apfs     # iOS 系统卷 → RAM（一次 loader 会话一次）
fastboot oem  run                              # 等 SpringBoard（约 1-2 分钟）
```

- 想跑 GPU/序贯轮：把第一行的镜像换成 `-gpu-flash.img`（`gpu=1` 自动执行 adreno 步骤）
- ⚠️ 启动卷**只 stage 这一个文件**：md1 备份不要 stage（镜像已内嵌全量小卷——R14 事故纪律）
- ⚠️ userdata 上的 iOS 卷是唯一存货：绝不要让 MIUI 开机（Android 会重格回 f2fs，iOS 卷即毁）
- 每次开机都要重复四步（会话住 RAM 是"不写闪存"设计的一部分）

## 构建（CI 一键）

Actions → `R28j · SpringBoard 一键流水线` → Run workflow（默认参数即可）。流水线做的事：

1. 工具链钉死（L1ghtmann v19.0.0-L2 具名 tag + 每轮全新缓存键、无 restore 链——R28i 对 lsd SIGBUS 构建漂移的定罪回应）
2. 从本树源码构建内核（clean build，ccache 零回填），跑 R28j marker 门禁（判决串/证据串逐条计数 + 内嵌 adsp `.mdt` 逐字节证明）
3. 对 R28d 底板镜像做外科手术：**只换内核条目**，装载器 v7 / ramdisk / cmdline / DTB 逐字节原样（SHA 硬校验）
4. 发布 Release（双镜像 + 启动卷 + SHA256SUMS + README 判读树 + 工具链审计表）

本地构建：`xnu/apply.sh` → `xnu-deps/setup.sh` → `xnu-build.sh -k`（详见 `xnu/README.md`）；打包 `tools/r28j-flash-build.sh`；离线血统判定 `tools/r28j-kernel-id.py <image>`。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `xnu/files/bsd/dev/raphael_adreno.c` | raphael GPU/TrustZone 探测树（step 1–22，本项目的核心实验场） |
| `xnu/files/bsd/dev/raphael_adsp_fw.h` | 内嵌的 adsp `.mdt`（T2 对照的体积来源，零卷变量） |
| `xnuloader/` | 看护装载器（USB `xl` 命令族、logdump 落盘、`oem run/ramdisk/logdump`） |
| `.github/workflows/r2X-*.yml` | 各轮一键流水线（R6–R28j 全保留，轮次即历史） |
| `tools/r2X-*.py/.sh` | 各轮的血统判定与外科手术打包工具 |
| `docs/WORKLOG.md` | 项目工作日志（全轮次证据链） |
| `docs/FLASH-R*.md` | 各轮刷机与判读指南 |
| `common/`、`qemu/`、`prebuilt/` | 上游继承的通用构建/模拟/预编译件 |

## 上游与致谢

- **[oosexgang/hackios-xnu-kernel](https://github.com/oosexgang/hackios-xnu-kernel)** —— HackiOS 主线（本仓库的上游）
- **[oosexgang/hackios-xnu-userland](https://github.com/oosexgang/hackios-xnu-userland)** —— 用户态（init、注入库、APFS 镜像构建）
- **[dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet)** —— t 系（ios-raphael 线）的上游
- **[apple-oss-distributions/xnu](https://github.com/apple-oss-distributions/xnu)** —— Apple 的 XNU 源码

## 法律

**两个仓库都不含任何 Apple 文件。** `ios165/fetch.sh`（用户态仓库）从 Apple 自己的 CDN 下载 iOS 16.5 IPSW 到*你的*机器并在那里解包。HackiOS 是独立研究项目，与 Apple Inc. 无隶属、未获授权、无背书。iOS、iPhone、Apple 是 Apple Inc. 的商标。本项目仅限在**自有设备**上做授权调试研究。

---

# HackiOS · XNU on the Xiaomi Redmi K20 Pro (raphael)

**The iOS 16 userland + the XNU kernel (Darwin 22.5) running on the Xiaomi Redmi K20 Pro (`raphael`, Snapdragon 855 / SM8150).**

This repository is the **raphael device line** of [oosexgang/hackios-xnu-kernel](https://github.com/oosexgang/hackios-xnu-kernel).

- **Boot chain**: the phone's own XBL/ABL (UEFI) → our `xnuloader` (watching loader, v7) → XNU with the raphael driver tree (touch / buttons / display / battery / USB) built in.
- **A research bench, not a ROM**: the system volume lives in RAM, nothing is written to the phone's flash (except the `logdump` forensics partition), and a reboot returns you to Android.
- **One variable per round**: R-series rounds (R28d → R28i → R28j…) are packed surgically — only the kernel entry of the device-validated base image is replaced; loader, volume and cmdline stay byte-identical, provably.

**Status (2026-10)**: SpringBoard, touch (Goodix GT9886), buttons, 8 cores, display + brightness, battery service, USB ECM networking, and the R26 flush-to-storage forensics channel all work. The current wall is the **GPU zap shader**: TrustZone refuses it with `-3145797` (signature/identity stage) even though the T2 control proved the XNU→Hyp→TZ chain innocent (`ACCEPTED (0)`). **R28j** runs the adsp control and the zap attempt back to back in one boot; **T3** (offline reversing of the secapp's `cmd 0x49` refusal branch) is next.

**Quick start** (unlocked bootloader + `fastboot`, nothing is flashed):

```sh
fastboot boot  raphael-xnu-r28j-flash.img
fastboot oem  ramdisk
fastboot stage raphael-r28j-flashboot.apfs
fastboot oem  run
```

See the [releases](https://github.com/Gsjsjzhznsz/hackios-xnu-kernel/releases) for the current round's images, the J1–J5 verdict tree, and the logdump reading discipline. The workflow (`R28j · SpringBoard 一键流水线`) builds the round from source with a pinned toolchain, gates every generation marker, and publishes the surgically packed images.

**Legal**: no Apple files are in either repository; the IPSW is fetched from Apple's own CDN onto your machine. HackiOS is an independent research project, not affiliated with or endorsed by Apple Inc. Authorized debugging on your own devices only.
