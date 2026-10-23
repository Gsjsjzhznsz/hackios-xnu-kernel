# FLASH-R11 — 驱动轮：触摸、按键、GPU、调光（内核第一次从源码构建）

R10 交出了干净的桌面。R11 让你能**摸**它：触摸（绝对优先）、物理按键（急需）、
GPU 加速（实验镜像）、软件调光。四件驱动全部编译进内核——所以这一轮的内核
**第一次从本仓库源码构建**（xnu/apply.sh + xnu-build.sh），R9 的门铃补丁也从
二进制进了源码（`GIC_INTID_VTIMER 27→19`，一个宏，四处生效）。

内核之外一个字节没动：loader = R10 0.10 原样，shim/FD/DTB = r1/r2 验证字节，
iOS 卷 = R5-R10 同卷。

## 四件驱动

### ① 触摸（`touchscreen=1`，主镜像默认开）—— SWEET 赛道的验证移植

`bsd/dev/raphael_touch.c` + `gt9886_cfg.h`：Goodix GT9886（"normandy"）挂在
GENI **SE19**（i2c@c88000）上，地址 0x5d。这不是新写的驱动——是 ios-raphael
（SWEET 赛道）`sweet_touch.c` 的 raphael 路径移植，**那条上电时序在同一台手机
上被 t13 真机验证过**（上电成功后会话存活数千行日志）：

```
TLMM 供电轨（GPIO98=3v3、100=1v8）→ 冷上电循环 → SDA/SCL 复用到 qup19
（GPIO57/58）→ 复位时序（GPIO54，低 2ms→高 300ms）→ 读 PID（0x4535，
期望 "9886"）→ 按 sensor id（0x4541）选 784B 面板配置下发（0x80/0x82/0x83
握手，vendor blob verbatim）→ 防 doze（0x30f0/0x3100）→ 0x4100 轮询 10ms
```

事件走 `/dev/redwood-touch`（坐标归一化 0..65535；sensor0 双轴翻转按 DT），
backboardd 侧的用户态桥（启动卷里现成）把它们变成 IOHID 事件——redwood 手机
上同一条链路驱动的是它的 Goodix。

开机后 **+5 秒**触摸上电（`touchdelay=` 可调；`touchflip=1` 再翻 180°）。

### ② 按键（`buttons=1`，主镜像默认开）—— 只读，不信任直到见过释放

`bsd/dev/raphael_buttons.c`：电源键（PM8150 PON 的 KPDPWR_N）、音量下
（同一 PON 的 RESIN_N）、音量上（PM8150 GPIO6，主线 DTS 实锤
`gpios = <&pm8150_gpios 6 GPIO_ACTIVE_LOW>`）。SPMI 仲裁器（V5，地址与
redwood 同款——SM8150 保持同一几何）observer 只读，60Hz 轮询，PON 自带
消抖。位选择按 PON subtype 自适应（classic=bit0/bit1，gen3=bit7/bit6），
step 4 打印原始状态字节——照片裁决。三键映射 iPhone X（电源=侧键
REDWOOD_BUTTON_LOCK）。

**这驱动只读**：不写 PMIC 任何寄存器（t17-t19 的教训：写错寄存器可触发
RPM/TZ 热复位）。没见过释放的键不报告（防引脚误判变成长按）。

### ③ GPU（`gpu=1`，**实验镜像**才开）—— Adreno 640 二十二步梯子

`bsd/dev/raphael_adreno.c`：redwood 的 Adreno 642L 驱动（4125 行，每步先
宣布再动手、读先于写、找不到就停的梯子原型）移植到 SM8150 的 Adreno 640：

| 差异点 | redwood（sc7280/a635） | raphael（sm8150/a640） | 出处 |
|---|---|---|---|
| 块基址 | gpu 0x3d00000/gmu 0x3d6a000/gpucc 0x3d90000/smmu 0x3da0000 | gpu 0x2c00000/gmu 0x2c6a000/gpucc 0x2c90000/smmu 0x2ca0000 | 主线 sm8150.dtsi |
| cmd-db | 0x80860000 | **0x85f20000** | 主线 sm8150.dtsi |
| GMU 固件 | a660_gmu.bin（vendor） | **a640_gmu.bin**（linux-firmware，块表格式，已解码验证） | a6xx_catalog.c |
| SQE | a660_sqe.fw | **a630_sqe.fw** | a6xx_catalog.c |
| zap | a660_zap.mdt + .b02 | **a640_zap.mbn**（单文件 ELF32，hash 段 + PT_LOAD 段都在文件内；linux-firmware 通用 sm8150 版） | linux-firmware + mdt_loader.c |
| DTCM 基址 | 0x10004000（a650 系） | **0x40000** | a6xx_gmu.c |
| GPU RSC | 外部块 0x3de0000 | **GMU 窗口内 +0x23000** | a6xx_gmu.c |
| PDC | AOP 代劳（两个使能寄存器） | **完整 uCode 加载**（PDC 序列 + TCS1/TCS3 命令组 + a630 家族 RSC 序列，sync 0x30090） | a6xx_gmu.c |
| GEN2 池 | 固件 NEEDS 块自带 | a640_gmu.bin **无 NEEDS 块**——驱动按 Linux 的 icache/dcache 位置（0x4000/0x44000，各 0x3f000）建池 | a6xx_gmu.c + 固件解码 |
| FAL10 接口 | 写 | **不写**（a650 系专属） | a6xx_gmu.c |
| 频率表 | 315/450/550 | **345/427/585 @ RPMH 64/80/128** | sm8150 gpu_opp_table |

固件三件（86KB，sha256 在 `fw-gpu-raphael/README.md`）随内核树入库，CI 塞进
小镜像的 fw/。**zap 是 linux-firmware 的通用 sm8150 版**——如果 TZ 拒收它的
签名（step 12 的判词会直说），换 raphael 自己 vendor 的同名文件即可。

### ④ 背光（软件调光）

`fbdim=10..100`（boot-arg，默认 100=不动）在帧缓冲缩放路径上乘一个系数；
`/dev/redwood-touch` 写 type-6 记录（x=百分比）运行时可调。AMOLED 的真背光
走 DSI/DCS（0x51），内核还没驱动 DSI 主机——软件调光先行，AMOLED 少发光
就是真省电。R12+ 再上 DCS。

## 刷写（主镜像：触摸 + 按键）

```
fastboot boot  raphael-xnu-r11-flash.img
fastboot oem  ramdisk
fastboot stage raphael-r11-flashboot.apfs
fastboot oem  run        # 黑屏 → 桌面 → +5s 触摸上电
```

rootfs（userdata）不用重刷（与 R5-R10 同卷）。

## 判读树

**A. 主镜像**（触摸 + 按键）：
1. 桌面出来，+5s 后 klog 应有 `RAPHAEL TOUCH: starting in 5 s` →
   `bus idle ...` → `bus live ...` → `GT9886 pid err=0 [39 38 38 36]` →
   `cfg send sensorN 784B err=0` → `selected GT9886 normandy`。
   **手指滑动生效 = 触摸全胜**（锁屏上滑解锁、桌面翻页）。
2. 触摸卡在哪一行，那一行就是判决：
   - 卡 `SE19 already busy` / `need ABL-loaded I2C firmware`：ABL 没留 GENI
     固件——下轮 loader 先复位 SE。
   - `pid is not 9886`：读到了但 ID 不对（总线活着，地址/协议问题）。
   - probe 行全 NACK（err=5/6）：轨没上电或 SDA/SCL 没复用——`bus idle` 的
     sda/scl 读值直接裁决（全 0 = 轨问题，0x2xx = 复用问题）。
3. 按键：`buttons: PMIC arbiter version 5xxxx, N channels` → `step 5:
   watching power volume-up volume-down` → 按电源/音量键出 `PRESSED/released`。
   三键的可用性独立判定（`usable` 列表），某键 "left alone" 有原因行。
4. **开机 5 秒内 USB 已被接管**——klog 拿法：趁 `oem run` 刚回、iOS 还没接管
   USB 的窗口（或早期卡死时），`fastboot oem log && fastboot get_staged
   r11-klog.txt`。成功启动后触摸/按键的判定**看行为**（滑动/按键直接生效）
   不再看日志。
5. 桌面时钟走 + widget 动画（CPU 软渲染）流畅度与 R10 同级。

**B. GPU 实验镜像**（第二刷，`gpu=1`）：
1. klog（或早期失败时的 oem log）里 `adreno: 1:` 到 `adreno: 22:` 每步一行。
   **走到 22 = 全通**：`TRUSTZONE HAS AUTHENTICATED AND STARTED THE ZAP
   SHADER` → `THE GMU'S FIRMWARE RUNS` → `THE GMU'S MESSAGE QUEUES ARE UP`
   → 桌面动画变成 GPU 合成（丝滑）。
2. 卡在第 N 步：那一行 + 前后两行回传——梯子每步自带判词（"which says which
   step and what it found"）。**预期焦点步**：12（zap 签名——通用版若被 TZ
   拒收，换 vendor 版）、14/15（GMU 固件/启动——a640_gmu.bin 的块表）、
   16（PDC——sm8150 没人代劳的完整加载）、17（HFI 表——a640 固件的表格式）。
3. GPU 镜像若整机冻死（hypervisor 不喜欢的访问）：**这就是实验镜像单独一发
   的原因**——主镜像的胜利不受影响；回传冻死前最后一步号。
4. `adreno=N` boot-arg 可走前 N 步（下轮诊断用；本轮两镜像已按全量配置）。

**C. 共同的兜底**（R9/R10 的工具全在）：checkpoint 天梯 `oem run 15..21`、
`oem irqtest`、`oem gic`、SGI 针刺——早期卡死时按序取证。

## 回退链

- **R10 镜像**（`raphael-xnu-r10-flash.img`）：干净桌面、无驱动——本轮的
  "行为基线"。
- **R9 镜像**：verbose 版（-v 控制台），看屏幕卡点用。
- 源码构建内核若行为异常（R5-R10 都是 prebuilt）：R10 镜像即 prebuilt 基线，
  一刷即回。

## R12 观察清单（回复时带上）

| # | 观察 | 回什么 |
|---|------|--------|
| D1 | 触摸 | 滑动解锁/翻页/长按是否生效？边缘手势（底部上滑回主屏）跟手吗？ |
| D2 | 按键 | 电源键息屏/唤醒？音量键出 HUD？长按电源出关机滑块？ |
| D3 | GPU 镜像 | adreno 走到第几步？22 全通后动画流畅度对比？ |
| D4 | 桌面时钟 | 时间在走吗？（= 时钟中断持续交付） |
| D5 | 异常 | 任何花屏/冻结/重启时刻 + 前因（在干什么） |
