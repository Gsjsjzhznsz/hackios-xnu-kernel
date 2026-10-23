# FLASH-R8 — 把 R7 的判决变成一次活体实验

R7 的 dump 把案子钉到了最后一行判词：

```
gic: core 700: SGI+PPI group 0 grpmod 0 enabled 8000000 pending 10080002 active 0
gic: the kernel's clock interrupt is enabled but in Group 0: it signals as FIQ,
    which this kernel never takes
sgi: 1 pends and is never taken
```

时钟中断**已使能、已 pending、从未被取**；heartbeat=1（`hv_wdt_pet_init()` 在
EARLY_BOOT 同步跑完的第一次 pet）之后再没来过——之后每 10 秒一次的 pet 靠时钟
中断驱动的 thread_call。内核活着，在等一个永远不来的 tick。

## 反汇编取证：谁忘了写组？

对**已交付的** prebuilt 内核（`prebuilt/mach.development.bcm2837`，r2/r4 谱系，
符号齐全）反汇编 `_gic_v3_irq_cpu_init`（@ `0xfffffff007474798`），关键序列：

```
0x...908: mov  w22, #0x80 ; movk w22,#1,lsl#16   ; w22 = 0x10080 = GICR_IGROUPR0
0x...944: mov  w8, #0x8000000                    ; mine = 1 << 27（vtimer PPI）
0x...948: add  x9, x20, x22                      ; x9 = gicr + 0x10080
0x...94c: ldr  w10, [x9]                         ; group 读
0x...950: orr  w10, w10, w8
0x...954: str  w10, [x9]                         ; ← GICR_IGROUPR0 |= (1<<27)   [line 477]
0x...958: ldr  w10, [x9, #0xc80]                 ; GICR_IGRPMODR0
0x...95c: bic  w10, w10, w8
0x...960: str  w10, [x9, #0xc80]                 ; ← IGRPMODR0 &= ~mine          [line 478]
0x...968: strb w10, [x9, #0x39b]                 ; IPRIORITYR(27)
0x...96c: str  w8, [x9, #0x80]                   ; ← GICR_ISENABLER0 = mine      [line 480]
```

**内核写了组。** 就在那条明显落地的 ISENABLER0 写（dump 里看到的
`enabled 8000000` 正是它的 0x08000000）前四条指令。同一帧、同一执行流：
使能写生效、组写没有。

非安全写被丢弃而邻居生效，只有一个解释成立：**GIC 运行在安全态
（DS=0，Qualcomm TZ 链的常态），组寄存器是 secure-only——非安全写丢弃
（WI）、非安全读恒 0（RAZ）**。推论：

- R7 dump 的 `group 0` 是**瞎着读出来的**——那行永远读 0，无论真实组是什么；
- 内核自己那次写**同样被丢**——它永远拿不到这个中断作 IRQ；
- loader（同为 NS EL1 的 UEFI 应用）写它也一样被丢——"gicset 式替写"
  **治不了这个寄存器**，这正是 R8 要当场证明/证伪的事。

回答 R7 遗留的两问：

1. **是 UEFI 把 IGROUPR0 留成 0 吗？** 是效果、不是动作：GICv3 架构复位值
   就是全 0（全 Group 0），这条链上没有任何人写过 1——EL3 只关心自己的
   中断，EL2/UEFI/ABL 不管，内核写了但被丢。Android 上是 Linux 的
   gic-v3 驱动在 irq 域分配时把组搬过去（那条链的 GIC 允许，或固件预置）。
   而且内核"以为"的说法不成立——组对内核不可见，它只决定 GIC→PE 的
   投递类别（Group 0 → FIQ）。
2. **改内核还是 loader 替写？** 内核**已经写了**（上游作者显然在别的板子上
   踩过并修了驱动）；loader 替写与内核同态、同样被丢。所以 R8 既不重编内核
   （保住 r1/r2/r4 验证字节），也不假设替写能成——而是把"写能不能粘住"
   本身变成一次当场出结果的实验。

## R8 loader（0.8）内容

| 机制 | 干什么 |
|---|---|
| `gic_group_repair()` | 把内核核整块 SGI+PPI 银行置于 Group 1：`IGROUPR0=0xffffffff`、`IGRPMODR0=0`（G1NS 而非 G1S），settle 后**相信回读** |
| 开机预置 | `run_on_other_cpu()` 在 PSCI CPU_ON 前调用一次——若帧状态能穿过上电，内核第一条指令起时钟就是 Group 1 |
| hung 兜底 | 心跳静默 50 s 判 hung 时再修一次（预置可能在上电丢失/内核自己的写被丢时兜底），判词进 klog |
| `oem gicgroup` | THE 实验：随时手动修 + 回读 + 判词 + 全量 dump |
| 判词更新 | `oem gic` 的 Group 0 行、`oem sgi` 的 pends 行带上 R8 盲读告警 |

内核 / shim / 固件 / DTB 仍是 r1/r2/r4 硬件验证过的字节。

## 实验协议（出包后）

```sh
fastboot boot  raphael-xnu-r8-flash.img
fastboot oem  ramdisk
fastboot stage raphael-r8-flashboot.apfs
fastboot oem  run                 # 卡死就等 ~60s：hung 判定时自动修一次并打印
fastboot oem gicgroup             # THE 实验（手动）
fastboot oem sgi 1 && fastboot oem gic
fastboot oem log && fastboot get_staged r8-klog.txt
```

## 判读树

**开机三行基线：**

```
gic: distributor ctlr <hex> as the firmware left it: G1NS _ ARE _ RWP _
gic: group repair (pre-arm before CPU_ON): core 700 frame 17b40000 IGROUPR0 .. -> ..
```

**THE 实验（`oem gicgroup` / hung 自动）二选一：**

```
IGROUPR0 0 -> ffffffff: every SGI+PPI is Group 1 now.
```
→ 写能粘住：这世界不需要别的。卡在等时钟上的内核**当场复活**——盯
heartbeat（1→2→3…）、`oem sgi 1` 应回 "was taken"。开机预置从此就是修复
本身；若复活后仍卡，剩余问题在天梯（`oem run 17..21`）里。

```
IGROUPR0 0 -> 0: the write did not stick.
```
→ GIC 丢弃非安全组写（安全态 DS=0，组寄存器 secure-only）。这条链上任何
EL1 软件——**内核或 loader**——都拿不到这个中断作 IRQ；它只能以本内核
不接的 Group 0 FIQ 永远 pending。修复不再属于 EL1。

**R9 分支（由实验结果决定）：**

| 实验结果 | R9 修复 |
|---|---|
| 写粘住 + 内核复活 | 预置转正（已是 R8 行为）；继续天梯追剩余冻结点 |
| 写粘住 + 内核仍卡 | 组对了但投递仍死 → CPU interface 层（ICC_IGRPEN1/PMR——内核已写，可能也被 EL2 吞）；天梯定位 |
| 写被丢 | 两条路：① 内核接 FIQ（重编内核：unmask F + `sleh_fiq` 既有 vtimer 路线——但 ICC_IGRPEN0 同为 secure-only，需实验）；② 找这条链上 raphael-Linux（日用级 mainline）如何拿到 IRQ 的组——那是唯一在这台机器上被证明可行的答案，照抄它的机制 |

## 为什么不直接重编内核加超时

R7 遗留的 H1（RWP 卡死）/H2（WAKER 卡死）已被 R7 基线行当场排除
（G1NS=1 ARE=1 RWP=0，waker=0）。剩下的唯一嫌疑就是组——而组**写没写**
已被反汇编定案，**写能不能用**只有这台机器的 GIC 能回答。R8 用一个
0 变量改动的 loader 把这个问题变成一行判词；内核重编（xnu-build.sh 就绪）
留给实验结果真正需要它的那一刻。
