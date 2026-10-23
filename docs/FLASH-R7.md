# FLASH-R7 — 修好眼睛的一轮：GIC 全量取证 + checkpoint 天梯

R6 的结论要重写。两件事定了案：

1. **盘没问题**（`ufs: userdata: an APFS container is there`，NXSB@+32 + 两处非零指纹）。
2. **R6 探针自己是瞎的**。`GICR_TYPER` 的亲和值在 bits **[63:32]**（Aff3.Aff2.Aff1.Aff0，与 MPIDR 同字节序；内核自己的 `gic_find_redistributor()` 读 `>> 32` 是对的），R6 的宏读 `>> 40`——**任何核都匹配不上任何帧**。所以 `no redistribulator frame says it belongs to core 700` 不是硬件真相，是探针 bug。stock DTB（本仓库 golden `prebuilt/raphael/raphael.dtb` 与 TWRP 备份的 base dtb 完全一致）证实拓扑就是 **0x000, 0x100, …, 0x700（8 个单核 cluster）**——loader 的 `core << 8` 编号**一直是对的**，`bootcpu=7` = 0x700 是真实存在的 prime 核，R5 的 `the firmware answers 0`（CPU_ON 成功）也是真话。

重读 R5/R6 日志，真相是：**内核活着走到了 EARLY_BOOT**。`heartbeat=1` 是 `hv_wdt_pet_init()`（`STARTUP(EARLY_BOOT, RANK_LAST)`）同步跑的第一次 pet；之后每 10 秒一次的 pet 要靠时钟中断驱动的 thread_call——**它再也没来**。`loader_log` 正常引导期间本来就不接收 console 输出（只在 return/panic 时灌入 msgbuf），所以 "0 字节" 从来不是"console 之前死了"的证据。内核没有 panic（panic 会走 loader_return 回到 loader），它是**活着等一个永远不来的 tick**，或**屏蔽着中断自旋**。

冻结窗口因此收窄到 **checkpoint 15（`kernel_startup_bootstrap` 之后，心跳=1 证明已过）与 21（arm_init 结束）之间**，头号嫌疑就是 `gic_v3_irq_cpu_init()`（checkpoint 16 `cpu_init` 调它）——它内部有两个无超时的自旋，而且指纹互不相同：

```c
/* gic_v3_irq.c: 嫌疑① WAKER 等待 —— PPI 27 还没使能 */
while (gic_read32(gicr + GICR_WAKER) & GICR_WAKER_CHILDASLEEP) { }

/* gic_v3_irq.c: 嫌疑② GICD RWP 等待 —— PPI 27 已使能之后；只在固件没设 G1NS|ARE 时才进入 */
if ((ctlr & (G1NS|ARE)) != (G1NS|ARE)) {
        gic_write32(gicd + GICD_CTLR, ctlr | G1NS | ARE);
        while (gic_read32(gicd + GICD_CTLR) & RWP) { }
}
```

嫌疑②的触发条件是"UEFI 留下的 GICD_CTLR 缺 G1NS/ARE 位"。redwood 用上游预编译 UEFI（位大概率已设好，内核**从不进**这个等待），raphael 用本机 XBL 自建 UEFI——这可能就是"同代码 redwood 活、raphael 死"的差异点。R7 每次开机打印这条基线，一眼定案。

## R7 loader（0.7）内容

| 命令 | 干什么 |
|---|---|
| `oem gic` | **修好 + 全量**：8 帧 TYPER 普查（aff/cpuid/LAST）、GICD G1NS/ARE/RWP、内核核的 WAKER + SGI/PPI group/enable/pending/active + 判读行。HUNG 时自动打印。 |
| `oem sgi N` | **带回读**：发出后读帧寄存器，回答 SGI 下落（从未 pending / pending 未取 / 被取）。 |
| `oem gicset` | **H1 手动实验**：开机后、`oem run` 前执行，替内核写 G1NS\|ARE 并等 RWP（有超时+判读），让内核的 bring-up 变成空操作。 |
| 开机自动 | **基线行**：`gic: distributor ctlr <hex> as the firmware left it: G1NS _ ARE _ RWP _`。 |

内核 / shim / FD / DTB 仍是 r1/r2/r4 硬件验证过的字节。`oem run [N]` 的 checkpoint 天梯（**内核自带，R6 起就有**，R7 文档化）：`oem run 15` 应当能回（心跳=1 已证明过了 15）；第一个**不回**的 N，冻结就在 N-1 与 N 之间。16=cpu_init 后，17=arm_vm_init 后，18=serial 后，19=kprintf 后，20=console 后，21=arm_init 完。

## 测试顺序（R7 出包后）

```sh
fastboot boot  raphael-xnu-r7-flash.img
fastboot oem  ramdisk
fastboot stage raphael-r7-flashboot.apfs
fastboot oem  run                 # 卡死就等 ~60s 的自动 HUNG dump
fastboot oem log && fastboot get_staged r7-klog.txt
```

卡死态追加：`fastboot oem sgi 1`（看回读行）→ `fastboot oem gic`。
H1 实验需要 fresh boot：`fastboot boot …` → `oem ramdisk` → `stage …` → **`fastboot oem gicset`** → `fastboot oem run`。

**等 CI 的三个小时里**，现有 R6 镜像就能跑天梯（每个 N 一次 fresh boot，四步照常，把 `oem run` 换成 `oem run N`）：`15 → 16 → 17 → 18 → 19 → 20 → 21`。

## 判读树（R7 实测样本，宿主机仿真输出）

**基线行（每次开机）：**
```
gic: distributor ctlr 12 as the firmware left it: G1NS 1 ARE 1 RWP 0     → 嫌疑②排除
gic: distributor ctlr 0 as the firmware left it: G1NS 0 ARE 0 RWP 0      → 内核必须自己写并等 RWP → H1 概率大增，做 gicset 实验
```

**H1 —— GICD RWP 卡死（PPI 27 已使能，RWP 永不清零）：**
```
gic: distributor ctlr 80000000: G1NS 0 ARE 0 RWP 1
gic: the distributor's RWP is STUCK: a write is settling forever --
    the kernel's gic bring-up waits exactly there, right after enabling PPI 27
gic: core 700: ... enabled 8000000 pending 0 ...
```
→ `oem gicset` 实验：若 gicset 后 run 活过 21 号 checkpoint，**当场定案 + 当场绕过**；R8 把该写入做成 loader 默认（或内核侧加超时）。

**H2 —— RD 唤醒卡死（PPI 27 未使能）：**
```
gic: core 700: frame 17b40000: waker 4 (PROCSLEEP 0 CHILDASLEEP 1)
gic: this redistributor never woke: the kernel's gic bring-up spins in its GICR_WAKER wait, ...
```
→ R8：内核侧 WAKER 等待加超时（需重编内核，`xnu-build.sh` 管线就绪）。

**H3 —— PPI 27 没使能但 WAKER 也正常：**
```
gic: the kernel's clock interrupt is NOT enabled on that core: it froze before
    finishing its gic bring-up (between kernel checkpoints 15 and 16 -- oem run 15 stops there and comes back)
```
→ 天梯 `oem run 15` 复核；若 15 能回 16 不能回，冻结就在 `cpu_init` 内部、GIC 段之前。

**H4 —— GIC 全绿但心跳冻结（时钟从未打断 / 之后冻结）：**
```
gic: the kernel's clock interrupt is enabled and not pending: either the timer
    never fired, or the core froze past its gic bring-up -- the checkpoint ladder
    (oem run 17, 18, 19, 20, 21) says which
```
或时钟其实活着、是屏蔽中断的自旋：
```
gic: the kernel's clock interrupt is PENDING and never taken:
    the core has its interrupts masked -- a spin, not a wait
```
→ 配合 `oem sgi 1` 回读（pends-and-never-taken = 屏蔽自旋；was taken = 核还活着）+ 天梯二分。

**帧普查（顺带验证硬件拓扑）：** 预期 `aff 0, 100, 200, 300, 400, 500, 600, 700 (LAST)`，与 stock DTB 一致。若全 0/乱码 → GICR 窗口地址或时钟有问题（那就要查 UEFI 的地址映射）。

## R8 路线（按指纹分流）

| 指纹 | R8 修复 |
|---|---|
| H1 RWP 卡死 | loader 默认预写 G1NS\|ARE（gicset 转正），或内核 RWP 等待加超时退化为"继续跑"（GICD 位不齐时 SPI 路由退化，单核 + PPI 27 不受 ARE 影响，大概率能活） |
| H2 WAKER 卡死 | 内核 WAKER 等待加超时（重编内核） |
| H3/H4b 17-21 之间 | 天梯钉死区间后，`loader_checkpoint` 加桩或 `oem peek` 看内核内存，逐函数归零 |
| H4a 屏蔽自旋 | `oem peek` 读内核栈/关键数据结构定位自旋点 |
