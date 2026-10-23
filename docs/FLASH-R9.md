# FLASH-R9 — 门铃轮：内核敲 27 号门，硅片把铃接在 19

R8 的判词说"写未粘住，修复属于 EL3 或 FIQ"。R9 的研究把这个判词本身修正了，
并在 R7/R8 自己的 dump 里找到了那个从头到尾都在场的真凶。

## R8 判词为什么错了

R8 用"写 IGROUPR0 → 回读 → 回读 0"判定写被丢弃。但这个判定逻辑不成立：

- **R7/R8 已证明组寄存器的非安全读是盲的（RAZ）**——R7 的 "group 0" 行就是
  瞎着读出来的。既然读是盲的，**回读 0 区分不了"写被丢"和"写落了但看不见"**。
  用一台盲仪器去验证一次写入，得到 0 不能证明任何事。
- **Linux 主线每次开机都从非安全 EL1 写这个寄存器**：
  `drivers/irqchip/irq-gic-v3.c` `gic_cpu_init()`：

  ```c
  /* Configure SGIs/PPIs as non-secure Group-1 */
  for (i = 0; i < gic_data.ppi_nr + SGI_NR; i += 32)
          writel_relaxed(~0, rbase + GICR_IGROUPR0 + i / 8);
  ```

  raphael-Linux（日用级 mainline）在这台 SoC 上每天开机都在写它。若这个写
  从非安全态会被丢，Android/Linux 的时钟中断早就永远进不来了。**写是有效的，
  读才是盲的。** R8 的 gic_group_repair 写很可能一直都在落地——只是回读永远
  看不见。

## 真凶：门铃号——一直写在 pending 行里

R7/R8 的 dump 从第一天起就把它摆在我们眼前：

```
enabled 8000000     ← 内核使能的是 bit 27 = INTID 27
pending 10080000    ← 时钟实际打在 bit 28 + bit 19 上
```

两边对不上，而 bit 27 从未 pending 过。两边各是什么：

| | INTID | 出处 |
|---|---|---|
| 内核守的门 | **27** | prebuilt `mach.development.bcm2837` 是 QEMU/bcm2837 世界出生的；反汇编 `gic_v3_irq_cpu_init`：`mov w8, #0x8000000`（=1<<27）硬编码 |
| 硅片接的铃 | **19** | 主线 `arch/arm64/boot/dts/qcom/sm8150.dtsi` timer 节点：`<GIC_PPI 3 IRQ_TYPE_LEVEL_LOW>` = EL1 虚拟定时器 = **INTID 16+3 = 19** = bit 19 |

内核从 EARLY_BOOT 起就布防了 CNTV（`_ml_set_decrementer` 写 `CNTV_TVAL_EL0`），
定时器打响，pending 打在 19 上；内核使能并守着 27；27 上永远不会有任何东西。
heartbeat=1 之后没有 tick，内核在等一个永远不会响的门铃。组寄存器从头到尾
可能都是好的——Linux 在同一块硅上天天用它。

（bit 28 = INTID 28 = SM8150 的 PPI 12，主线 dtsi 里没有设备认领——
推断为引导链遗留的杂散 pending，处于未使能状态，无害。`oem irqtest` 的
现场布防会把 vtimer 的真实接线再钉死一次。）

## R9 改动

**内核补丁**（`tools/r9-kernel-patch.py`，字节锚定 ×4，capstone 双端验证，
全文件仅 4 字 / 6 字节差异；其余 r1/r2/r4 验证字节一字不动）：

| 站点 | 函数 | 原指令 | 补丁 | 语义 |
|---|---|---|---|---|
| S1 | `_gic_v3_irq_cpu_init` @0x…493c | `movk w8,#0x800,lsl#16` | `#0x008` | ≥2 核路径：SGI0\|SGI1\|vtimer = 0x08000003 → 0x00080003 |
| S2 | `_gic_v3_irq_cpu_init` @0x…4944 | `mov w8,#0x8000000` | `#0x80000` | 单核路径（本链 cpus=1 实际走这条）：vtimer = 1<<27 → 1<<19 |
| S3 | `_gic_v3_irq_cpu_init` @0x…4968 | `strb w10,[x9,#0x39b]` | `#0x393` | IPRIORITYR 字节 0x380+27 → 0x380+19（优先级 0xa0 写给真门铃） |
| S4 | `_gic_irq_handler` @0x…4b04 | `cmp w20,#0x1b` | `#0x13` | **分发比较**：INTID==27 → rtclock_intr 改为 INTID==19 |

同函数里 `GICR_IGROUPR0 |= w8`、`IGRPMODR0 &= ~w8`、`ISENABLER0 = w8`
都用同一个 w8 位——立即数一改它们免费跟随；`_ml_set_decrementer` 的 CNTV
布防、EOI、SGI 机制全部与 INTID 无关。分发端其余分支（INTID 0 → IPI、
1 → psci_smp_stop_here、≥32 → SPI 表）不变。

**loader 0.9**：`GIC_VTIMER_INTID` 27→19；判词行换成修正后的读法；
`gic_group_repair` 保留（兑底）；新增 **`oem irqtest`**——交付实验，
在 loader 自己的核上做，不需要内核：

```
布防自己的 CNTV ~50ms → 在自己帧里使能 INTID 19 → ICC_PMR=0xff +
ICC_IGRPEN1=1（内核 bring-up 写的同款一对）→ 等 bit 19 pending →
读一次 ICC_IAR1_EL1（PSTATE.I 掩着也能应答：喂它的是 GIC 的信号状态，
不是异常）→ 全量还原
```

判词三选一：

| 判词行 | 含义 | 下一步 |
|---|---|---|
| `INTID 19 acked from ICC_IAR1 ... DELIVERS as a Group 1 NS IRQ` | 时钟可以作普通 IRQ 交付 | R9 补丁即足——直接 `oem run` 看心跳 |
| `pends ... but ICC_IAR1 answers spurious` | 组的事是真的（银行不肯作 IRQ 发信号） | R10：EL3 monitor 设组 / 内核接 FIQ |
| `never pended after a fresh CNTV arm` | vtimer 另接别处（与主线 dtsi 矛盾） | pending 行会指出真实接线 |

## 实验协议（出包后）

```sh
fastboot boot  raphael-xnu-r9-flash.img
fastboot oem  irqtest            # 可选但强烈建议：交付判词，3 秒出结果
fastboot oem  ramdisk
fastboot stage raphael-r9-flashboot.apfs
fastboot oem  run                # 看 klog：heartbeat 1 -> 2 -> 3 ... = 内核活了
# 若仍卡死（~60s loader 判 hung）：
fastboot oem irqtest && fastboot oem gic
fastboot oem sgi 1               # 复活验证：应回 "was taken"
fastboot oem log && fastboot get_staged r9-klog.txt
```

## 判读树

1. **`oem run` 后 heartbeat 1→2→3** = 时钟中断回来了。这是本轮的胜利条件。
   之后的冻结（launchd/用户态/显示）属于新问题，天梯 `oem run 17..21` 定位。
2. **heartbeat 仍卡 1** + irqtest 判词 A（DELIVERS）= 交付没问题但内核没取到
   ——嫌疑落在内核侧（PMR/优先级/掩码），R10 用天梯 + `oem gic` 帧回读定位。
3. **heartbeat 卡 1** + irqtest 判词 B（spurious）= 组的事是真的，EL1 无解，
   R10 走 EL3 monitor 或内核接 FIQ（重编内核：unmask F + FIQ 向量，注意
   ICC_IAR0/ICC_IGRPEN0 从非安全态同样受限，需 EL3 配合或纯定时器 FIQ 路线）。
4. **irqtest 判词 C** = 主线 dtsi 与实物不符（几乎不可能），按 pending 行
   实测接线改补丁常量重出。

## 为什么不重编内核

xnu-build.sh 就绪，但重编会把"验证过的字节"换成"新工具链的字节"，一次引入
无数变量。四处立即数补丁是**外科手术式**的最小改动：每一处都有反汇编断言
守门（CI 里再验一次），全文件差异恰为 4 字。r1/r2/r4 的验证资产原样保留。
