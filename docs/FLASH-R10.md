# FLASH-R10 — 清屏轮：把桌面还给 SpringBoard

R9 的门铃补丁（vtimer INTID 27→19）让时钟中断回来了，内核一路活到底：launchd
全程跑通，**SpringBoard 的桌面图标在 `-v` 控制台日志底下成功渲染**。iOS 用户态
起来之后，loader 的 fastboot 随 USB 被接管而哑火——终端卡死不再是故障，是
**完全启动的签名**。R9 那张"画花的桌面"就是全链打通的现场照片。

R10 只做一件事：把这块桌面擦干净。

## 改动（三处，全部在 cmdline / 交接面；内核一字节不动）

| # | 改动 | 机制 |
|---|------|------|
| 1 | payload cmdline 去掉 `-v` | `boot_args.video.v_display` 由 0 变 **1（图形模式）**——内核与 launchd 不再往屏幕画字，这正是非 verbose iPhone 的标准开机方式。framebuffer 交接与 `-v` 无关（`v_baseAddr/rowBytes/width/height/depth` 恒定传入），UI 渲染路径不受影响 |
| 2 | loader 基础 cmdline 去掉 `debug=0x8 ` 前缀 | raphael 没有 UART，这个位从来只喂屏幕上的控制台叠加。心跳计数（`kernel_live.heartbeat`）与内核日志环（loader 直读共享内存）**都不经过 console**——失败日的取证能力一件不丢 |
| 3 | 交接前面板清成纯黑（`screen_clear()`） | loader 的 `print()` 走 UEFI 控制台，上屏。有 `-v` 时这些字会被内核控制台滚走；去掉 `-v` 后，loader 的遗言（还有固件的）会在屏上**冻住整段 launchd 启动**直到 SpringBoard 首帧。现在交接前直接填黑 + dcache clean |

- **内核**：与 R9 字节完全相同（`tools/r9-kernel-patch.py`，门铃 27→19，全文件
  仅 4 字 6 字节差异）。shim/FD/DTB 仍为 r1/r2 验证字节。iOS 卷与 R5-R9 相同。
- **loader 0.10 的其余部分** = R9 原样：`oem irqtest`、`oem gic/sgi/peek/gicset/
  gicgroup`、group repair 兜底、checkpoint 天梯、hung 自动判读，全部保留。

## 刷写

```
fastboot boot  raphael-xnu-r10-flash.img
fastboot oem  ramdisk
fastboot stage raphael-r10-flashboot.apfs
fastboot oem  run
```

rootfs（userdata）不用重刷。然后就是等：黑屏一阵（launchd 在跑）→ 桌面出现。

## 判读树

1. **黑屏 20-60 秒 → 干净的苹果桌面出现**（无任何日志叠加）= **R10 全胜**。
   随后 fastboot 卡死/哑火 = iOS 接管 USB = 成功签名，拔线拍照留念即可。
2. **黑屏超过 2 分钟无桌面**：
   - fastboot 若还活着（机器没走到 USB 接管那步）→ 取证套餐照旧：
     `oem log && get_staged r10-klog.txt`、`oem irqtest && oem gic`、
     checkpoint 天梯 `oem run 15..21`（每条 fresh boot）
   - 或直接回退 **R9 镜像**（`raphael-xnu-r9-flash.img`，带 `-v debug=0x8` 的
     verbose 版）看屏幕上卡在哪一行——R9 就是 R10 的调试镜像，二者仅差这三处
     cmdline/清屏改动。
3. **桌面出现但有异样**（花屏/撕裂/残影/偏色）→ 拍照，走下面观察清单 C。

## 触摸 / 背光 / 显示刷新——R11 路线图（本轮不做，先说清楚）

上游 hackios 在 POCO X5 Pro（redwood）上**触摸是全通的**：Goodix GT9916
（Berlin-D）走 SoC 串行引擎 13 的 SPI，轮询线程 125 Hz，事件进
`/dev/redwood-touch`，用户态守护进程喂给 backboardd——锁屏滑动、手势、
multitouch 全部在列（见 README "What works"）。但那套驱动的每一个地址都是
redwood 的（sc7280 的 TLMM/GCC/PMIC、SE13 SPI 帧法），而 **raphael 的触摸是
Goodix GT9886 挂 GENI I2C（SE7，地址 0x5d）**——I2C 与 SPI 在 GENI 上是两套
帧法，上电时序、引脚、时钟、供电轨也全不同。直接传 `touchscreen=1` 会拿
redwood 的地址戳 sm8150 的寄存器，等于在整机上乱点穴（t17-t19 的教训：
戳错寄存器可触发 RPM/TZ 热复位）。

所以触摸是一个**内核重编轮**（R11）：
1. CI 里首次启用 `xnu-build.sh` 工具链，先验证"重编无改动内核"行为等价
   （门铃补丁仍可字节锚定应用）；
2. 把 `redwood_goodix.c` 的 SPI 帧法换成 GENI **I2C** 帧法，地址换成 raphael
   的（SE7 基址、TLMM 引脚、GCC 时钟、供电轨——I2C 上电序列在同一台机器上
   已被 ios-raphael 赛道真机验证过：SE7 GENI I2C、0x5d、轮询 10ms、doze
   disable，上电成功后会话存活数千行日志）；
3. 事件仍进 `/dev/redwood-touch`，用户态链路现成（启动卷里已有）。

背光（AMOLED，DCS/PWM 走显示管线）与 GPU 加速合成（redwood 用 Adreno +
freedreno 跑 CoreAnimation，`gpu=1`）同理——都需要内核侧新代码，全部排在
R11+，按本轮观察清单的实际痛点排优先级。

## R11 观察清单（对清屏后的桌面，回复时带上）

| # | 观察 | 回什么 |
|---|------|--------|
| C1 | 亮度 | 当前固定亮度观感：过亮 / 合适 / 过暗？ |
| C2 | 动画 | 时钟/天气 widget 的秒针或数字跳动、图标的进场动画是否流畅？有无撕裂、花屏、残影？ |
| C3 | 色彩 | 壁纸与图标颜色是否正常？（当前声明 sRGB——P3 会让 WebKit 渲染黑屏，是刻意保守） |
| C4 | 触摸 | 无功能（预期内），确认"看得到摸不到"的现状即可 |
| C5 | 时钟 | 桌面时钟的时间是否在走？（= 时钟中断持续交付的直接证据） |

## 回退

- 调试/verbose：**R9 镜像**（`raphael-xnu-r9-flash.img`）——cmdline 带
  `-v debug=0x8`、不清屏，与本轮唯一差别就是那三处。
- 更早的回退链不变：R8（loader 0.8）→ R7（探针修复）→ R6（分诊）。
- boot-arg 级回退（若需要 verbose 但不想换镜像）：内核/启动链不支持运行时
  改 cmdline，直接用 R9 镜像即可。
