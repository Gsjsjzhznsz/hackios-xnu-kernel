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

## 当前状态（2026-10 · R32 实机判决 + R33 GPU 常驻 + R34 GL 全栈真机验证（I1）+ R35 Metal 轮）

| 已工作 | 未工作 / 卡点 |
|---|---|
| 锁屏 → 桌面 → 应用（R28i 真机点亮 SpringBoard） | **窗口服务器 GL（GL=1）**：R35 送 libredwood-gl.dylib 进卷并注入 backboardd（gpu_draws() 门），真机判决待 J 树 |
| 触摸（Goodix GT9886，含冷启动重试梯）、电源/音量键 | Metal：发现链全栈已就位（R35：RedwoodMetal.bundle + metal=1 + metal_props/metal_test 上机），真机判决待 J 树 |
| 八核（`cpus=8`，Kryo 485） | 蜂窝、蓝牙 |
| **GPU 全管线真机打通（R32 设备判决）+ GL 用户态真机验证（R34 I1）**：step 12/19/21/22 全过 → gl_test 报 freedreno FD640 / GLES 3.2，三角 57 ms 画成、26% 像素计数与几何精确一致、上屏 | WiFi（raphael = WCN399x/ath10k-SNOC + ICNSS/MPSS：移植方案已立项，[docs/RAPHAEL-WIFI-PORT-PLAN.md](docs/RAPHAEL-WIFI-PORT-PLAN.md)，fork 资产 hakios.mpss/wifi/qmi 已确认可达，R36 MPSS 装载轮待开工） |
| 显示 + 亮度通道（IOMFB sel 50 → DSI DCS 0x51） | 完整充电路径（电池链与充电监视在，端到端未验证） |
| 电池服务（AppleSmartBattery）+ 5s 充电输入监视 | App Store / Apple ID（无 Secure Enclave） |
| USB ECM 网络（`ue0` 192.168.7.2） | |
| logdump 分区取证 + R26 flush 通道（可能复位的调用前整日志落盘） | |

**当前轮 R35（Metal 轮）**：R34 的设备判决（I1 成立，用户 logdump 入账）证明 GL 全栈真机打通——`gl_test: freedreno; FD640; OpenGL ES 3.2 Mesa 25.0.7`，三角 57 ms 画成/29 ms 读回/26% 像素精确，上屏 8 秒；同份 logdump 在案 `backboardd: CoreAnimation: Failed to initialize Metal or OpenGLES contexts!` = 系统级通道缺席，即本轮课题。R35 三件事：① **Metal 发现链上机**——RedwoodMetal.bundle（源码声明的 `_MTLDevice` 子类 RedwoodMetalDevice，资源层走 RedwoodGPU 的 msm 形接口）进卷 + `/System/Library/Extensions/` 链接 + cmdline 追加 `metal=1`（IOAcceleratorES 服务接受两个发现属性的唯一开关），`rc.jobs.r35metal` 依次 metal_props on → metal_test（五步）→ metal_drv_test --sweep（全类全方法）；② **GL=1 的 libredwood-gl.dylib 进卷**并经 inject.conf 注入 backboardd（gpu_draws() 门开 ⇒ CoreAnimation 经 EAGL→Mesa→freedreno→A640 画 UI 的通道）；③ **内核一字节未动**（R33 的 420f4b71… verbatim + CI 字节复现门禁），cmdline 恰好一个新 token——单变量纪律。判读树 J（J1 发现链全通 / J2 metal=1 门 / J3 bundle 缺位 / J4 类注册 / J5 backboardd 深层 / J6 daemon 循环 / J7 早期崩）见 [r35-springboard Release](https://github.com/Gsjsjzhznsz/hackios-xnu-kernel/releases) 的 `README.txt`。R34 段（GL 测试轮）的完整记述移至 git 历史与 [r34 Release](https://github.com/Gsjsjzhznsz/hackios-xnu-kernel/releases)。

**越狱路线**：本项目内核即越狱——kernel 由我们构建、注入式 dylib（libredwood*）已在 iOS 进程内跑；包管理生态（/var/jb + Procursus + Sileo）的落地路径见 [docs/JAILBREAK.md](docs/JAILBREAK.md)（fork 的 rc.jobs.jb/sileo 实证：WiFi 联网后 apt 可用）。

**参照系（用户指令）**：[HeliumStudio-Dev/hackios-xnu-kernel](https://github.com/HeliumStudio-Dev/hackios-xnu-kernel)（`raphael-bringup` 分支，同机型同 XNU 基座）——其日常 GPU 形就是裸 `gpu=1`（A640 常驻 + freedreno/FD640 GLES，真机三角绘制+回读无错、桌面/Safari 用户确认可用），WiFi 为 5 GHz WPA3 SAE 真机联网（其独立大移植：ath10k/WCN399x + ICNSS/MPSS/SNOC）。本项目 R33 对齐其 GPU 常驻形；R34 的 GL 栈即其已验证的同一路线（上游 Mesa/redwood-drm→RedwoodGPU）；WiFi 移植方案见 [docs/RAPHAEL-WIFI-PORT-PLAN.md](docs/RAPHAEL-WIFI-PORT-PLAN.md)。

## 快速开始（手机端，四步）

需要：解锁 bootloader 的 Redmi K20 Pro、`fastboot`、Release 里的两个文件（镜像 + 启动卷）。**不写手机闪存。**

```sh
fastboot boot  raphael-xnu-r35-gpu-flash.img   # R35 Metal 轮（发现链判决在系统安静后跑）
fastboot oem  ramdisk
fastboot stage raphael-r35-flashboot.apfs      # iOS 系统卷 → RAM（一次 loader 会话一次）
fastboot oem  run                              # 等 SpringBoard（约 1-2 分钟）+ rwtouch wait 22 后的五行判决
```

- 控制组/回滚：`raphael-xnu-r35-flash.img`（= r34 日常镜像逐字节原样，无 GPU 步骤）或 r34 GPU 镜像
- ⚠️ 启动卷**只 stage 这一个文件**：md1 备份不要 stage（镜像已内嵌全量小卷——R14 事故纪律）
- ⚠️ userdata 上的 iOS 卷是唯一存货：绝不要让 MIUI 开机（Android 会重格回 f2fs，iOS 卷即毁）
- 每次开机都要重复四步（会话住 RAM 是"不写闪存"设计的一部分）

## 构建（CI 一键）

Actions → `R35 · SpringBoard 一键流水线` → Run workflow（默认参数即可）。流水线做的事：

1. 工具链钉死（L1ghtmann v19.0.0-L2 具名 tag + 每轮全新缓存键、无 restore 链——R28i 对 lsd SIGBUS 构建漂移的定罪回应）
2. 从本树源码构建内核并断言字节复现（sha == R33 的 420f4b71…——R35 不动内核，差一字节即环境漂移/误改，硬门禁）
3. GL+Metal 构建（userland pin de5ef58 + Mesa 25.0.7 → GL 三件 + libredwood-gl.dylib + RedwoodMetal.bundle + metal_props/metal_test/metal_drv_test，八件全部 Mach-O 断言）
4. 卷构建（提取 R34 谱系卷 → GL/Metal 增量 + Extensions 链接 + inject.conf +1 行注入 → mkapfs 重建 → selftest 往返 + 81 文件逐字节证明）
5. 外科手术：日常镜像 = r34 逐字节原样；GPU 镜像换 ramdisk 条目 = GL+Metal 卷 + cmdline 追加 metal=1（装载器 v7 / kernel / DTB 逐字节原样，SHA 硬校验）
6. 发布 Release（双镜像 + 启动卷 + 卷 + 卷清单 manifest + SHA256SUMS + README 判读树 + 工具链审计表）

本地构建：`xnu/apply.sh` → `xnu-deps/setup.sh` → `xnu-build.sh -k`（详见 `xnu/README.md`）；GL+Metal 件 `tools/r35_gl_metal_build.sh`；卷 `tools/r35_volume_build.py`；打包 `tools/r35_flash_build.py`；离线血统判定 `tools/r35-kernel-id.py <image> --expect-volume-sha <sha>`。

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
