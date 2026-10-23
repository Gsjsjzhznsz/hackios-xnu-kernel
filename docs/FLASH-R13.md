# R13 · 刷机与判读手册（SMMU 修复 + 八核 + boot-only 快线）

> 上一轮战报：[R12](FLASH-R12.md) —— GMU 苏醒（step 6 计数器 19198638 Hz = 19.2 MHz 晶振，
> **GPU 块已活**），卡 step 8：SMMU 尺寸自检 `that makes 0x10000 of registers, the device
> tree says 0x20000: not reading further`。触摸 / 按键 / 清屏三连胜保持。

## 本轮改了什么

| # | 改动 | 出处 / 依据 |
|---|------|------------|
| ① | `raphael_adreno.c`：SMMU 块尺寸 `0x20000 → 0x10000` | 主线 `sm8150.dtsi`：`adreno_smmu: iommu@2ca0000 { reg = <0 0x02ca0000 0 0x10000> }`。0x20000 是 SC7280（redwood 母机 `kgsl-smmu@3da0000`）的遗留值，R11 换基址时一起带了过来 |
| ② | `smmu_layout()`：IDR 优先——合理值（≥0x4000 且 ≤已映射窗口）直接采纳并打日志 `going with the ID registers`；只有乱码才停 | 用户指令“既然 IDR 确认是 0x10000 就直接按它读”。硬件自己的话 > 移植表；R12 的 IDRs（`idr0=0x4c017e06, idr1=0x20000005` → 4K 页 ×8 → 0x10000）与主线 DT 完全一致 |
| ③ | cmdline 加 `cpus=8`（两镜像同） | 内核 SMP 全套是上游现成的：`GENERIC_PSCI_SMP`（`psci_smp.c`：PSCI CPU_ON 到内核自身 reset vector + 缓存清洗 + 入调度等待）、`MAX_CPUS 8`、`GenericARMPE` 注册 DT 全部核。loader 见 payload 自带 `cpus=` 就不再注入 `cpus=1`，转而探测副核、写 DT |
| ④ | CI：**boot-only**——iOS 卷（7.8GB）不再下载/转换/拼接；小卷三级获取（cache → R12 release 直取 → 完整回退）；theos / xnu-deps / ccache 三重缓存；`xnu-clang` 包装器 `XNU_CCACHE=1` | 用户指令。小卷只装 userland + GPU 固件，跨轮不变（R5-R12 实测） |

## 镜像

| 镜像 | cmdline | 用途 |
|------|---------|------|
| `raphael-xnu-r13-flash.img` | `touchscreen=1 buttons=1 cpus=8 ufs=2 bootcpu=7 root_apfs=ufs_userdata` | 日常机：触摸 + 按键 + 八核 |
| `raphael-xnu-r13-gpu-flash.img` | 同上 `+ gpu=1` | GPU 梯子：这次过 step 8，新水域 10-21 |
| `raphael-r13-flashboot.apfs` | — | stage0 启动卷（与 R12 内容一致，手头有旧的可不重复 stage） |

## 刷机（bootloader 下，与 R11/R12 相同四步）

```
fastboot boot  raphael-xnu-r13-flash.img      # 或 GPU 镜像
fastboot oem  ramdisk
fastboot stage raphael-r13-flashboot.apfs
fastboot oem  run
```

> **不需要**重刷 userdata——iOS 卷 R5-R12 字节相同，手机里那份就是。
> 老规矩：**绝不要让 MIUI 开机**（会重格 userdata，iOS 卷即毁）。

## 判读树

### A. 八核（主 / GPU 镜像通用，klog 里找）

1. **loader 行**（logdump / klog 开头）：
   `cores for the kernel, in the order it takes them: 700 600 500 400 300 200 100`
   —— 期望 7 核（内核跑 Prime 700，loader 自己守着 core 0 不给）。
2. `core %x is not off (the firmware says %d): not the kernel's to take`
   —— 某核固件报告没关（ABL 占用）。属正常损耗，数一下少了谁、是否对称。
3. **内核行**：`GenericARMPE: N of M processors are running`
   —— N 核已入调度。大核（4/5/6/7）立即起，小核（1/2/3）+28s
   （上游调优值；`small_cores_after=0` 可改全立即——需要的话下轮加进 cmdline）。
4. **banner 行**：`memory 6144 MB, page size 16384, N cpu` —— **N ≠ 1 即生效**。
5. 若 N = 1：找 `psci: cpu N (MPIDR 0x...) was not started: the firmware answers X (原因)`
   —— PSCI 拒启，原因会写明（denied / already on / invalid parameters…），回传即诊。
6. 副核半路死会说清楚：`psci: cpu N (MPIDR 0x...) took an exception on its way in and has
   switched itself off: ESR ... ELR ...` —— 回传。
7. **风险提示**：本机（hackios 链）此前只跑过单核；SMP 机器上游在 redwood 母机天天跑，
   但 raphael 是第一次。任何不稳（卡死 / 花屏 / 触摸失灵）→ 刷回 R12 镜像即回单核。

### B. GPU 梯子（GPU 镜像，klog 里 `adreno: N`）

- **step 8 / 9 应过**（SMMU 修复）。8 = 全局寄存器 + 流表（IDR/SCR0/SMR/S2CR/CBAR），
  9 = context 0 的 SCTLR/TTBR0/TCR/FSR（第一次读“硬件自己的”寄存器，不经 hypervisor）。
- `going with the ID registers` = IDR 与常量不符但已采纳（理论上不会出现；出现即回传）。
- **新水域**：10 = GMU 数据存储器读写（ITCM/DTCM 经 0x2c6d000 寄存器窗口）→
  14 = 固件定位 + 内存 → 15 = SMMU context 1 + stream 5（GMU 的翻译表，第一次**写** SMMU）→
  16 = GMU 固件装载 → 17 = GMU 寄存器告位 → 18 = PDC/RSC 序列器 →
  19 = RPMh 睡眠程序 → 20 = HFI 队列 → 21 = 收官。
  （11-13 = TrustZone 鉴权三步，默认在跳过掩码 0x1c00 里，不走。）
- **卡在任何一步都正常**——每步一行日志，回传 klog 下一轮接着修。
  梯子止损时系统照常软渲染跑完：`THE GPU IS NOT STARTED: step N (…) failed; the system
  draws with its software renderer`，触摸 / 按键 / 八核不受影响。
- 全 22 步通过 = 硬件加速上线（动画丝滑那一刻）。

### C. 万一整机早起不来

- 拔线长按电源回 bootloader → `fastboot oem log && fastboot get_staged r13-klog.txt`
- `fastboot oem irqtest && fastboot oem gic` —— R9/R10 的 GIC 判定仍可用
- checkpoint 梯子照旧可二分：`oem run 15 / 16 / 17 / 18 / 19 / 20 / 21`
- 终极回退：R12 / R11 镜像（单核、已验证）

## 回退链

R13（八核+SMMU）→ R12（单核+测量降级）→ R11（首源码轮）→ R10（干净屏幕）→ R9（verbose）。
iOS 卷与启动卷跨轮通用，不用动 userdata。
