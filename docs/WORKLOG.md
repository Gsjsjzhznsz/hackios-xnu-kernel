# XNULOADER 赛道工作日志（raphael / K20 Pro，SM8150）

一轮一节，最新的在最下面。**规矩（R8 立于用户指令）：每轮的 worklog、私有
脚本与工具，随该轮一起提交进本仓库**——会话丢了也要能凭仓库独立恢复，
判读分析不许只活在聊天记录里。host 侧脚本住 `tools/host/`。

赛道简史（详细见 git log 与 docs/FLASH-R*.md）：
- r1/r2：raphael stage 移植——手机自己的 xbl.img 作 UEFI 源；FD/shim/golden
  DTB 三件硬件验证字节，此后各轮永不改动
- r4：loader 链定型（内核 prebuilt/mach.development.bcm2837，r2/r4 谱系）
- r5：双盘闪存启动（payload ramdisk=md1 /redwood，flashboot.apfs 开机
  stage 上传=md0 根）；首次心跳=1 冻结、内核日志 0 字节；PSCI
  CPU_ON(0x700) 回答 0（已启动）
- r6：分诊 loader（oem gic/sgi/peek + userdata APFS 判定）。探针瞎眼：
  GICR_TYPER 亲和值读 >>40（实际在 [63:32]）→ 永远"no redistributor
  frame"；存储排除：ufs_userdata 里 NXSB@+32（闪存上有健康 APFS 容器）
- r7：修好探针（>>32）+ 全量 dump + sgi 回读 + gicset + checkpoint 天梯
  （oem run 15..21）文档化；CI run 39878502505 success

---
Task ID: R8
Agent: Z.ai (main)
Task: R7 实测判决（r7-klog.txt）→ R8（GIC 组活体实验轮）+ 立规：worklog/私有脚本/工具必须随轮入库

Work Log:
- R7 实测判决（用户 r7-klog.txt，R7 镜像出 CI 39878502505）：
  * `gic: frame 0..7 aff 0,100,200,300,400,500,600,700 (LAST)`——硬件拓扑
    = loader 编号（0x000..0x700 单核 cluster）；上一会话的"幽灵 MPIDR"
    假设就此退役，`core << 8` 从来没错
  * `gic: distributor ctlr 12: G1NS 1 ARE 1 RWP 0`——内核永不进 RWP 等待，H1 退役
  * `gic: core 700: waker 0 (PROCSLEEP 0 CHILDASLEEP 0)`——RD 醒着，H2 退役
  * `sgi: 1 pends and is never taken`
  * `gic: core 700: SGI+PPI group 0 grpmod 0 enabled 8000000 pending 10080002 active 0`
    → `the kernel's clock interrupt is enabled but in Group 0: it signals as
    FIQ, which this kernel never takes`
  * heartbeat=1 的语义：`hv_wdt_pet_init()`（STARTUP(EARLY_BOOT, RANK_LAST)）
    同步跑完的第一次 pet；之后每 10s 的 pet 靠时钟中断驱动的 thread_call，
    再也没来。内核活着，等一个永远不到的 tick。
- 关键问题"谁忘了写组"不用理论裁决，反汇编已交付内核定案
  （工具：tools/host/r8-kernel-disasm.py，本条即该轮产出）：
  * `_gic_v3_irq_cpu_init` @ 0xfffffff007474798（mach.development.bcm2837，
    符号齐全）：
    `w22=0x10080 (GICR_IGROUPR0)；w8=0x8000000 (1<<27 vtimer)`
    `ldr/orr/str [x9]` = GICR_IGROUPR0 |= mine（gic_v3_irq.c:477）
    `ldr/bic/str [x9,#0xc80]` = GICR_IGRPMODR0 &= ~mine（:478）
    `strb [x9,#0x39b]` = IPRIORITYR(27)
    `str w8,[x9,#0x80]` = GICR_ISENABLER0 = mine（:480）
  * **内核写了组**——就在使能写前四条指令；R7 dump 看到的
    `enabled 8000000` 正是那条 ISENABLER0 写落地的 0x08000000
  * 同帧同执行流：使能写生效、组写没有 → 唯一成立的解释：**GIC 运行在
    安全态（DS=0，Qualcomm TZ 链常态），组寄存器 secure-only：非安全写
    丢弃（WI）、非安全读恒 0（RAZ）**。R7 的 "group 0" 行是瞎着读出来的；
    内核自己那次写同样被丢；loader（同为 NS EL1 的 UEFI 应用）替写
    同样会被丢——R7 遗留问题 2 的答案：改内核（已经写了）与 loader
    替写（同态同丢）都治不了这个寄存器，得先把"写能不能粘住"变成实验
  * 对 R7 遗留问题 1：不是 UEFI 把 IGROUPR0"留成"0——GICv3 架构复位值
    就是全 0（全 Group 0），这条链上（EL3 只管自己的中断、EL2/ABL/UEFI
    无人过问、内核写了被丢）从来没有人写进过 1；且"内核以为"不成立——
    组对内核不可见，它只决定 GIC→PE 的投递类别（Group 0 → FIQ）。
    Android 机器上是 Linux 的 gic-v3 驱动在 irq 域分配时把组搬过去的
    （那条链允许 NS 写组，或其固件预置过）
  * 上游 hackios 作者提示"CPU interrupt problem"与此一致
- R8 改动（loader 0.8，loader.c 单文件，内核/shim/FD/DTB 字节不动）：
  * `gic_group_repair()`：把内核核整块 SGI+PPI 银行置 Group 1
    （IGROUPR0=0xffffffff、IGRPMODR0=0，G1NS 非 G1S），settle 后相信回读
  * 三个时机：`run_on_other_cpu()` 里 PSCI CPU_ON 前预置一次（帧状态若能
    穿过上电，内核第一条指令起时钟就是 Group 1）；hung 判定（心跳静默
    50s）时兜底修一次；`oem gicgroup` 手动随时
  * `oem gic` 判词行带上盲读告警；`oem sgi` 的 pends 行加"组 0 时 SGI 也
    是 FIQ"提示
  * 刻意不重编内核：R7 基线行已排除 H1/H2，唯一嫌疑就是组，而组能不能
    写只有这台机器的 GIC 能回答；内核重编（xnu-build.sh 就绪）留给
    实验结果真正需要它的那一刻（R9 分支表见 docs/FLASH-R8.md）
- 交付链：tools/r8-flash-build.sh（R7 脚本改名+R8 门禁特征串
  oem gicgroup/group repair/pre-arm before CPU_ON/at the hung verdict）；
  .github/workflows/r8-springboard.yml（loader 0.8，判读树重写为
  gicgroup 决胜行）；docs/FLASH-R8.md（完整证据链+实验协议+R9 分支）
- CI：提交 745cbcf 推送；run 39927063425（tag r8-springboard）dispatch 成功
- 立规（用户，本轮）：worklog、私有脚本和工具以后必须上传到仓库——
  本条 WORKLOG 与 tools/host/（r8-kernel-disasm.py、r8-watch.py、README）
  即首例；会话恢复不得再依赖聊天记录

Stage Summary:
- R8 = 0 变量改动的 loader 轮：把"非安全写能不能设置 SGI+PPI 组"变成
  一行当场出结果的判词；内核字节不动，r1/r2/r4 法则保持
- 判读树决胜行：`oem gicgroup` 回读 0xffffffff → 写粘住，卡在等时钟上的
  内核当场复活（盯 heartbeat 1→2→3、`oem sgi 1` 应回 was taken）；
  回读 0 → 这条链上任何 EL1 软件都拿不到 IRQ，修复属于 EL3（monitor 设
  组）或内核接 FIQ 路线（R9，需重编内核）——决策表在 docs/FLASH-R8.md
- 预期 R9 分支：写粘住+复活 → 预置转正，继续天梯追剩余冻结点；写粘住
  仍卡 → CPU interface 层（ICC_IGRPEN1/PMR 可能也被 EL2 吞）；写被丢 →
  照抄 raphael-Linux 在这台机器上拿到 IRQ 组的机制（唯一被证明可行的
  答案），或内核接 FIQ
