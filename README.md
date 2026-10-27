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

## 当前状态（2026-10 · R32 实机判决 + R33 GPU 常驻落地 + R34 GL 测试轮）

| 已工作 | 未工作 / 卡点 |
|---|---|
| 锁屏 → 桌面 → 应用（R28i 真机点亮 SpringBoard） | **窗口服务器 GL（GL=1）**：R34 把 Mesa/FD640 的 libOSMesa + gl_test 送进卷（GL=test：三角上屏验全栈），窗口服务器切换是下一轮 |
| 触摸（Goodix GT9886，含冷启动重试梯）、电源/音量键 | Metal（编译器在，无 Metal 驱动） |
| 八核（`cpus=8`，Kryo 485） | 蜂窝、蓝牙 |
| **GPU 全管线真机打通（R32 设备判决，logdump 入账）**：step 12 TZ 认签 → step 19 zap 认证+起动 → step 21 3D 核按命令写内存 → step 22 GPU 画的画面上屏 | WiFi（raphael = WCN399x/ath10k-SNOC + ICNSS/MPSS：移植方案已立项，[docs/RAPHAEL-WIFI-PORT-PLAN.md](docs/RAPHAEL-WIFI-PORT-PLAN.md)，参照 HeliumStudio raphael-bringup 的独立大移植） |
| 显示 + 亮度通道（IOMFB sel 50 → DSI DCS 0x51） | 完整充电路径（电池链与充电监视在，端到端未验证） |
| 电池服务（AppleSmartBattery）+ 5s 充电输入监视 | App Store / Apple ID（无 Secure Enclave） |
| USB ECM 网络（`ue0` 192.168.7.2） | |
| logdump 分区取证 + R26 flush 通道（可能复位的调用前整日志落盘） | |

**当前轮 R34（GL 测试轮）**：R33 的设备判决（H1 成立）证明 GPU 常驻落地——steps 1..22 全过后 `"THE GPU STAYS ON for the system to draw with: the service RedwoodGPU"`（585 MHz，温度/频率在案），系统软件渲染。R34 送 GL 用户态上机：userland `build.sh GL=test` 的产出（Mesa 25.0.7 libOSMesa.8.dylib = softpipe+freedreno、gl_test、u2f_test）注入 md1 卷，`etc/rc.jobs` 换成 `rc.jobs.gltest`——gl_test 经 OSMesa→freedreno(FD640)→内核 RedwoodGPU 服务画一个三角，读回像素并放面板 8 秒。**内核一字节未动（R33 的 420f4b71… verbatim + CI 字节复现门禁），cmdline 一个 token 未动——唯一变量 = 卷**。手术方式：提取 R13 谱系卷（只读 apfs.py）→ 增量注入 → mkapfs 重建 → 逐文件往返证明（79 文件 byte-identical + 3 增量 + 1 换装）。判读树 I（I1 全通 / I2 softpipe / I3 编译链接 / I4 无上屏服务 / I5 回滚）见 [r34-springboard Release](https://github.com/Gsjsjzhznsz/hackios-xnu-kernel/releases) 的 `README.txt`。

**越狱路线**：本项目内核即越狱——kernel 由我们构建、注入式 dylib（libredwood*）已在 iOS 进程内跑；包管理生态（/var/jb + Procursus + Sileo）的落地路径见 [docs/JAILBREAK.md](docs/JAILBREAK.md)（fork 的 rc.jobs.jb/sileo 实证：WiFi 联网后 apt 可用）。

**参照系（用户指令）**：[HeliumStudio-Dev/hackios-xnu-kernel](https://github.com/HeliumStudio-Dev/hackios-xnu-kernel)（`raphael-bringup` 分支，同机型同 XNU 基座）——其日常 GPU 形就是裸 `gpu=1`（A640 常驻 + freedreno/FD640 GLES，真机三角绘制+回读无错、桌面/Safari 用户确认可用），WiFi 为 5 GHz WPA3 SAE 真机联网（其独立大移植：ath10k/WCN399x + ICNSS/MPSS/SNOC）。本项目 R33 对齐其 GPU 常驻形；R34 的 GL 栈即其已验证的同一路线（上游 Mesa/redwood-drm→RedwoodGPU）；WiFi 移植方案见 [docs/RAPHAEL-WIFI-PORT-PLAN.md](docs/RAPHAEL-WIFI-PORT-PLAN.md)。

## 快速开始（手机端，四步）

需要：解锁 bootloader 的 Redmi K20 Pro、`fastboot`、Release 里的两个文件（镜像 + 启动卷）。**不写手机闪存。**

```sh
fastboot boot  raphael-xnu-r34-gpu-flash.img   # R34 GL 轮（三角在锁屏稳定后上屏 8 秒）
fastboot oem  ramdisk
fastboot stage raphael-r34-flashboot.apfs      # iOS 系统卷 → RAM（一次 loader 会话一次）
fastboot oem  run                              # 等 SpringBoard（约 1-2 分钟）+ gl_test 判决行
```

- 控制组/回滚：`raphael-xnu-r34-flash.img`（= r33 日常镜像逐字节原样，无 GPU 步骤）或 r33 GPU 镜像
- ⚠️ 启动卷**只 stage 这一个文件**：md1 备份不要 stage（镜像已内嵌全量小卷——R14 事故纪律）
- ⚠️ userdata 上的 iOS 卷是唯一存货：绝不要让 MIUI 开机（Android 会重格回 f2fs，iOS 卷即毁）
- 每次开机都要重复四步（会话住 RAM 是"不写闪存"设计的一部分）

## 构建（CI 一键）

Actions → `R34 · SpringBoard 一键流水线` → Run workflow（默认参数即可）。流水线做的事：

1. 工具链钉死（L1ghtmann v19.0.0-L2 具名 tag + 每轮全新缓存键、无 restore 链——R28i 对 lsd SIGBUS 构建漂移的定罪回应）
2. 从本树源码构建内核并断言字节复现（sha == R33 的 420f4b71…——R34 不动内核，差一字节即环境漂移/误改，硬门禁）
3. GL 构建（userland pin aba1e81 + Mesa 25.0.7 softpipe,freedreno → libOSMesa.8.dylib + gl_test + u2f_test）
4. 卷构建（提取 R13 谱系卷 → GL 增量注入 → mkapfs 重建 → selftest 往返 + 79 文件逐字节证明）
5. 外科手术：日常镜像 = r33 逐字节原样；GPU 镜像只换 ramdisk 条目 = GL 卷（装载器 v7 / kernel / cmdline / DTB 逐字节原样，SHA 硬校验）
6. 发布 Release（双镜像 + 启动卷 + GL 卷 + 卷清单 manifest + SHA256SUMS + README 判读树 + 工具链审计表）

本地构建：`xnu/apply.sh` → `xnu-deps/setup.sh` → `xnu-build.sh -k`（详见 `xnu/README.md`）；GL 件 `tools/r34_gl_build.sh`；卷 `tools/r34_volume_build.py`；打包 `tools/r34_flash_build.py`；离线血统判定 `tools/r34-kernel-id.py <image> --expect-volume-sha <sha>`。

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
