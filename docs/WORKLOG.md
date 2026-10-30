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

R8 发布记录 (Task 25 补, 会话恢复后核实):
- CI run 39927063425 (tag r8-springboard, commit 745cbcf) 08:46:41Z 开跑,
  08:56:48Z completed success (~10 min); release 421630415 同刻发布, 11 资产
- 资产: iOS 卷 5 分片 (aa..ae, 合计 5,628,755,968 B, 与 R5/R6/R7 字节相同)
  + raphael-xnu-r8-flash.img (8,212,591 B) + raphael-r8-flashboot.apfs
  (4,194,304 B) + r8-boot-volume-root.apfs (4,194,304 B) + rejoin.sh
  + README + SHA256SUMS
- 公网回读校验 (Task 20 纪律): flash.img / flashboot.apfs / boot-volume-root
  三资产 sha256 与 SHA256SUMS 全等 (0281064f… / 14485e7c… / c290c248…),
  本地存档 work/r8-out/; iOS 大卷沿 R5/R6 同卷声明免重下
- r8-flash sha256: 0281064f49559dd454e9d1ada169c4bc0a10ef86b32e407959d5def82ba79bc1
- 交付件刷新 download/r8/ (四小件 + FLASH-R8.md + SHA256SUMS)
- 现存 releases: r5/r6/r7/r8 并存 (未删旧轮: iOS 卷跨轮同字节, 留旧
  release 可省重下; 需清理时按用户指令)
- 待办: 等用户按 docs/FLASH-R8.md 协议实测, 回传 r8-klog.txt /
  gicgroup 判词行 → 按 R9 分支表定下一轮

---
Task ID: R9
Agent: Z.ai (main)
Task: R8 实测判决（r8-klog.txt：IGROUPR0 0->0 ×2，heartbeat 卡 1，sgi pends）→ R9 门铃轮

Work Log:
- R8 实测归档：pre-arm 与 oem gicgroup 两次回读均 0 -> 0；内核 heartbeat 恒 1；
  sgi 1 pends never taken；帧拓扑/distributor ctlr 12 与 R7 一致
- **R8 判词修正（本轮核心认知翻转）**：R8 用"写→回读 0"判"写被丢"，但 R7/R8
  已证明该寄存器非安全读是盲的（RAZ）——盲仪器验证不了写入，回读 0 区分不了
  "被丢"与"落了看不见"；且 Linux 主线 gic_cpu_init() 每次开机都从非安全 EL1
  写 GICR_IGROUPR0=~0（irq-gic-v3.c:1293），raphael-Linux 日用正常 = 写有效
- **真凶定位（一直写在 R7/R8 dump 的 pending 行里）**：enabled 8000000（内核
  使能 INTID 27 = QEMU/bcm2837 出生的 prebuilt 硬编码门牌，反汇编 movz #0x800
  lsl#16）vs pending 10080000（时钟实际打 bit 28+19）——主线 sm8150.dtsi：
  EL1 虚拟定时器 = GIC_PPI 3 = INTID 19 = bit 19。**内核敲 27 号门，硅片把铃
  接在 19**。组从来不是故事（bit 28 = SM8150 PPI 12，主线无设备认领，引导链
  遗留杂散，未使能无害）
- 内核取证（tools/host/r9-kernel-forensics.py：修 r8 工具的节偏移 bug [offset@48
  非 64=flags] + 全 __TEXT 断点续扫 [capstone 遇坏字静默停] + 符号归属）：
  * _gic_v3_irq_cpu_init 全函数还原：探针是 _ml_get_cpu_count（>=2 走
    SGI0/1+vtimer 组合使能 0x08000003，<2 只 vtimer 0x08000000；本链 cpus=1
    走后者，与 dump enabled=8000000 吻合）；IGROUPR0/IGRPMODR0/IPRIORITYR/
    ISENABLER0 四连写用同一 w8 位；随后 GICD_CTLR|=0x12 + RWP 等待 +
    ICC_AP1R0=0 + **ICC_PMR=0xff + ICC_IGRPEN1=1**（CPU 接口齐装）
  * _gic_irq_handler 分发还原：IAR1 应答 → INTID 0→cpu_signal_handler、
    1→psci_smp_stop_here、**0x1b→_rtclock_intr（时钟路径）**、>=0x20→SPI 表、
    其余→通用 PPI 路径；处理后 EOIR1 + 排空循环
  * CNTV 布防（_ml_set_decrementer 写 CNTV_TVAL）与 INTID 无关 ✓；
    _fiq_context_init 只是保证 CNTV_CTL 使能，无真 FIQ 路线；
    _step_core_start 的 movk #0x800 是 0x0800020c 常量的一部分，无关排除
- R9 内核补丁（tools/r9-kernel-patch.py，字节锚定 ×4 + capstone 双端语义断言 +
  全文件 diff 审计恰 4 字 6 字节）：
  * S1 @0xfffffff00747493c movk w8,#0x800,lsl#16 → #0x008（多核路径）
  * S2 @0xfffffff007474944 mov w8,#0x8000000 → #0x80000（单核路径，本链实走）
  * S3 @0xfffffff007474968 strb [x9,#0x39b] → #0x393（IPRIORITYR(19)）
  * S4 @0xfffffff007474b04 cmp w20,#0x1b → #0x13（分发比较）
  * IGROUPR0/IGRPMODR0/ISENABLER0 写用同一 w8 位免费跟随
  * 内核 sha256: 9239d4ad…(原) → b9fe82ca…(r9)
- R9 loader 0.9（zig cc 本地编译预检零警告，CI 用真 clang/lld）：
  * GIC_VTIMER_INTID 27→19 + 判词行改 R9 修正读法 + gic_group_repair 保留
  * **oem irqtest**：loader 自己核上的交付实验（不需内核）：布防自己 CNTV
    ~50ms → 使能 INTID 19 → PMR=0xff/IGRPEN1=1 → 等 pending → 读一次
    ICC_IAR1_EL1（PSTATE.I 掩着也能应答）→ 判词三选一（DELIVERS/spurious/
    never pended）→ 全量还原（含无关 INTID 代答代还）
- 交付链：tools/r9-flash-build.sh（内核补丁步骤 + 门禁：loader 特征串 ×15 +
  补丁字节在镜像内二次验证 [cmp w20,#0x13 = 9f4e0071 / movz w8,#0x8 lsl16 =
  0801a052]）；.github/workflows/r9-springboard.yml（②b 装 capstone + 编译
  门禁，⑥ 打 r9 镜像，⑧ README R9 判读树，⑧b release 正文 R9 故事）；
  docs/FLASH-R9.md（证据链 + 协议 + R10 分支表）
- 立规落实：tools/host/r8-kernel-disasm.py 偏移 bug 修复入库；R9 工具
  （r9-kernel-forensics.py / r9-kernel-patch.py）随轮入库

Stage Summary:
- R9 = 门铃轮：内核 4 字补丁（27→19）+ loader 0.9 交付实验（oem irqtest）
- 胜利条件：oem run 后 klog heartbeat 1→2→3 = 时钟中断回来，内核活了；
  之后的新冻结（launchd/用户态）归 R10 天梯
- R10 分支表：心跳活→继续天梯追剩余冻结；卡 1 + irqtest DELIVERS→嫌疑在
  内核侧（PMR/优先级/掩码）；卡 1 + irqtest spurious→组的事是真的，EL3
  monitor 或内核接 FIQ；irqtest never pends→按 pending 行实测接线改常量
- 方法论：dump 的"回读验证"只在读不盲时有效——R8 的教训是拿盲仪器下了
  "写被丢"的结论；R9 用 Linux 主线同款写法 + 活体应答（ICC_IAR1 实测）
  取代回读判读

R9 发布记录 (Task 27 补):
- dispatch #1 run 39935808450 (babbcf6): ②b 编译门禁过（R9 loader 76,288 B，
  15 特征串全在），⑥ 失败——镜像内补丁字节验证窗口 k[:0x200000] 太小（补丁
  站点在文件偏移 ~0x470xxx），补丁工具本身全绿（sha256 与本地一致 b9fe82ca…）
- 修复 a3aed36（全文件搜索 + 只做存在性检查——原字节在别处自然出现，缺席
  检查会误报）；dispatch #2 run 39937219342 全绿（10:20:13Z completed
  success），release 421703283 @ 10:20:10Z，11 资产
- 资产: iOS 卷 5 分片（与 R5-R8 字节相同）+ raphael-xnu-r9-flash.img
  (8,212,591 B, sha256 e6537a55…) + raphael-r9-flashboot.apfs (4,194,304 B,
  14485e7c… 与 R8 同字节) + r9-boot-volume-root.apfs (4,194,304 B) + rejoin +
  README + SHA256SUMS
- 公网回读校验（Task 20 纪律）: flash/flashboot/boot-volume 三资产 sha256 全等
  PASS，存档 work/r9-out/；交付件刷新 download/r9/（四小件 + FLASH-R9.md +
  README + SHA256SUMS）
- 现存 releases: r5/r6/r7/r8/r9 并存（iOS 卷跨轮同字节留旧省 5.6GB 重下）
- 待办: 用户按 docs/FLASH-R9.md 实测——oem irqtest 判词 + oem run 后
  heartbeat 是否 1→2→3；回传 r9-klog.txt → 按 R10 分支表定下一轮

---
Task ID: R10
Agent: Z.ai (main)
Task: R9 实测判决（用户报告：SpringBoard 桌面在 -v 日志下渲染成功、iOS 接管
USB 后 fastboot 哑火 = 完全启动）→ R10 清屏轮（用户点名：去掉 -v 与
debug=0x8，看干净桌面；触摸/背光给路线图）

Work Log:
- R9 实测归档（用户报告，无 klog——USB 随完全启动被 iOS 接管，oem log 终端
  卡死不再回话）：桌面图标（照片/App Store/设置）在 -v 控制台日志底下成功
  渲染 = launchd 全程跑通、SpringBoard 上屏、UI 链路（boot video → 合成）
  全通；fastboot 哑火是**成功签名**（loader 常驻 core 0，但 USB 控制器被
  iOS 重置后通道即断），从此成功态取证 = 拍照，失败态（早冻）fastboot 仍活
- R10 定案 = 清屏轮，三处改动、全部在 cmdline/交接面，内核一字节不动：
  * payload cmdline 去 `-v`：boot_args.video.v_display 0→1（图形模式，非
    verbose iPhone 的标准开机方式；framebuffer 交接参数恒定，UI 路径不受
    影响——机制读自 loader.c:3356-3372 的 v_display 注释与代码）
  * loader 基础 cmdline 去 `debug=0x8 ` 前缀（loader.c base_cmdline）：
    raphael 无 UART，该位只喂屏幕叠加；心跳计数（kernel_live.heartbeat）
    与内核日志环均不走 console，失败日取证能力一件不丢
  * loader 交接前 screen_clear()：print() 走 UEFI 控制台会上屏，无 -v 滚屏
    后 loader 遗言会冻在屏上整段 launchd 启动——填黑 + clean_dcache 后再
    jump（UEFI ConOut 屏与 GOP 同一 framebuffer，oem screenshot 同款直访
    模式）
- 触摸/背光调研归档（R11+ 路线）：上游 hackios 在 redwood（POCO X5 Pro）
  上触摸全通（Goodix GT9916、SE13 SPI、125Hz 轮询、/dev/redwood-touch、
  boot-arg touchscreen=1 运行时开关），但全部地址是 sc7280/redwood 的；
  raphael = GT9886 走 GENI **I2C**（SE7, 0x5d）——帧法/引脚/时钟/供电轨全
  不同，直接开 touchscreen=1 等于拿错地址戳 SoC（t17-t19 教训：可触发
  RPM/TZ 热复位）。R11 = 内核重编轮：① CI 启用 xnu-build.sh 先证"重编等
  价"；② redwood_goodix.c 的 SPI→I2C 帧法移植 + raphael 地址（同机上电
  序列已被 ios-raphael 赛道真机验证：SE7 GENI I2C、0x5d、poll=10ms、doze
  disable）；③ 事件仍进 /dev/redwood-touch（用户态链路现成）。背光
  （AMOLED DCS/PWM）与 GPU 合成（redwood 走 Adreno+freedreno 跑
  CoreAnimation）同理排队
- 交付链：tools/r10-flash-build.sh（CMDLINE 无 -v；门禁 = R9 全特征串 +
  R10 两特征串 + debug=0x8 缺席断言 + 镜像内 cmdline 无 -v 复验；内核仍
  走 r9-kernel-patch.py）；.github/workflows/r10-springboard.yml（七步
  流水线 R10 化，资产/README/判读树/release 正文全换）；docs/FLASH-R10.md
  （判读树 + R11 观察清单 C1-C5 + 触摸路线图）
- 本地预检：zig 0.13（clang 18 内核）按 build.sh 同参数编译 loader.c
  **零警告零错误**；R10 两特征串在目标文件、debug=0x8 绝迹、R9 十五特征串
  全保留（CI ②b 门禁同款断言）

Stage Summary:
- R10 = 清屏轮：cmdline（-v / debug=0x8）+ 黑屏交接，三处改动全在交接面，
  内核与 r1/r2/r4 验证字节不动；R9 镜像即现成的 verbose 回退
- 判读树决胜行：黑屏 20-60s → 干净桌面 = 全胜；fastboot 卡死 = 成功签名；
  黑屏 >2min → R9 镜像看卡点；失败态 fastboot 照活，取证套餐不变
- R11 分支表：触摸 = 重编内核轮（GENI I2C 移植）；背光/GPU 合成按 C1-C5
  观察清单的痛点排队；本轮先把"看得清"还给用户

R10 发布记录 (Task 28 补):
- dispatch 两次（第一次后台任务静默成功所致），concurrency 组自动取消旧
  run 39943697102，存活 run 39943757373 于 11:06:25Z 开跑、11:16:53Z
  completed success（约 10 分钟；②b 编译门禁/⑥ R10 打包六重校验/⑧b 发布
  全绿）
- 发布 release 421751934 tag r10-springboard
  "R10 · 纯血 hackios SpringBoard · iOS 16.5 (20F66) · 清屏轮"，11 资产
- 资产: iOS 卷 5 分片（与 R5-R9 字节相同）+ raphael-xnu-r10-flash.img
  (8,212,591 B, sha256 70a2496d…) + raphael-r10-flashboot.apfs (4,194,304 B,
  14485e7c… 与 R9 同字节 = userland 未动铁证) + r10-boot-volume-root.apfs
  (4,194,304 B, 635d59e8…) + r10-rejoin.sh + README.txt + SHA256SUMS
- 公网回读校验（Task 20 纪律）: 三资产公开 URL 下载 sha256 与 SHA256SUMS
  全等 PASS，存档 work/r10-out/；交付件刷新 download/r10/（四小件 +
  FLASH-R10.md + README.txt + r10-rejoin.sh）
- 现存 releases: r5/r6/r7/r8/r9/r10 并存（iOS 卷跨轮同字节留旧省 5.6GB
  重下；**R9 从此兼任 R10 的 verbose 回退镜像**，按 FLASH-R10.md 判读树 B）
- 待办: 用户按 docs/FLASH-R10.md 实测——期望"黑屏 20-60s → 干净桌面 →
  fastboot 卡死（成功签名）"；回传观察清单 C1-C5（亮度/动画/色彩/触摸现状/
  桌面时钟走不走）→ 定 R11（触摸重编内核轮：GENI I2C goodix 移植）

---
Task ID: R11
Agent: Z.ai (main)
Task: R10 大捷（干净桌面确认）→ 用户指令：R11 驱动批量适配（触摸绝对优先/按键急需/GPU freedreno/背光），USB 暂缓，直接开干——内核首次源码构建

Work Log:
- R10 实测归档：干净 iOS 桌面 + fastboot 随 USB 被接管而哑火 = 完全启动签名
- 侦察三源：①上游 hackios 内核 overlay（redwood_* 驱动族 + redwood.patch 构建
  钩子 + xnu/apply.sh+xnu-build.sh 源码构建链，theos/L1ghtmann 工具链）；
  ②ios-raphael 旧项目基线树（work/xnu：sweet_touch.c 的 RAPHAEL 路径——
  GT9886@0x5d on GENI SE19@c88000、TLMM 98/100 供轨/54 复位/122 IRQ、784B
  gt9886_cfg.h、t13 同机真机验证；sweet_spmi.c 的 sm8150 SPMI 仲裁器=与
  redwood 同址）③上游主线权威（sm8150.dtsi/gpucc-sm8150.c/a6xx_catalog.c/
  a6xx_gmu.c/mdt_loader.c/a6xx.xml + LKML raphael DTS 全文 + pm8941-pwrkey.c）
- 门铃进源码：GIC_INTID_VTIMER 27→19（gic_v3_irq.c 单宏驱动 R9 四处二进制
  站点：分发比较/使能位/优先级/组掩码全跟随）
- raphael_touch.c（新，~900 行）：sweet_touch RAPHAEL 路径完整移植 →
  redwood_touch_post（0..65535 归一化，sensor 翻转按 DT + touchflip=1），
  touchscreen=1 门控，touchdelay= 默认 5s（真睡眠——cpus=1 下忙转会饿死
  launchd），say()→redwood_tl 实时日志钩子（每步上电行进 loader 日志环）
- raphael_buttons.c（新，~600 行）：redwood_buttons 架构 + raphael 外设：
  PON（PM8150 sid0 periph 0x008，KPDPWR/RESIN 位按 subtype 自适应
  classic=bit0/1 gen3=bit7/6，step3/4 打印裁决）+ 音量上（pm8150_gpios 6
  = periph 0xC5，主线 DTS 实锤）；只读 + 未见过释放不信任 + 60Hz 轮询；
  导出同名 redwood_spmi_lock/unlock（redwood_torch 链接保持）
- raphael_adreno.c（新，redwood_adreno.c 4125 行移植，scripts
  r11_port_adreno.py + r11_gpu_a640_fix.py 两段变换，每处改动注明出处）：
  地址表 sm8150 化（gpu 0x2c00000/gmu 0x2c6a000/gpucc 0x2c90000/smmu
  0x2ca0000/cmd-db 0x85f20000/pdc 0xb290000+新 pdc_seq 0xb490000）；
  a630 家族流程（DTCM 0x40000、RSC=GMU+0x23000、FAL10 不写、RSC ucode
  a630 字组）；PDC 完整加载（sm8150 无人代劳：SEQ uCode + TCS1/TCS3
  三命令组 + sync 0x30090，a6xx_gmu.c 逐行对照）；GEN2 池（a640_gmu.bin
  无 NEEDS 块——解码实证 ITCM 0x0/0x200/0x400/0x4000、DTCM 0x40000/0x43fe0、
  dcache 数据 0x57000——驱动按 Linux icache/dcache 0x4000/0x44000 各
  0x3f000 建池）；zap 单文件 .mbn 适配（qcom_mdt_read_metadata 语义：
  ELF头+hash 段拼 blob 给 TZ；PT_LOAD 段从 .mbn 内 p_offset 取）；频率表
  345/427/585@64/80/128（sm8150 gpu_opp_table）；chipid 0x06040001
- 固件源裁决链：a6xx_catalog.c 实锤 a640 的 GMU 固件是 a640_gmu.bin（非
  a630_gmu.bin——后者是 a630 legacy 裸镜像，块表解析必失败）；kernel.org
  对本沙箱 403 → Debian firmware-qcom-soc 20260916-1 拆分包（28MB）验明
  qcom/a640_gmu.bin（块表✓解码）+ qcom/a630_sqe.fw + qcom/sm8150/
  a640_zap.mbn（ELF32 Hexagon、3 phdr、hash 段@0x1000、PT_LOAD@0x3000
  RELOCATABLE）三件俱全；入库 fw-gpu-raphael/（86KB，sha256 在 README）
- MiniIOMobileFramebuffer：fbdim 软件调光（drawRows 三条写路径乘系数，
  unscaled memcpy 路径分支）+ redwood_fb_set_dim（type-6 记录，x=百分比，
  redwood_input.c 写路径挂钩）；DCS 硬背光 R12+
- 集成：redwood_input.c 换调 raphael_touch_init/raphael_buttons_init +
  redwood_input.h 声明；redwood.patch conf/files 三行换注册（goodix/
  buttons/adreno → raphael_*，原文件留树不编译）
- tools/r11-flash-build.sh：内核改为源码构建产物（缺失即 fail，不用
  r9-kernel-patch.py）+ 双镜像（主 touchscreen=1 buttons=1 / GPU 实验
  +gpu=1）+ 六重门禁升级（门铃字节在源码内核中验证 + R11 标记串
  RAPHAEL TOUCH/buttons: step 1/a640_gmu.bin/a640_zap.mbn/panel dimmed +
  cmdline touchscreen/buttons/-v 缺席断言）
- .github/workflows/r11-springboard.yml：九步流水线，内核源码构建前置
  （②c：theos+L1ghtmann iOSToolchain+apply.sh+deps+make -j4 -k，编译错误
  在 7.4GB 下载前暴露；内核产物标记串六重验证）；⑤ 步 fw-gpu-raphael →
  fw-gpu 填充 + 7z 抽验 fw/a640_gmu.bin 在小镜像；⑥ 双镜像 + iOS 卷分片
  与 R5-R10 同字节（免重刷）

Stage Summary:
- R11 = 驱动轮：源码构建内核（门铃进源码）+ 触摸（t13 验证时序移植）+ 按键
  （只读防呆）+ GPU（a640 全量移植，实验镜像隔离风险）+ 软件调光
- 双镜像发布：主镜像赢交互（滑动+按键），GPU 镜像独立实验（22 步梯子每步
  带判词，卡哪步回传哪步）；R10 镜像 = 行为基线回退
- 预期风险点已全部在 FLASH-R11.md 判读树标出：触摸（ABL 的 GENI 固件/
  总线 idle 裁决）、按键（PON 位 subtype 裁决）、GPU（zap 通用签名 TZ
  拒收/GMU 固件块表/PDC 完整加载）、源码构建本身（prebuilt→新工具链变量，
  R10 可一刷即回）

## R11 发布记录（Task 20 纪律）

- CI 七轮鏖战全绿：39957248914（THEOS=$HOME 字面目录）→ 39959129155
  （theos 主仓 sdks/ 空置，SDK 2020 年迁至 theos/sdks 独立仓）→
  39968195997（runner 无 uuid-dev，hostcompat.h 强制 include
  <uuid/uuid.h> 首行即死，exporthdrs 0.44s 全灭；本地沙箱恰好装有
  uuid-dev 故复现不出——dpkg -S 实锤）→ 39970464195（raphael_buttons.c
  残留 (void)problem，全内核唯一编译错误；touch/adreno 首编即过）→
  39973048286（redwood_fb_set_dim C++ 定义缺 extern "C"，C 调用方
  找不到符号，唯一未定义符号）→ 39974173713（dsymutil：Ubuntu apt
  clang 不装 /usr/bin/dsymutil，dSYM 步 Error 127 连累 strip 产物；
  内核链接本身已成功）→ **全绿，release 421986356，12 资产**
- 首个源码构建内核：15,029,152 B，sha256 5fa857ad5819e805…（DEVELOPMENT
  构建），门铃 cmp w20,#0x13 / movz w8,#0x8,lsl#16 源码级验证，
  双镜像共用（主 = touchscreen=1 buttons=1 / GPU = +gpu=1）
- 公网回读：五件 sha256 与发布 SUMS 全符；镜像解包（gzip 层内 XNUP）
  九项标记 + cmdline 断言复核；iOS 卷五片与 R10 SUMS 全同 = 免重下
  免重刷；flashboot 与 R9/R10 字节相同（14485e7c）= 固件/shim 铁证未动
- 交付：download/r11/（双镜像 + flashboot + 小镜像备份 + SUMS/README/
  rejoin + VOLUME-NOTE；卷分片不落盘——与 R10 同字节且无需重刷）

## R12（进行中）

- R11 真机判决：触摸/按键/清屏全胜（GT9886 normandy 选中；1257 次总线读
  7us 零失败）；GPU 卡 step 5 频率计数器（`did not finish`），软渲染回退
- 病理定案（三权威源逐行对日志）：计数器=被测时钟驱动的计数+晶体/4 做窗，
  DONE 等被测时钟边沿——链上任何一环静止则永不来；R11 断链全在移植层：
  ① 0x152000 bit16 是 GPLL vote 保留位（真地址 0x152004 bit16，vendor
  gcc-sm8150.c：gcc_gpu_gpll0_div_clk_src），写丢与日志逐字吻合
  ② hub 家族（hub_clk_src/hub_aon/hub_cx_int/cb）是 SC7280 的（gpucc-sc7280.c）；
  mainline gpucc-sm8150.c 全表没有——0x1170/0x117c/0x1178/0x1180/0x1204
  在 SM8150 是未实现洞（读 0 写丢）；SMMU 时钟真身=sm8150.dtsi adreno_smmu
  的 ahb+memnoc+snoc_dvm（R11 日志显示全在跑）
  ③ 计数器本就不该是门禁（Linux debugfs-only；频率=半分频 3/2×300MHz=
  200MHz 整；硬握手证人=Rcg UPDATE 清零+GDSCR PWR_ON，R11 双双通过）
- 修复（tools/host/r12_fix_adreno.py，锚定替换+T() tab 归一）：
  GCC_PLL_VOTE 0x52000→0x52004；删四条幽灵写入+hub clock_source；
  补 cxo_aon@0x1004；measure_clocks/cx_ready/step22 三处降级为证人
  （新日志串 "going on with the configured rates" 作内核标记）；
  measure 列表只留本 SoC 真有的四个时钟
- 工具事故记录：Edit 工具对整文件做过一次 tab→8 空格重排（git checkout
  恢复后全部经脚本重放解决）；脚本 T() 正则 ^((?: {8})+) 每组 8 空格
  →等量 tab，measure_clocks 尾块用字面 tab 直写（3tab+12sp 续行）
- 流水线：r12-springboard.yml（骨架=r11；②c 标记+R12 串）、
  tools/r12-flash-build.sh（sed 派生）；双镜像 cmdline 与 R11 相同
  （对照实验：只换内核源码）

---
Task ID: R20
Task: R19 战报（bank 0 先开后 SMR(0x2ca0800)=0x84000000 仍读回 0）→ 三方向裁决 → 本机 DT 破案 → R20

Work Log:
- （补记：R13-R19 的 round 记录未随轮入仓——本仓 git log 各轮 commit
  message 即其存档；R8 规矩自本轮恢复）
- 三方向裁决（用户 R20 指令）：
  · 方案一（全局 bypass/PA 直通）→ R21 后备。全局寄存器读回本身不可信
    （sCR0 读 0x2f0406 而翻译在跑），先修形态再看
  · 方案二（SMC/SCM 配流表）→ msm-4.14 无此服务（legacy SMMU_PROGRAM
    是 32 位时代的），非破局点；但 step 19 的 zap/PAS 13 基础设施在场
  · 方案三（UEFI/loader 抢配）→ 被参考项目否定：xnuloader 全程不碰
    SMMU，纯 EL1 直写在 sc7280 跑通——无需抢配
- 破案（决定性）：redwood_adreno.c（sc7280 参考，POCO X5 Pro，
  SpringBoard GPU 首帧实证）space_stream() 注释——"A match is taken
  only as the device tree has it"；sc7280 上 mask 0x401 被拒、
  0x400 被收。解析本机 vendor DTB（499823 B；沙箱脚本
  raphael_dt_gpu.py，FDT 逐字节，无 dtc）：
    kgsl-iommu@2CA0000/gfx3d_user   (sid 0, mask 0x401) ← 一条罩
    streams 0+1（LPAC）；gfx3d_secure (2, 0x400)；
    gmu@2C6A000/gmu_user/gmu_kernel (4/5, 0x400) ← 与 sc7280 恰同形
  三轮"hypervisor 拦截"真相：写入形态从来不是本机 DT 原样。GMU 蒙对
  （5/0x400 同形），gfx 全灭（0/0x400 非本机形态）。R18 扫的 entry 全
  是 0x400 形态、R19 的 entry 2（1/0x400）同样非本机形态——三轮战报
  同一机制自洽。附带收获：qsmmu-v500 双 TBU（gfx_0_tbu 0-0x3ff @
  0x2cc5000 状态窗 0x2cc2200；gfx_1_tbu 0x400-0x7ff @ 0x2cc9000
  状态窗 0x2cc2208）——R21 取证地址已入库
- 修改（scripts/r20_apply.py 字节锚定×10 全一次命中；文件 8 空格缩进
  无 tab、LF——核实后非假设）：
  · SMMU_STREAM_MASK 拆 SMMU_MASK_GMU 0x400 / SMMU_MASK_GFX 0x401
    （DT 出处与两向证据写进注释）
  · space_stream/space_on 增 mask 参数（写入值与校验值同源）
  · GMU 路径带 SMMU_MASK_GMU（行为不变；bank 候选逻辑未动）
  · step 18：space_on(core, entry 0, stream 0, SMMU_MASK_GFX) →
    smr(0x2ca0800) = 0x84010000
  · stream 1 默认不写 entry 2（0x401 罩住、走 context 0）；
    adreno_stream1=1（entry 2 走 context 0，sc7280 形态）/2（空上下文）
    降级为强制实验开关，PE_parse_boot_argn 进 step_core_space
  · R19 判词 "the hypervisor drops SMR/S2CR" 从代码除名
  - diff：9 hunks +66/−27，git diff 干净（补丁存
    download/r20-gpu-step18.patch——沙箱侧）
- 流水线：r20-springboard.yml（骨架=r19；缓存键迁 r20-；门禁正向 +
  R18 四条与 R19 判词反向除名）、tools/r20-flash-build.sh（门禁同步；
  判读段胜利线改为正确的 0x84010000——R19 版 README 里的
  "0x84400400" 是从未对上实际写入值的笔误，R20 一并纠正）
- 静态校验：标记串正反三方核对（源码/flash-build/workflow）全过；
  括号/花括号平衡；YAML 解析过；r20-flash-build.sh 可执行位就位

Stage Summary:
- R20 定性：不是"无条件拦截"，是写入形态从来不是这部手机的 DT 原样
- 胜利线：smmu smr (0x2ca0800) = 0x84010000（R17 以来 gfx 流首次落地）
- 失败分支（0x84010000 仍读 0）R21 战场已排定：TCU/TBU 取证 →
  sCR0.USFCFG → loader 时间线二分

## R21 · TrustZone，按本机自己的方式问（2026-10-24）

- 战报（R20）：step 18 通关——`about to write smmu smr at 0x2ca0800:
  0x84010000` → 读回 `0x84010000` → `step 18: done`（R17 以来 gfx 流
  SMR 首次落地，三轮 SMMU 迷案结案）。step 19 停在第一句 TZ 对话：
  `about to ask TrustZone to give up the zap shader: smc 0x42000206,
  arguments 0xd, 0, 0`——之后 wrapper 的应答行（`TrustZone returns ...`）
  永远没来，触摸/按键照走：发 SMC 的那颗 CPU 被吞在 EL2/EL3，一去不回。
- 破案（三条 redwood 残留，全部本轮证据）：
  1. give-up call（svc2/cmd6）是 redwood 的世界：那边内核来去不重启，
     前任会留 shader，TZ 对它有问必答（没有也答 success）；本机每次
     过 XBL → TZ 全新，不可能有"上次的 shader"；本机自家驱动
     （dt: qcom,kgsl-hyp，compatible "qcom,pil-tz-generic"，firmware
     a640_zap，pas-id 0xd——scripts/raphael_dt_zap.py 实测）进场也不发
     give-up。→ 默认不发送；adreno_tzunload=1 才发、放最后。
  2. ZAP_MEMORY 0x8b71a000 = redwood gpu_micro_code；本机 = **pil_gpu_
     region@0x98f15000**（8 KB，reserved-memory 实测）。
  3. zap_header() 的 hash 段判定按 redwood .mdt 的 p_flags 低三位——
     本机 a640_zap.mbn 的标记在 **24-26 位**（phdr[1] 0x2200000，
     mdt_loader.c: QCOM_MDT_TYPE_HASH 2<<24）：旧判定一个 hash 都找不到。
- 修改（scripts/r21_apply.py 字节锚定×9 全一次命中；+113/−39）：
  · scm_how 静态量：64 位形态先问（0xC2xxxxxx），被拒则 32 位二试
    （0x82xxxxxx，Linux 的第二试），全轮调用同源
  · step 19 重排：通道探针（is-call-available (6,1) 本体 + 签名/落位/
    认证三条可用性）→ adreno_tzunload 门控的 give-up → .mbn PT_LOAD →
    zap_header → INIT_IMAGE(2,1) → MEM_SETUP(2,2)（本机 pil_gpu_region，
    0x2000）→ 写段 → AUTH_AND_RESET(2,5)
  · 拒绝即失败（"TAKEN TO MEAN THAT IT KEEPS A ZAP SHADER" 判词除名）；
    失败路径追加诊断：PAS_IS_SUPPORTED GPU + audio 对照 + 补发 give-up
  · zap_header() hash 判定加 (p_flags & 7<<24) == 2<<24
  · zap_unload/step_zap_signature 改用 scm_how
- 流水线：r21-springboard.yml + tools/r21-flash-build.sh（缓存键迁
  r21-；门禁正向 +R21 八条、反向 + "TAKEN TO MEAN..." 除名；R20 全部
  保留标记原样）
- 静态校验：R20 全部保留标记在场；退役标记不在场；括号/花括号平衡；
  补丁存 download/r21-trustzone-zap.patch（沙箱侧）

Stage Summary:
- R21 定性：不是"缺少 SCM 授权握手"（TZ/PAS 环境由 XBL 自己备好），
  而是 redwood 的 warm-boot 语义（give-up）撞上 raphael 的冷启动世界，
  外加两处地址/格式残留
- 胜利线：TRUSTZONE HAS AUTHENTICATED AND STARTED THE ZAP SHADER →
  step 20（CP 出安全模式）→ 21（commands）
- 判读树按楔死位置分层：0xc2000601 本身楔 = 整门皆暗（R22 = HVC/
  kgsl-hyp）；0xc2000201 楔 = PAS 族暗（R22 = 内存归属）；
  0xc2000205 楔 = 最接近；返回错误码 = 通道全活、码即答案

## R22 · -3145797 拆案（元数据布局 × 调用形态的三连矩阵）

- R21 战报判读：通道全活——0xc2000601 探针（本体 + 0x2000201/2/5 三条可用性）全答
  0/0x1；0xc2000206（give-up，SMC64 快调用 + 通道已建）干净返回 0/0（R20 的楔死
  从此定性为"冷通道首发的 SMC32 give-up"，纪律保持：先探针、只走 0xC2xxxxxx）。
  但 PAS_INIT_IMAGE (0xc2000201, pas 13, 元数据 0xff014000) 返回 **-3145797
  (0xFFCFFFBB), answer 0** ——调用有答、镜像不收；0xc2000207 对 GPU/audio 都答 0
  （该问句语义不明，不能作为"TZ 封杀 GPU"的证据）；MEM_SETUP/AUTH_AND_RESET 未到达。
- 拆案（两份源码 + .mbn 实测三方对照）：
  1. **元数据布局**（zap_header()，+113/−39 的 R21 版）：TrustZone 按缓冲里 ELF 头
     自己声明的 p_offset 取 hash 段（mdt_loader.c: qcom_mdt_read_metadata；Linux 的
     zap 路径干脆整个 .mbn 传进去：qcom_scm_pas_init_image(13, fw->data, fw->size)）。
     a640_zap.mbn 实测（scripts 侧解析）：header pair 0x94B、hashes 0x1a38B @
     **p_offset 0x1000**、PT_LOAD 0x7b0B @0x3000、全文件 14256B。R21 把 hash 打包到
     0x94（.mdt 形态——.mdt 里那确实是 p_offset），TZ 到 0x1000 处读到的是打包块
     中段 → -3145797。14256B < 16KB 页：整个文件放进单页即可（16K 页 XNU，物理连续）。
  2. **调用形态**：R21 的 PAS 调用带 SCM_FAST（与探针同型）；参考 redwood step 12 与
     Linux 都发**可抢占**调用（redwood 原话 "a call that may be preempted, as
     Linux's is"；wrapper 的 SCM_INTERRUPTED + session 重试舞步就是为此存在，R21 里
     从未走过）。签名校验时长不保证"一次答完"。
- R22 修改（xnu/files/bsd/dev/raphael_adreno.c，+99/−36）：
  · zap_header(phys, whole)：双形态元数据——whole=true 整个 .mbn（Linux 形态，每个
    p_offset 都指着真字节）；whole=false R21 打包形态（留作变量隔离）；单页 16KB、
    物理连续、to_memory 清缓存；打印 hash 的文件偏移
  · step 19 三连矩阵：①整 .mbn + 可抢占调用 ②R21 打包 + 可抢占 ③整 .mbn + FAST。
    尝试间补发 give-up（R21 证明有答、返回 0）清场；每试打印自己的拒绝码；获胜
    形态（mem_how）接管 MEM_SETUP(2,2)（落位尺寸改 0x1000——redwood 的活调用也是
    0x1000，尽管区域是两页）与 AUTH_AND_RESET(2,5)
  · wrapper：被抢占 N 次后应答时打印 N（重试舞步首次可见）
  · step 12（mask 跳过中）同步 zap_header 新签名
- 流水线：scripts/r22_pipeline.py 派生 tools/r22-flash-build.sh（门禁 +R22 十一条、
  R21 全部保留、R18/R19 退役名单原样）+ .github/workflows/r22-springboard.yml
  （缓存键迁 r22-、ccache 回退链补 r21-ccache-；README/release body 全量换 R22 判读树；
  bash -n + yaml 校验过）
- 静态校验：R21/R20 全部保留标记在场（含被换行/转义拆开的两条按编译后拼接形态复验）；
  退役标记不在场；括号平衡；补丁 download/r22-trustzone-zap-matrix.patch（沙箱侧）

Stage Summary:
- R22 定性：-3145797 = TZ 有答的拒绝，两个待隔离变量（元数据布局 0x94→0x1000/整文件、
  调用形态 FAST→可抢占）；三连矩阵一轮开机完成归因
- 胜利线不变：TRUSTZONE HAS AUTHENTICATED AND STARTED THE ZAP SHADER → step 20（CP
  出安全模式）→ 21（commands）
- 判读树：尝试1 收 = 布局+形态是墙；尝试2 收 = 形态是墙；尝试3 收 = 布局是墙；
  三试同码 = 码属于内存/状态（R23 = 元数据放哪 / kgsl-hyp 的 PAS 环境）；换码 = 进展

## R23 · 拒绝码结案 + 文件受审（-3145797 的真身）

**结论先行：-3145797 = TZ 对"这一个镜像"的认证拒绝，与调用形态/内存布局/地址无关；而随卷的 a640_zap.mbn 是高通 SecTools 测试签名的固件——熔丝手机的 TZ 谁叫都不收这条链。**

### 四问裁决（R22 战报的 a-d）
- **a) -3145797 (0xFFCFFFBB) 的含义**：TZBSP PIL 服务层拒绝码（x0 携带，不属 SCM 分发器小负数集 {-1,-2,-3,-4,-5,-12,-55}，本谱系内核 `scm_remap_error` 未知码一律 remap 成 -EINVAL，真机日志正是 `rc:-22`）。行为语义由真机日志实锤：SM8150 系手机上 spss 的 PAS_INIT_IMAGE（同一 func id 0x42000201）吃一模一样的码，同一次开机 cdsp/adsp/slpi/ipa_fws/venus/modem 六镜像全过、a640_zap 也过（"Brought out of reset"）——完美调用者照样吃码，前提是镜像过不了。精确枚举名未公开，行为定性已闭合。
- **b) hyp 是否为 PAS 0xd 配 SHM 池**：没有。DT 全表（t17-dtb.bin）：唯一 TZ 面向的共享池 = qseecom_region@0x9e400000（20MB shared-dma-pool，QSEE 应用用）；PAS 元数据就是普通 DRAM（vendor 内核用 dma_alloc_coherent，六镜像同机全过为证）。"SHM 桥"是 redwood（SM7325 代 TZ）的特性，SM8150 代 TZ 用 XPU 临时保护元数据（qcom_scm.c 注释原话），无需登记。zap 落位区也不在 DT：kgsl-hyp 节点没有 memory-region，pil_gpu_region@0x98f15000 无人引用（孤儿），真机 zap 用动态分配页（0xfe500000，1 页）。
- **c) 元数据可否放 hyp 已 shared 的页/先注册再 INIT**：不需要。vendor 形态 = dma_alloc_coherent 普通页（连续、4K 对齐、缓存清理后发调用）。R22 矩阵已证地址无关。R23 落位改用我们自己的新页（vendor 同款），adreno_zapmem= 可强制回 DT 区做 A/B。
- **d) INIT_IMAGE 前是否有漏掉的前置调用**：没有。vendor 全流程 = is_call_available 探针（可选）→ INIT_IMAGE(整 .mbn) → MEM_SETUP(动态页, 0x1000)（relocatable 才有）→ 写段 → AUTH_AND_RESET。唯一额外动作 = qcom_scm_clk_enable（DT 时钟；本机 DT 无 scm 节点，非判别项——spss 先例证明时钟全开照样能拒镜像）。

### 文件取证（本案钥匙）
- 随卷 a640_zap.mbn（14256B，sha256 a17975ca…，R13 起随 md1，卷内字节与 fw-gpu-raphael 全等）哈希段尾证书链：**"Generated Test Attestation CA" / "Generated Test Root CA" / SecTools / San Diego / CDMA Technologies / "General Use Test Key (for testing only)"** —— 高通 SecTools 测试链，非量产签名。
- phdr 表：phdr[0] type 0（头对）、phdr[1] type 0 + 哈希标记 bit24-26（0x1a38@0x1000）、phdr[2] PT_LOAD（0x7b0@0x3000, relocatable）。解析器选段正确；调用形态与 vendor 完全一致。
- 真机对照（sm8150 同谱系 4.14 内核，PIL 内存图与本机一致：ipa@0x98f00000/spss@0x99000000/cdsp@0x99100000 全同）：zap 三连成功、spss 拒绝同码。

### 内核改动（raphael_adreno.c，+176/−54）
- `zap_chain_has()`：哈希 blob 内有界查针。
- `zap_header()`：发调用前扫描证书链可读名（前 6 个）+ 测试签名有界查证，打印 **"THE SIGNATURE CHAIN SAYS: A TEST KEY …"** 或 "no test-key marks"。
- step 19：矩阵退役，单一规范尝试（整 .mbn + 可抢占）；落位 = 自家新页（vendor 同款，0x1000 授权），`adreno_zapmem=<phys>` 可强制 DT 区；拒绝路径加**零页对照**（GPU id / audio id 各一发，同码=调用级、换码=镜像级）。
- 门禁：+15 条 R23 标记、−7 条 R22 矩阵标记（退役清单入 CI）。

## R24 · 换血：本机原厂 zap 文件

**R23 实锤闭环：判词自报 "A TEST KEY"、零页对照 -3145738 换码（TZ 读得动缓冲、拒的是镜像；-(0x3000xx) 码族实证：0x0A=零页解析拒、0x45=测试链签名拒）。R21-R23 七次同码的唯一变量 = 签名链。R24 换上本机 /vendor 原厂件。**

- 文件取证（upload/a640_zap_stock.elf，用户自本机 /vendor 提取）：14256B，sha256 627ecc6a…；与测试签名件 diff 仅 3453B（0x103c..0x1f36，全部在证书区）；ELF 头/phdr/代码段逐字节相同（同一份 zap 代码）；zap_header 验收 PASS（hash 0x1a38@0x1000、PT_LOAD 0x7b0@0x3000 同布局）；链名 "Xiaomi Attestation CA"（2021→2041，Beijing）+ "Xiaomi Root CA"（2017→2037），测试标记零命中
- 交付机制（md1 卷字节固定的破局）：新件与旧件**同长（14256B）**且卷内旧件**唯一命中（0x7b000）**⇒ APFS 卷内同长字节置换（演练于 r23 md1：置换后 NXSB 完好、测试链清零、新件唯一；测试链 2 处均在文件区间内随之清零）
- 内核改动（scripts/r24_patch_c.py 字节锚定×5 全命中，配平 0/0）：三岔判词（A TEST KEY 防旧件回流 / THE PHONE'S OWN PRODUCTION CHAIN 预期判词 / 无标记兜底）+ Xiaomi Attestation CA / Xiaomi Root CA 探针 + 取证名 6→8 个；R23 全部机制保留（单一规范尝试、零页对照、动态页落位 adreno_zapmem=、签名链取证）
- 流水线：tools/r24-flash-build.sh + .github/workflows/r24-springboard.yml（新增 ④b 同长置换步：root.apfs 与 .volumes 缓存源同换、幂等、置换后三重断言；打包门禁硬校验"卷内必须有小米链、必须无测试链"；门禁 +4/−1）
- 红线遵守：不 stage md1（boot-only 不变）；只用 .elf 不用 .mdt；零页对照全保留

## R28g · T2 对照轮：adsp 的 .mdt 走 zap 的同一条调用链（2026-10-25）

- （补记：R25-R28d 的 round 记录未随轮入仓——本仓 git log 各轮 commit
  message 即其存档（r25/r25b/r26/r27/r28/r28b/r28c/r28d，连同各轮
  workflow 与 flash-build 内的判读树全文）；R8 规矩自本轮恢复。）
- 立案（T1 判决之后）：官方包 = 本机固件集（hyp/tz/devcfg/storsec
  逐字节），随卷 zap = 包内 /firmware/a640_zap.elf（sha 全等）——
  「版本错配」死亡，TZ 拒的是自家原厂 stock zap。r28d 日志唯一从未
  动过的变量：换一个本机原厂真固件、走同一条调用链，隔离「文件」
  与「路径」。判读树（用户裁决）：0 → 链路无罪，进 T3（secapp
  0x49）；-3145797 → 与 zap 同墙（全局性：查 hyp 包装壳与 secapp
  会话）；-3145738 → 数据没到 TZ（hyp 本地解析挂，查包装形态）；
  其它 → 新证据进 T3。
- 内核（xnu/files/bsd/dev/raphael_adreno.c，+165/−5）：
  · 新开关 adreno_test_adsp（默认 0；PE_parse_boot_argn 仿
    adreno_e1_mode）。=1 时 step 19 走 adsp_pas_test() 而不是 zap
    尝试——zap 全路径（找段、链判词、E1、落位、MEM_SETUP、写段、
    认证）按设计跳过；开关 0 时行为与 r28d 逐字节相同（zap 路径
    零改动）。
  · adsp_pas_test()：与 zap 尝试完全同形——image_file("fw",
    "adsp.mbn") 读档（in-place）→ ELF32 描述（15 段/phoff 0x34/
    hash 段 0x1c78@0x1000，e_phentsize 按 16 位读）→ 证书链扫描
    （整文件有界；本 .mdt 的证书在 phdr 表与 hash 段之间，不在
    hash 段内）→ 自家新页 + to_memory → 0x42000201 可抢占两参数
    （VALUE_AND_BUFFER，size 留家）→ "T2 VERDICT:" 三岔判决行 →
    give-up 交回 → "the step stops here by design"。R26 纪律全程
    （put_the_log_away 包住 E1 与判决行）。
  · 代数戳换词 R28d → R28g（驱动首行 + step 1 + step 19 基线行，
    基线行加 adreno_test_adsp 自报）；R28d 旧措辞进退役表。
- 小卷换血（tools/r28g-volume-build.py，本条即该轮产出）：fw/
  adsp.mbn = fw-gpu-raphael/adsp.mbn = NON-HLOS FAT16 image/
  ADSP.MDT（7,820 B，Xiaomi Attestation CA/Xiaomi Root CA，无测试
  链标记）——不是补丁而是重建：上游 apfs.py 读 R28d 卷（80 项，
  解压 decmpfs）→ 物化目录树 + manifest → mkapfs.py -z --plain fw
  重打（R13 卷的同款配方）→ 自证四条（80 项逐字节原样 / 新件恰为
  源文件 / 四件 fw 全部单 extent 原位 / 小米链在与测试链绝）。
  实测：1024 块容器不变，844 free，adsp.mbn 落 block 127（zap
  extent 紧后），r24b 审计 gate PASS + 7z 第三读者。
- 流水线：tools/r28g-flash-build.sh（外科手术换三条目：kernel +
  ramdisk + GPU 镜像 cmdline 加 adreno_test_adsp=1；主镜像 cmdline
  不动；装载器 v7 断言原字节；打包后离线复证三条目 + 截断律）+
  tools/r28g-kernel-id.py（KNOWN 表加 R28g 卷 870650e3…；代数
  grep 双 VERDICT）+ .github/workflows/r28g-springboard.yml
  （②增克隆上游 userland 借 tools/apfs；③门禁 R11-R28g 全表 +
  R28d 两条措辞退役；⑤打包复证；缓存键迁 r28g-ccache-）。
- 静态校验：源码标记串正反核对（R28g 代数 2/基线 1/T2 宣告 2/
  判决 3/开关 3；R28d 旧措辞 0）；括号平衡；无 tab/CR；
  yaml 解析过；bash -n 过；本地全流程演练（合成门禁形内核）双镜
  像出、三方验证全过。
- 判读树全文：Release README.txt（workflow ⑥ 生成）；日志预期
  行样例：fw/adsp.mbn: 7820 bytes in the image of /dev/md1 →
  an ELF of 15 segments → THE ADSP'S CHAIN SAYS: THE PHONE'S
  OWN PRODUCTION CHAIN → T2 armed → T2 VERDICT:。

### R28g 发布记录（补）
- CI run 40203429335（前置两轮排障：run 40201979174 loader 门禁 grep 对齐
  笔误；run 40202199652 底板资产/上游 userland 的克隆落位；run
  40202807172 kernel-id 的卷判决由 sha 匹配改为结构判——zlib 压缩形态随
  构建机的库变，跨机字节不同而内容逐项相等，flash-build 的自证在构建
  机上跑过即为准）。
- 内核 sha256：9cbcac8a4d66b1a64ec3e7b723055be367027ec190d9ef89b7c8bb0e106855a3
  （15,062,568 B，clean build；代数 grep：代数戳 2/基线 1/T2 宣告 2/
  判决 3/开关 3；R28d 旧措辞 0）——已入 r28g-kernel-id.py KNOWN 表。
- 四资产公网 sha256（api_size 全等，Task 20 纪律）：
  f17f46acac0401acc9bcd1b34c059ee4be52ad0518c89e7e6604b855a77322c1  raphael-xnu-r28g-flash.img（7,979,119 B）
  49d25cf17ad2b71b2c2a302d676c25b4a680fbe0e5b8c33de666f8aaa57b85b0  raphael-xnu-r28g-gpu-flash.img（7,979,119 B）
  14485e7c5ce125ef516168445c2500ac04ca5e40a908c2aef516b2d92c24ba0c  raphael-r28g-flashboot.apfs（= R13 全量字节，逐字节原样）
  9308eae198d20a5d257c6dc317e1e0b3c09c3e520bcce1f2b364a63118842bbf  r28g-md1-root.apfs（本机沙箱同输入重建为 870650e3…，zlib 形态差异，内容逐项相等）
- 直链：https://github.com/Gsjsjzhznsz/hackios-xnu-kernel/releases/download/r28g-springboard/
  （raphael-xnu-r28g-flash.img / raphael-xnu-r28g-gpu-flash.img /
   raphael-r28g-flashboot.apfs / r28g-md1-root.apfs / SHA256SUMS / README.txt）

## R28h · 二分判决：死因=重建卷；R28g 世代内核另有 lsd SIGBUS 回归（2026-10-26）

- R28G 黑屏死机（零日志、未到 xnuloader、未到 step 19）后按用户指令出二分
  对照：A = 去 T2 补丁 + adsp.mbn 卷；B = T2 补丁内核 + R28d 原卷（不重建）。
- **B 镜像真机判决（r28h-b-logdump，465,856 B）**：
  ① 内核存活——装载器 v7 全程 flush 配对，跑到 122 s，心跳 13，用户态
  全起（launchd/backboardd/notifyd/runningboardd/powerd/analyticsd…），
  backboardd 渲染白苹果（surface 1125x2436）——**R28G 第一秒死机的元凶
  = R28g 重建的 md1 卷**（B 镜像与 R28G 唯一差异就是卷），内核无罪；
  ② T2 未触发——B 镜像 cmdline 沿 R28d 原样、未带 adreno_test_adsp=1，
  日志自报 "adreno_test_adsp=0 (off; the zap attempt, as every round)"，
  zap 照旧 refused -3145797（-0x300045）——整轮落空，教训入 R28i（默认开）；
  ③ **新问题：系统性用户态 SIGBUS**——lsd[29→121] 无限循环 SIGBUS（"exited
  due to SIGBUS | sent by exc handler"），analyticsd[31/35/40]、
  mobilegestalt.xpc[28/37] 同崩——LaunchServices 注册完不成，SpringBoard
  卡白苹果。R28d 内核（同 cmdline、同卷、同用户态）从未如此（r28d 日志
  lsd[29] 正常 running）——**R28g 世代二进制引入的回归**，且非 T2 运行时
  （T2 没跑）：嫌疑头名 = 构建环境漂移——r25-theos-v1 / r25-xnu-deps-v1
  缓存键带跨代 restore-keys 链（r23→r14），两次 run 可能拿到不同代的
  theos/xnu-deps；次嫌疑 = runner 镜像滚动的 apt clang；再次 = T2 代码的
  静态布局踩雷（R28i 判读树预注册了分离实验）。
- L1ghtmann/llvm-project releases 核查：latest = v19.0.0-L2（2024-04-21
  发布，至今未动）——工具链 tarball 本身未漂移；漂移面收窄到缓存链跨代
  与 runner 镜像两层。

## R28i · T2 对照二版：内嵌 adsp + 默认开启 + 工具链钉死（2026-10-26）

- 立案（用户三项指令）：① 清理/固定 CI 工具链，复现 R28d 级稳定的内核；
  ② T2 开关默认强制开启（刷机不带参数）；③ 用 R28d 同样干净的底座打包，
  一刷既测 T2 又不卡白苹果。
- **工具链钉死（workflow ③ + ②c/d）**：
  · L1ghtmann 工具链 tarball 从 releases/latest 改钉具名 tag
    v19.0.0-L2（该 tag 即 R28d/R28g 实际拉到的字节，纯收紧零变化）；
  · theos / xnu-deps 缓存键全新（r28i-theos-v19L2-v1 / r28i-xnu-deps-v1）
    且**删除 restore-keys 链**——首跑必 miss、从钉死源确定性重建，跨代
    混用从结构上不再可能（等效"清理缓存"，且重建可复现）；
  · 新增 toolchain-versions.txt（随 release 交付）：runner 镜像 / apt
    clang / lld / dsymutil / theos ld64 全版本号 + tarball 具名 tag——
    未来任何一轮"同源码不同行为"先对这张表；
  · ccache 沿 R28b/R28d/R28g 零回填 clean build 纪律。
- **T2 默认开启（raphael_adreno.c step_zap_load）**：test_adsp 默认
  0→1（"the flasher types no arguments, so the default IS the round"），
  PE_parse_boot_argn 保留——不传=开，adreno_test_adsp=0 具名关闭（逃生门）。
  cmdline 本轮零改动（R28d 原字节，无 token）。
- **adsp 的 .mdt 内嵌进内核**（xnu/files/bsd/dev/raphael_adsp_fw.h，新）：
  fw-gpu-raphael/adsp.mbn（7,820 B，sha256 f5881fc760e8…，与 R28g 卷里
  那份逐字节相同）按 redwood_focaltech_fw.h 先例转 C 数组（scripts/
  r28i_mkfwheader.py 生成 + 字节级往返校验）；adsp_pas_test() 弃
  image_file("fw","adsp.mbn") 改读内嵌数组（"the volume is not read"）——
  R28G 死因（重建卷）从结构上移除，T2 数据源零卷变量；R28g 的
  "no fw/adsp.mbn in the small image" 缺件判词随之退役（此路径已死）。
- **最小手术（tools/r28i-flash-build.sh，新）**：底板四件 = R28d release
  官方字节（workflow ② 对官方 SHA256SUMS 硬校验：95278e76…/ef441b1c…/
  14485e7c…/372c7fcb…）；手术只换 kernel 条目——ramdisk 断言逐字节 ==
  R28d 原卷（372c7fcb…）、cmdline 断言逐字节 R28d 且无 T2 token、loader
  断言 v7（ec15106d…）、DTB/头字段/截断律原样；one kernel, both images
  （双镜像 kernel/ramdisk 一致性二重断言）。
- **门禁**：R28i 标记正查（代数戳 2/基线 1/T2 宣告 2/内嵌副本 2/判决 3/
  开关 3/fw 名 1）+ R28g 三条措辞与 R28g 缺件判词反查 0 + 内嵌 .mdt
  字节级证明（k.find(fw)>=0 且唯一）+ minimac 豁免行保留 + 新 sha 断言
  （R27/R28/R28b/R28d/R28g 全不允许）+ R11-R27 历史保留标记全表。
- **判读树（预注册分诊，release README.txt 全文）**：
  第一刷主镜像（无 gpu=1，T2/GPU 步骤不跑）= lsd 控制组——进桌面 →
  环境已复原，刷第二张；仍 SIGBUS → 环境仍有暗变量或 T2 静态布局踩雷
  → 下一轮 R28i-minus（R28d 源码原样重编）分离两变量。
  第二刷 GPU 镜像（gpu=1）= T2 对照——证据链四行（内嵌副本 7,820 B →
  ELF 15 段 → THE ADSP'S CHAIN SAYS → T2 armed）→ "T2 VERDICT:" 三岔
  （0→T3；-3145797→全局墙查 hyp/secapp；-3145738→查包装；其它→新证据）。
  主镜像不崩 + GPU 镜像崩 → GPU 步骤/T2 运行时问题（新证据）。
- 静态校验：源码标记串正反核对（R28i 代数 2/基线 1/T2 宣告 2/内嵌副本 2
  （announce 跨行拼接后）/判决 3/开关 3；R28g 旧措辞 0）；kernel-id 与
  flash-build bash -n / ast.parse 过；workflow yaml 解析过 + 缓存结构
  断言（3 键全新、无 restore 链）+ curl 行断言（tarball 具名 tag、无
  floating latest）；头文件字节级往返校验过。
- 待办：推送触发 CI（本沙箱无 GitHub 凭据——r28g 是当时会话有 token 推
  的；本轮材料备齐待推），构建成功后注册内核 sha 入 KNOWN 表 + 发布记录。

## R28j · 真机判决：J1 —— 同 boot 闭环成立，T3 转正（2026-10-26）

**输入**：用户回传 `r28j-logdump.txt`（GPU 镜像 boot：cmdline `rd=md0 … touchscreen=1 buttons=1
cpus=8 ufs=2 bootcpu=7 root_apfs=ufs_userdata gpu=1`、`R28 cmdline: the extras riding this
boot: (none)` —— R28j 默认参数零 token 实证；单块 `== XNU FLUSH #37 at 60 s ==`，214,609 B，
loader flush #1..#37 全程配对（每次 read back the same (0)），内核全程存活无复位）。

**J1 判决（判读树原文命中）**：

1. **T2 先行且通过**：`T2 armed: smc 0x42000201, arguments word 0x82, x2 1 (the pas id),
   x3 0xfef9c000 (the metadata page), x4 0`；T2 页检查过（`x3 0xfef9c000 is page-aligned,
   the metadata 7820 bytes (one page, the whole .mdt in it)`）；内嵌副本自证
   （`fw/adsp.mbn: 7820 bytes, the kernel's own embedded copy (sha256 f5881fc7...)`）；链读出
   `THE ADSP'S CHAIN SAYS: THE PHONE'S OWN PRODUCTION CHAIN ("Xiaomi Attestation CA"/"Xiaomi
   Root CA")`；调用被抢占 1 次后应答 → **`T2 VERDICT: ACCEPTED (0) -- the XNU→Hyp→TZ chain is
   sound; the zap's refusal is zap-specific: go T3`**（判决行逐字在场）；adsp give-up
   （`smc 0xc2000206, arguments 0x1` → returns 0, answer 0）干净交还。
2. **zap 紧随同 boot**：空探针 `smc 0xc2000601` → answer 0x1（到达时系统活着）；链读出
   `THE SIGNATURE CHAIN SAYS: THE PHONE'S OWN PRODUCTION CHAIN -- this is the file this
   phone's vendor ships: TrustZone should take it`；E1 页检查过（`x3 0xff020000 is
   page-aligned, the metadata one page (the whole 14256-byte file in it)`）；`E1 armed:
   smc 0x42000201, arguments word 0x82, x2 13, x3 0xff020000, x4 0` →
   **`TrustZone returns -3145797, answer 0` → `refused: the call returns -3145797`** ——
   与 R28d 之墙同码；zap give-up（0xd → 0/0）干净，结尾收尾 give-up 再 0/0。
3. **J1 成立**：T2 0 + zap -3145797，同 boot 背靠背、各带 give-up、两半之间只有文件/id/
   读取来源三处不同（卷 ride R28d 原字节）→ 序贯对照闭环 → **T3 转正**。

**对照组新证据（T3 台账，本轮新增）**：

1. **零页双查**：`smc 0x42000201, arguments 0xd, 0xff034000, 0`（GPU id × 零页）→
   **-3145738**；`arguments 0x1, 0xff034000, 0`（audio id × 同零页）→ **-3145738** ——
   与真 zap 的 -3145797 **不同码** ⇒ secapp 确实读取并解析了缓冲区。拒绝码分层钉死：
   **-3145738 = 元数据解析层拒绝**（垃圾输入，与 pas id 无关），**-3145797 = 签名/身份层
   拒绝**（真镜像、原厂签名仍被拒）。zap 之墙与通道/形态/页/调用史全部无关（R21-R24b
   形态、页对齐、同通道、同 boot 内 adsp 全过）。
2. **is_supported 双查**：`smc 0xc2000207, arguments 0xd` 与 `arguments 0x1` → 均
   returns 0, answer 0 —— 平台层对 GPU pas id 无显式禁令，拒绝只针对镜像本身。
3. **Q5 设备侧作答（两条 0x40 哈希段头 dump 在案）**：adsp `@0x1000:
   30820122 300d0609 2a864886f70d01010105 0003 82010f00 3082010a 0282010100 bb375441…`
   （DER RSA 公钥签名表头）；zap `@0x1000: 00000000 06000000 00000000 00000000
   90190000 90000000 ffffffff 00010000 ffffffff 00180000 00000000 78000000 00000000
   00000000 14000000 e1500a00`（v6 元数据头表：版本 6、段尺寸/哈希尺寸/幂字段）——
   两镜像哈希段结构根本不同，T3 离线解析 secapp `cmd 0x49` 的直接输入。

**系统侧**：step 19 FAILED → `the steps after it are not attempted` → `THE GPU IS NOT
STARTED: step 19 (the zap shader) failed; the system draws with its software renderer`；
GMU 收 slumber（message 33, 3 words）+ `cm3_sysreset` 写 1 + SMMU（smr/s2cr）清理——
优雅降级全程无复位；用户态照常起（backboardd `startup complete. 8.03521s elapsed`、
SpringBoard pid/7 created、RAPHÆL TOUCH/buttons 正常上岗）。

**⚠️ 次要观察（与 J1 无关但必须记录）：lsd SIGBUS 回归在本轮 GPU 镜像 boot 再度在场。**
**（2026-10-26 改判：本节的“回拨”与“SIGBUS”均非本轮 boot 自有内容，见下一条取证；
真正属于本轮 GPU boot 的是 +4 min 时钟跳变，风暴归前代 boot 残迹。）**
launchd 戳非单调（00:05:29 → 00:01:14 回拨，用户态时钟把戏，仅记档不影响判决）后进入
崩溃循环：`lsd[39] exited due to SIGBUS | sent by exc handler … service has crashed 4
times in a row`（后续 lsd[41]/[42]、analyticsd[35]/[40]，tail 处 analyticsd[121] 仍在
重生循环）。跨 boot 对照表：R28d 内核（gpu=1，zap 每轮拒绝）lsd 干净；R28h-B（R28g 代
内核，T2 在树未武装）SIGBUS；R28i 主镜像（无 gpu=1，adreno 全跳过）干净（README：R28i
真机点亮 SpringBoard）；本轮 R28j GPU 镜像（T2 武装 + zap）SIGBUS。⇒ 回归与"新世代
内核 + adreno 轮实际执行"共现；与 T2 的 smc 调用本身无必然（R28h-B 反例）。
**控制组确认（2026-10-26 操作者确认）**：本轮只以脚本 boot 了 GPU 镜像（脚本内无任何
R28j 主镜像 flash/boot 步骤）——主镜像未刷、未 boot、进桌面未验证。预注册树"先主镜像
（lsd 控制组）后 GPU 镜像"的顺序本轮被倒置，控制组半边未执行 ⇒ "主镜像干净 / 也
SIGBUS"二分暂不可判，adreno 世代代码路径与环境/构建面两个嫌疑均未排除，
R28i-minus（R28d 源码原样重编）保持候补、未转正。
**时钟序列入账**：本轮确立"回拨（00:05:29 → 00:01:14）先于 lsd 崩溃循环"的先后时序；
但回拨是否各 boot 普遍发生无跨 boot 记录，而 R28i 主镜像干净 ⇒ 回拨单独不充分（若
普遍发生）。时钟触发器假设与 adreno 共现假设并列，由主镜像控制组 boot 的时钟记录
分诊——主镜像 boot 一石三鸟：控制组 + 进桌面验证 + 时钟行为基线。

**Stage Summary**：
- R28j 判决 = **J1：同 boot 闭环成立，T3 转正**；zap 之墙定性收敛为"secapp 对 stock zap
  镜像的身份拒绝"（-3145797），解析层（-3145738）与身份层分离由零页双查钉死
- T3 台账齐备：两条 0x40 哈希段头 dump（Q5）、双码分层、is_supported 双 0、链读出两份
- 遗留：lsd SIGBUS 归因悬空——主镜像控制组本轮未执行（未刷未 boot，操作者确认），
  "干净/也崩"二分不可判；adreno 世代共现与时钟回拨触发器两假设并列，待主镜像
  控制组 boot 分诊；R28i-minus 保持候补

## R28j 主镜像控制组判决 + logdump 残迹取证：风暴与"回拨"改判，时钟 +4 min 跳变归 GPU boot 自有（2026-10-26）

**输入**：操作者补刷 R28j 主镜像并回传 `r28j-main-logdump.txt`（cmdline 无 gpu=1、
extras none、头行 "FLUSH #30 at 60 s; 175476 bytes follow"；文件实收 466,703 B）。

**残迹取证（字节级，可复现）**：
1. main[1924..4313] ≡ gpu[2166..4554]，diff 仅 4 行（全部为拼接缝的行首截断）。
2. main 自有写边界 = byte 175,476（行 1922，末戳 00:01:24.932499，写尾腰斩
   `buttons: reading GPIO6 of the PM8150: t`）；gpu 自有写边界 = byte 214,609
   （行 2481，末戳 00:05:29.827985，写尾同样腰斩）。
3. 两文件实收 ~467 KB = 同一固定读长 ⇒ 回读没有按头行声明的字节数读，把分区里
   前代 boot 的内容原样拖进了文件。**工具缺陷：回读必须严格按头行字节数截断。**
4. 指针计数轨迹（"N s: the kernel has logged"）：main 26 s→36 s 仅 +465 B（纯
   coalesce 噪声 = 日志安静），log 份量 142,137 B；gpu 30 s 起 180,797 B 冻结。
   两边的 log 段 = 指针有界，段内内容全为各自 boot 自有。

**改判**：
1. **lsd/analyticsd SIGBUS 风暴（lsd[39] 4 连崩 → analyticsd[121]）不属于 R28j 任何
   一个 boot** —— 它在 gpu 自有写边界（byte 214,609）之下的分区残迹里，是 R28j GPU
   boot 之前某个 boot 的自有记录（疑 R28h-B 一系或更早 GPU 轮；身份待旧档重审计）。
2. **"时钟回拨 00:05:29 → 00:01:14"没有任何单个 boot 发生过** —— 00:05:29.827985 是
   R28j GPU boot 自有记录的终点；00:01:14.496777 起是前代 boot 的中段内容；交界处
   `d|...` 腰斩行 = GPU 写尾切掉前代行首。"用户态时钟把戏"一段作废。
3. **GPU boot 自己的真实异常（指针计数在案）：墙上时钟在 T2/zap 序贯对照 + give-up
   收尾后数秒内 +4 min 跳变**（自有 log：1x 正常戳 → 00:04:59.205118 直进，跳变点
   log 字节 ~141.6 KB ≈ mark 10-16 s，紧随 step 19 拒绝收尾；异常窗 00:04:59→
   00:05:29.827985 全在指针计数内）。跳变源未定位（仓库内 settimeofday/clock_settime
   零命中）→ 台账 Q-new：跳变 setter 是谁。
4. **主镜像控制组判决：0→38 s 全程 1x 干净**（无跳变、无风暴、lsd[29] 存活；26 s 起
   日志安静）。GPU boot 跳变在 ~10-16 s ⇒ 控制组覆盖跳变窗口 ⇒ **跳变与 adreno/T2/zap
   相位强相关**（"GPU 相关性"以自有记录级证据回归；R28i-minus 分离实验退役）。
5. 机制假设（工作假设）：跳变 +4 min → ~10-15 s 后一次回真同步（前代 boot 的
   00:01:14.496777 = 其回真点，mark+anchor 恰合真值）→ lsd/analyticsd 在回真瞬间
   SIGBUS（前代风暴起于回真 +0.03 s）。R28j GPU boot 捕获止于回真之前，故自有记录
   无风暴。⇒ **谁设 +4 min、谁回真，是同一条链上的两个口**。
6. 跨 boot 对照表（R28d 干净 / R28h-B SIGBUS / R28i 干净）：每份 logdump = 自有新鲜区
   + 分区残迹，只有落在自有区且在指针计数内的观察才归该 boot —— 全表待按此重审计。
7. **J1 判决不受影响**：T2/zap 全部证据（内嵌自证、链读出、VERDICT、give-up、零页
   双查、is_supported、Q5 双 dump）都在 gpu 自有新鲜区内。

**R28k 预注册（本轮唯一代码变量：被 TZ 拒绝的 step 19 不再终止 run）**：
- 依据：R27 注释在案 —— 早前 two-parameter run 曾在被拒后继续（26 s 死亡，R27 因此给
  20/21/22 加前置 flush 守卫），此后默认拒即停。R28k 恢复"记录后继续"，仅限
  TrustZone 见过镜像并拒绝（refused: 标签）的情形；内核代次标签升 R28k。
- K1：步 20/21 完成、core_runs ⇒ GPU 无 zap 常驻（keep=1 默认已开）→ RedwoodGPUStart
  —— 下一站 IOGPU/Metal；zap 之墙继续 T3 离线逆向。
- K2：20/21/22 死亡/复位 ⇒ zap 硬前提，T3 升关键路径（R27 守卫保底记录）。
- K3：+4 min 跳变在 R28k 自有记录复现 ⇒ 跳变绑 adreno 相位本身（非拒即停路径），
  时钟问题独立立项。
- K4：跳变不现 ⇒ 跳变与拒即停/收尾序列相关，下一轮单查 give-up/清理路径。
- 附带：未来 logdump 尾部仍会出现前代残迹 —— 判读只看自有区（写边界 + 指针计数）。

**Stage Summary**：
- 风暴与"回拨"改判为残迹假象；GPU boot 自有异常 = +4 min 时钟跳变（T2/zap 后数秒），
  与主镜像同段位 1x 干净形成对照 ⇒ adreno 相位进函数链
- 工具缺陷在案：logdump 回读按固定 ~467 KB 而非头行字节数 —— 下轮起修正
- R28k 已推（step 19 拒绝后继续）；待 CI 构建 + 真机 K1-K4 分诊
- Q-new：+4 min 跳变 setter / 回真同步者，两口同链

## R28k · 真机判决：K4 命中（跳变不现）；K1/K2 之间落新叶——zap 是"出安全模式"的硬前提而非 core 存活的前提；lsd 风暴转正为本轮自有、白苹果直接阻断点（2026-10-26）

**输入**：操作者回传 `r28k-gpu-logdump.txt`（R28k GPU 镜像 boot：头行 "XNU FLUSH #36 at 153 s;
449185 bytes follow"，trunc1 副本 449,130 B 相符 ⇒ 本轮回读按头行字节数取全，kernel log 为
自有新鲜区、无前代残迹混入；`R28 cmdline: the extras riding this boot: (none)`，gpu=1 在 cmdline）。

**主线证据（step 19→21，R28k 变量"拒绝后继续"全程在场）**：
1. T2 对照照常：`T2 VERDICT: ACCEPTED (0)`（内嵌 adsp .mdt 同 boot 前置）。
2. E1 zap 照常被拒：`smc 0x42000201, arguments 0xd, 0xff00c000, 0` → -3145797（`refused:` 标签
   在案，R28k 放行条件命中）；give-up ×2 各 returns 0（拒后 + 控制后）；零页双查 -3145738 ×2、
   is_supported ×2 answer 0 —— 与 R28j 台账逐项一致（T3 证据链复现）。
3. **step 19 REFUSED → R28k 放行**：`step 19: REFUSED -- the record is complete above; R28k: the
   run goes on to the 3D core without the zap`（拒即停自本轮起废止）。
4. **step 20 通过（R28 系首次到达的硬件相位）**：CP 启动 `alive after it; status 0x1`；init 消费
   `ring read up to 9, status 0x1` → **`20: THE 3D CORE TAKES COMMANDS`** —— 无 zap 常驻，
   3D core 上电、CP 起来、ring 命令照吃。
5. **step 21 失败（卡点精确定位）**：`command the 3D core out of secure mode (it asks the zap
   shader), then to write a word into a page of ours`：出安全模式命令被 CP **消费**
   （`ring read up to 11, status 0x1`，消费 ≠ 生效）；随后主存写入测试 ——
   `ring read up to 15 of 15, status 0x800005, interrupts 0, command processor: fault 0,
   interrupts 0x1, protection 0`；`the GMU sees it busy: 0x70800005` —— ring 全被吃掉、字没落进
   主存、CP 中断挂 1 位。驱动 doctrine（raphael_adreno.c 42-43 行 "the zap shader … without
   which the 3D core stays locked in secure mode"；step 21 = CP_SET_SECURE_MODE 0x66，3031 行）
   本轮硬件层验证：**CP 吃命令但出不了安全模式，主存写被安全态吞掉**。
6. 优雅失败全程：`step 21: FAILED; the steps after it are not attempted` → `THE GPU IS NOT
   STARTED: step 21 (the 3D core's first commands) failed; the system draws with its software
   renderer` → GMU slumber（message 33）+ cm3_sysreset + SMMU 清理 + gbif halt/let-go（core
   跑过才有此 cycle）→ `over; the system goes on starting` —— 内核存活到 FLUSH #36（153 s），
   无死亡无复位（**K2 不触发**）。

**K3/K4 判决：K4 命中——+4 min 跳变未复现**：
- 全量时间戳扫描（2,829 枚用户态戳）：first 00:00:39.778 → last 00:03:04.79，各连续段单调，
  无任何 +4 min 量级跳变；最大正向间隙 27.7 s（00:01:18→00:01:46 风暴前静默窗）；用户态相对
  内核 mark 的基线偏移全程恒定（~+31 s：末戳 184.8 s ↔ FLUSH #36 mark 153 s 精确闭合）。
- 按 K4 预注册：跳变与拒即停/收尾序列相关。R28k 数据进一步收窄：R28k 同样有 give-up ×2 与全
  teardown（slumber/sysreset/SMMU/gbif）却无跳变 ⇒ 跳变绑的不是"拒绝+give-up+清理"泛型，而是
  **"core 从未启动即收尾"那条特定路径**（R28j 拒后即停、core 未启动 → 跳变；R28k 跑完 20/21、
  core 启动过 → 无跳变）。Q-new 继续挂账，降级支线。
- 唯一非单调点 = 日志环接缝伪影：风暴把内核日志环打满一圈，接缝处腰斩行（`nchd|` 行头）+
  5.3 s 窗口重排（00:03:04.79 之后再入 00:02:26.38..00:02:31.65 段）。工具规则入账：
  **风暴期 logdump 尾部可出现环绕接缝，判读须扫全文件找接缝，不得默认行序=时序**。

**lsd 风暴：本轮自有实锤（非残迹）+ 机制级新证据**：
- 风暴在本轮写边界与指针计数内：首发 **lsd[29] SIGBUS @ 00:01:46.623310**（lsd[29] 为本 boot
  原生 pid，00:00:46.8 出生后正常服务 1 分钟），随后重生即崩，死亡行 84 条（计数器峰值
  `service has crashed 78 times in a row` @ 00:02:31.5，lsd[120]），持续到日志终点 00:03:04.79
  （= flush 瞬间）。
- **首发死亡与 backboardd 的 LaunchServices 数据库拉取毫秒级同步**：00:01:46.623052
  `backboardd[9:357] error on blocking retry to fetch database … 4097` → 00:01:46.624079
  `LaunchServices: Database mapping failed, retries = 0` → 00:01:46.624177
  `disconnect event interruption received for service com.apple.lsd.mapdb` ↔ lsd[29] 死亡
  00:01:46.623310。⇒ SIGBUS 落在 lsd 服务 mapdb 映射请求、touch 映射页的瞬间；嫌疑收敛为
  "lsd 数据库映射页不可 fault-in"（卷上文件截断/损坏 vs 内核 fault 路径拒绝，两假设并列，
  待主镜像分诊）。
- 本轮风暴 **analyticsd 零死亡**（捕获窗内；R28j 时代残迹风暴为 lsd+analyticsd 双死）——签名
  差异入账，捕获窗截断效应注意（analyticsd[121] 00:02:31.6 才重生）。
- 与 R28j 改判链闭环：R28j 自有记录（跳变、无风暴）+ R28k 自有记录（无跳变、有风暴）⇒
  **"跳变→回真→SIGBUS"因果链作废**：lsd SIGBUS 不需要时钟跳变，独立事件；adreno 世代共现
  假设保留但触发面二分待主镜像分诊。

**白苹果阻断点判决**：
- SpringBoard[7] 全程存活零崩溃（00:00:39.86 spawn、00:00:53.5 连 backboardd SystemShell、
  ps 在案 pid 7 CPU 2.68 s、无任何 exit/signal 记录）——**不是"无法存活"，是"起不完"**：
  lsd 死循环令 LaunchServices 不可用，shell 交接无法完成，backboardd PUIProgressWindow
  （白苹果）驻留。
- 软件渲染不是阻断点：`CoreAnimation: Failed to initialize Metal or OpenGLES contexts!` +
  `Software renderer! Simulating crash!`（00:00:53.47）与 R28i 进桌面时同款（R28i 先例：
  no GPU draws 仍点亮 SpringBoard）⇒ 软渲染可容忍，lsd 不可容忍。

**三问判决（对应操作者三问）**：
1. K1 达成度：**部分达成（K1/K2 之间新叶）**——"GPU 无 zap 常驻"硬件层被证明可容忍到
   "core 活 + CP 吃命令"（step 20 通过），但 K1 全文条件（20/21 完成、core_runs →
   RedwoodGPUStart）不成立：step 21 出安全模式失败。**zap 是"出安全模式/主存写可见"的硬前提，
   不是 core 存活的硬前提**。K2 不触发。zap 之墙继续 T3。
2. 白苹果直接阻断点：**是 lsd 死循环**（精确化：SpringBoard 存活但起不完；软渲染非阻断）。
3. 下一步最小实验：**刷 boot R28k 主镜像（同内核、无 gpu=1、adreno 全跳过）**——单变量分诊
   lsd 触发面：干净+进桌面 ⇒ adreno 相位触发面（内核侧）；仍 SIGBUS ⇒ 卷面触发（lsd mapdb
   历史损坏）→ 卷侧修复/重建。同 boot 收获：进桌面第二次验证 + 时钟 1x 基线。
   step 21 的 zap 依赖短期不可解（-3145797 身份层拒绝，T3 在途）且非进桌面必经点 ⇒
   **lsd 优先，T3 并行**。主镜像若复现 lsd SIGBUS，先捞 mapdb 文件验截断再动手。

**Stage Summary**：
- R28k 判决 = **K4 命中（跳变不现）+ K1/K2 间新叶（zap=出安全模式硬前提、非 core 存活前提）**；
  step 20 无 zap 通过为 R28 系首次到达的硬件相位（THE 3D CORE TAKES COMMANDS）
- lsd 风暴转正为本轮自有；首发死亡与 backboardd mapdb 映射请求毫秒级同步 ⇒ SIGBUS 点位锁定
  在 lsd 服务映射页瞬间；"跳变→回真→SIGBUS"链作废
- 白苹果 = lsd 死循环阻断 shell 交接（SpringBoard 存活未起完）；软渲染非阻断（R28i 先例）
- 下一轮：R28k 主镜像控制组（同内核单变量）——lsd 触发面二分 + 进桌面 + 时钟基线，一石三鸟
- 工具规则新增：风暴期 logdump 尾部可有日志环接缝（腰斩行 + 窗口重排），判读先扫接缝

## R28k · 主镜像控制组判决 + GPU 复测快照判读：lsd 触发面 = adreno 相位执行（预注册树左支命中，卷面出局）；残迹假象三度咬人——复测快照仅 60 s，风暴窗未覆盖（2026-10-26）

**输入**：①操作者口头报告"刷了 R28k 主镜像不卡白苹果了"（**主镜像日志未回传**）；②本轮上传
`r28k-gpu-logdump.txt`（467,945 B，头行 "XNU FLUSH #39 at 60 s; 220671 bytes follow"）——**R28k GPU
复测 boot 的 60 s 快照**（cmdline gpu=1 在案）；③upload 目录盘点出此前未入判读的两份：
`r28j-main-logdump.txt`（R28j 主镜像，#30 at 60 s）与 `r28k-gpu-logdump-trunc1.txt`（上轮首刷，
#36 at 153 s）。

**字节级切分取证（scripts/forensics_r28_boot_split.py 入库；按头行字节数分自有快照/前代残迹）**：
1. 复测自有区（内核 0-60 s，用户态 00:00:40.39→00:01:17.53，854 枚戳）：adreno 656 处；
   **step 19 REFUSED / step 20 `THE 3D CORE TAKES COMMANDS` / step 21 FAILED 各 1 次——R28k GPU
   序列确定性复现**（与首刷逐项同码：-3145797 / ring 消费 / 0x800005）；lsd 12 处正常活动零 SIGBUS。
2. 复测文件残留段（247,205 B，00:01:18.86→00:02:31.65）：SIGBUS 84 条、计数器 78 ×2，首崩行
   `lsd[29] @ 00:01:46.623310` 与 trunc1 逐字节一致（probe 验证 in-file = True）——**本附件的风暴
   全部是上轮首刷的分区残迹，非复测自有**；复测的风暴首发窗口（用户态 00:01:46+ ≈ 内核 ~67 s）
   **不在快照覆盖内，复测风暴状态未证**。
3. `r28j-main` 残留段（291,158 B，倒序 00:04:59.24→00:02:31.65）：首崩 `lsd[39] @ 00:01:15.526826`
   = R28j GPU 时代自有签名（4 连崩判读链一致），其内 78 连崩 @ lsd[120] 段归 R28j GPU 之前的前代——
   与 R28j 改判链完全吻合，无新事实。
4. **三份文件无一含 R28k 主镜像自有区**——主镜像侧判决证据 = 操作者现场观察（无日志佐证）。

**分诊判决（上轮 Q3 预注册树）——左支命中：触发面 = adreno 相位执行（内核侧）**：
- 主镜像（adreno 全跳过）：**不卡白苹果**（操作者观察）⇒ lsd 干净、风暴未发生。
- GPU 镜像（adreno 跑 step 1-21）：lsd SIGBUS ×78 风暴（首刷自有区实锤 + 本附件残迹双重在案）。
- 同内核（one kernel, both images）、同卷、同 lsd 二进制、同工具链，唯一变量 = adreno 相位执行
  ⇒ **SIGBUS 触发面收敛到 adreno 相位的内核侧活动**。
- **卷面假设出局**：lsd mapdb 文件若被历史硬断电损坏，主镜像同卷同文件同 mmap 必崩——主镜像干净
  ⇒ mapdb 文件未损坏 ⇒ 上轮锁定的 SIGBUS 点位（lsd 服务 mapdb 映射请求、touch 映射页瞬间
  fault-in 失败，与 backboardd 4097/"Database mapping failed" 毫秒级同步）由 adreno 相位在内核侧
  铺垫。候选机制（待 R28l 二分）：SMMU 上下文/映射改动波及用户态页、uncached/写合并分配扰动
  pmap、GMU/CP 相位的内存压力与页迁移、UFS DMA 与 apfs 缓存交互。
- 证据等级：GPU 侧 = 日志实锤；主镜像侧 = 操作者观察（无日志）。方向已定，主镜像日志回传降为
  加固项（设备侧顺手抓即可，不再阻塞）。

**GPU 复测判读**：
- R28k 轮可重复性成立：两次独立 boot 的 step 19/20/21 逐项同码同形——K1/K2 间新叶判决
  （zap = 出安全模式硬前提、非 core 存活前提）加固；T3（secapp cmd 0x49）证据链复现一致。
- 复测 60 s 窗口内 lsd 正常干活与主镜像同相（00:00:40-00:01:17）——风暴首发自内核 ~67 s 起，
  按首刷确定性推断复测同爆发；"风暴是否随机"之问待复测完整窗日志关闭（低优先）。
- K1-K4 无新增判决输入（复测快照未及 GPU 用户态下游与 +4 min 判定窗）。

**下一步（R28l 预注册）**：
- lsd 修复方向锁定内核侧 adreno 相位；**最小实验 = adreno 相位二分**：R28k GPU 镜像以 test-mask
  裁短相位（机制先例：mask 0x1c00 已在 cmdline 通路）——只读相位（step 1-9：时钟/RPMh/qfprom/
  GPUCC/SMMU ID 只读）vs 写相位（step 13+：CX/GMU FW/CP 启动）。只读相位干净 + 写相位触发 ⇒
  触发点在硬件启动写序列；只读相位即触发 ⇒ 更早的读取/映射面。每轮一个变量照旧。
- 主镜像线（已达成）：进桌面第二次验证 + 时钟 1x 基线随操作者观察入账（细节待日志顺手回传）。
- T3 并行不占设备，继续。

**工具规则（第三次入账，升级为判读前置步）**：
- logdump 回读为固定读长（~467 KB），每份文件 = 最新快照（按头行字节数）+ 前代残迹（倒序拼接，
  可跨多个 boot）。**判读第一步 = 字节级切分自有区/残迹区**，风暴/跳变/崩溃归因只认自有区。
- **判读前先盘点 upload 目录全部文件**（本轮上轮 trunc1 判读时 r28j-main 已在目录而未被检查，
  险些漏掉 R28j 主镜像自有区证据）；切分脚本入库 scripts/forensics_r28_boot_split.py，随轮复用。
- 残迹识别签名：段内时间倒序（如 00:04:59→00:02:31）、跨 boot 微秒级同款行、头行声明字节数
  与文件实长不符（差值 = 残迹长）。

**Stage Summary**：
- **lsd 触发面判决落地：adreno 相位执行（内核侧）**——主镜像干净 + GPU 镜像风暴 ×78，同内核
  单变量对照成立；卷面假设出局；lsd 修复从"修不修"进入"修哪里"（R28l 相位二分）
- R28k GPU 序列确定性复现（step 19/20/21 两次 boot 逐项同码）——K1/K2 间新叶加固，T3 继续并行
- 工具规则升级：logdump 自有区/残迹切分为判读前置步；upload 目录全盘点入纪律
- 设备侧动作（R28l 预注册）：R28k GPU 镜像 + adreno test-mask 二分（只读相位 vs 写相位），
  判读树随轮预注册

## R28l · 预注册：相位二分轮——GPU 镜像 cmdline 裁到只读相位（adreno=9，steps 1-9），写相位候审；判读树 L1-L3 与探针自检先于刷机立字（2026-10-26）

**操作者确认入账**：①"r28k-gpu-logdump.txt 确实是复测日志，且因 lsd 崩溃打满环形缓冲区导致
历史残留拼接（也是'卡白苹果'现象之所见）"——复测同爆发的操作者侧确认，R28k GPU 风暴的
确定性（首刷 + 复测两次 boot 同现象）闭环；②"r28j-main 当时跑出来确实没崩"——主镜像干净
由操作者直接确认，分诊判决的证据等级从"口头观察"升为"操作者确认"，加固完成。

**R28l 的唯一实验变量**：GPU 镜像 cmdline 追加相位 token 组——
`adreno=9 adreno_skip=7168 adreno_pause=0 adreno_watch=0`（= R28k 自动分支的硬编码形态
last_step=21→9、mask 0x1c00、pause 0、watch 0；**十进制 7168** 规避 boot-arg 解析器的
进制歧义——宣告行 "without those in mask %#x" 是 mask 落地的真机自证探针）。只读相位
steps 1-9 本轮跑（时钟树/熔丝/寄存器读；step 4/5 只写时钟寄存器，不碰内存映射面）；
写相位（SMMU context bank 13、GMU FW 与启动、zap/T2、3D core）候审。内核侧只动三处
字符串：宣告行代数戳 R28k→R28l、step 1 flush 自测行 R28k→R28l、新增相位二分宣告行
×1；R28k 的 run-goes-on 措辞原样保留（step 19 路径本轮不达，字符串在场）；T2 全部
措辞原样（T2/zap 路径候审）。gpu_block_stop() 全条件化（core_runs/core_is_on/
gmu_talks/gmu_runs/zap_is_loaded 标志驱动）——step 9 截断时全部自跳过，收尾安全；
keep 解析在循环后且 done=9<21，RedwoodGPUStart 天然不可达。loader 的 cmdline 零拷贝
指针无长度上限（134 B 无忧，NUL 由 pad 保证）。工程验证清单：PE_parse_boot_argn 的
`adreno=`/`adreno_skip=`/`adreno_only=`/`adreno_pause=`/`adreno_watch=` 通路全在
（5256-5337 行，文档背书组合语义）；gpu=1 消费点唯一（redwood 版被 soc-base 挡）。

**交付物**：tools/r28l-flash-build.sh（外科：双镜像换 kernel 条目；GPU 镜像 cmdline
加相位 token 组；daily cmdline verbatim；逐条断言）、tools/r28l-kernel-id.py（KNOWN
收编 R28k sha 916d7719…= run 40318421748 clean build；MARKERS 加 R28l 两行；verdict
主判据 R28l 化 + cmdline 相位 token 检查；对 R28k 真镜像实测回判 R28k ✓）、
.github/workflows/r28l-springboard.yml（605 行：门禁大表 R28l 化、gone 表收编 R28k
代数戳、sha 断言收编 R28k、cmdline 复证 grep 双查、README/release 全文 = L1-L3 判读树）。

**判读树（预注册，先于刷机立字；先自检探针再判分晓）**：
- 探针自检（任一不满足 ⇒ 本票作废，修复后重跑）：
  - P0：宣告行 `adreno: R28l: the phase bisection` 在场；
  - P1：`steps up to 9, without those in mask 0x1c00`——十进制 mask 落地探针；
    若打 `mask 0x0` ⇒ skip 解析失败 ⇒ 作废（预期不会：strtoul 十进制无歧义）；
  - P2：logdump 按头行 "L bytes follow" 截断、只判自有区（写边界 + 指针计数；
    三次残迹教训的常设纪律）。
- **L1（主分支）**：GPU 镜像（只读相位）全程干净（lsd 零 SIGBUS、无风暴、系统照常
  起，日志跑到用户态 00:03:00+ 过风暴首发窗）⇒ 触发面 = 写相位（steps 13+：SMMU
  context bank、GMU FW/启动、zap/T2、3D core）⇒ R28m 再二分（13-17 vs 19-21，
  或 13 单步：SMMU context bank + GMU FW 装载是第一个大内存/映射操作面）。
- **L2**：GPU 镜像（只读相位）仍风暴 ⇒ 触发面 = 只读相位（steps 1-9：时钟树写/
  RPMh/qfprom 读/SMMU ID 读）⇒ 嫌疑面大改写（时钟写/Logdma/UFS 写放大入场；
  注意 adreno 相位日志本身大量刷 storage——写放大线此时转正）。
- **L3（时间形状，与 L1/L2 独立记账）**：若 R28l 的风暴首发（若有）仍与 backboardd
  mapdb 拉取失败毫秒级同步且时刻同款（用户态 00:01:46 前后）⇒ 触发点与相位深度
  无关（时间相关面——启动序贯的固定时刻）；若时刻不同 ⇒ 深度相关，L1/L2 内继续收。
- 附带（时钟线）：+4 min 跳变在 R28l 自有记录仍不现 ⇒ K4 链续期（写相位没跑跳变
  也不现 ⇒ 跳变绑更深相位 13+）；若复现 ⇒ L2 方向佐证。
- 控制组：主镜像 cmdline verbatim（R28k 已证干净），本轮可不刷；刷则第三验证入账。

**设备侧操作（GPU 镜像主体）**：fastboot boot r28l GPU 镜像 → oem ramdisk → stage
r28l flashboot → oem run；**等 ~2-3 分钟过风暴首发窗再抓 logdump**（R28k 首发在用户态
00:01:46 ≈ 内核 ~67 s；抓早了 = 复测快照 60 s 的覆辙）。判读第一步 = 字节级切分自有区
（scripts/forensics_r28_boot_split.py 随轮复用）。T3（secapp cmd 0x49 离线逆向）并行
不占设备，继续。

## T3 · 静态取证第一批：cmd 0x49 的直接输入全解剖——链排除、头同构、两次翻案、缺格定位（2026-10-26）

**对象与工具**：fw-gpu-raphael 三件——a640_zap.mbn（原厂 14256B / 627ecc6a）、a640_zap.mbn.testkey-r13（14256B / a17975ca）、adsp.mbn（7820B / f5881fc7）。方法：DER 逐张走查（openssl 实解）+ SHA256 反查 + 双文件 diff 落图 + 全零间隙扫描（沙箱侧 scripts/t3_secapp_anatomy.py、t3_anatomy2.py）。**Q5 两条 0x40 设备侧 dump 与本地文件逐字节核对 ✓✓**（zap@0x1000、adsp@0x1000 前缀）——卷内文件 = 本地文件 = R28k 真机读出的字节，三方同一。

**F1 证书链全解析（DER 实解，非字符串级）**：
- zap 原厂链 = [叶：C=US/ST=California/**CN=SecTools Test User**/O=SecTools/L=San Diego，serial=01，2021-10-25→2041-10-20，签发者=**Xiaomi Attestation CA**] + [Xiaomi Attestation CA，serial=05，2017→2037 ← Xiaomi Root CA] + [Xiaomi Root CA，serial=01，自签，2017→2037]——**图片签名本身就是高通 SecTools 测试密钥对做的；小米只是在其上重发了证书链**（R24"原厂件"的准确含义）。
- zap 测试链 = [SecTools Test User ← Generated Test Attestation CA ← Generated Test Root CA（SecTools/CDMA Technologies/"General Use Test Key (for testing only)"）]——同款叶 subject。
- **adsp 链 = [SecTools Test User，serial=01，2021-10-17→2041 ← Xiaomi Attestation CA] + 同款 Attestation CA（serial=05）+ 同款 Root CA（serial=01）**——与 zap 原厂链**完全同构**（叶子比 zap 早一周签发，同一批重签活动）。T2 的 ACCEPTED(0) 证明这条"测试叶+小米链"结构 TZ 收。
- 证书槽 = v6 头 word[9]=0x1800（6144B）声明的固定槽 [0x1238..0x2a38)：原厂链 3100B + 0xff 填充、测试链 3327B 恰满至 0x1f37；两文件 [0x1f37..0x2a38)（2817B）逐字节全等（纯 0xff 填充）。签名块 ≈[0x1120..0x1238) 256B（RSA-2048，两链各异）。

**F2 v6 头同构表（adsp@0x214 ↔ zap@0x1000，逐字对照）**：
  word:  adsp / zap —— [0]=0xc/0；[1]=6/6；[2,3]=0/0；[4]=0x1bd0/0x1990；[5]=0x2d0/0x90；[6]=0x8e020800/0xffffffff；[7]=0x100/0x100（RSA-2048 签名尺寸）；[8]=0x8e020800/0xffffffff；[9]=0x1800/0x1800（证书槽尺寸）；[10]=0/0；[11]=0x78/0x78；[14]=4/0x14；**[15]=0x0a50e1/0x0a50e1（两镜像全同 ⇒ 非 OEM 身份字段，平台/工具版本类）**。扩展区（0x1040+/0x254+）：0x72、0x102、0x6003 双方一致。zap 原厂 vs zap 测试的头差异仅 3 处：word[15]（0x0a50e1/0）、word[16]（0x72/0）、word[19]（0x102/0x100）。

**F3 翻案一：.mdt 的 p_offset=0x1000 是假的。** adsp phdr[1] 声明哈希段 0x1c78@0x1000，但文件实长 0x1E8C ⇒ 0x1000 之后只有 0xE8C；而 **[0x214..0x1E8C) 恰 = 0x1C78 = 声明值**——.mdt 的哈希段实体 = 紧跟 phdr 表（0x214）到文件尾，0x1000 处是证书中部（Q5 的 adsp dump 实为 Root CA 证书内嵌的 RSA SubjectPublicKeyInfo，非"签名表头"）。T2 整文件形态 ACCEPTED ⇒ TZ 验证的元数据在 0x214——**TZ 不可能按 p_offset=0x1000 读 adsp**（那里按任何头解析必炸）。

**F4 翻案二：zap 整文件形态递给 TZ 的 [0x94..0x1000) = 3948B 全零。** zap phdr 表尾 = 0x94；v6 元数据（头+签名+证书槽）实体在 [0x1000..0x2a38)（这次 p_offset 是真的）。⇒ 若 TZ 对 .mbn 沿用"header pair 之后取元数据"的约定，整文件形态读到的"元数据" = 全零——**原厂链从未被 TZ 真正看过一眼**。

**F5 排除表（至此）**：调用形态/地址/内存归属/arginfo——R21-R28 矩阵排除（三参族 SMC 即复位，两参=唯一应答形态）；通道——T2 排除；**链根（Xiaomi Root CA）**——adsp 同根 ACCEPTED ⇒ 排除；**叶子=SecTools 测试密钥模式**——adsp 同构叶 ACCEPTED ⇒ 排除；**v6 元数据头格式**——adsp 同构头 ACCEPTED ⇒ 排除；is_supported 平台禁令——双 0（adsp 也是 0 且能过）；缓冲可读性——零页 0x0A + adsp 全证 ⇒ 排除。**2×2 矩阵 [签名链 × 元数据位置] 的"原厂链 × 打包形态"格从未测过**：R21 打包 = 测试链（0x45 死于链，判得对）；R24+ 整文件 = 原厂链（元数据躺在 0x1000，前面 0x94..0x1000 全零）。

**F6 统一理论（当前唯一无矛盾解）：TZ 从 buffer 的 header pair 之后取元数据，不按 p_offset。** 则：R21 打包（测试链）→ TZ@0x94 见真 v6 头 → 解析 ✓ → 测试链拒 → 0x45 ✓（当年"读到打包块中段"的解读错，但观测恰好不可区分）；R22-R28k 整文件（原厂链）→ TZ@0x94 见全零 → 元数据无效 → 0x45 ✓（格式层，永远轮不到验链）；T2 adsp 整文件 → TZ@0x214 见真头 → 小米链 → ACCEPTED ✓；零页（无 ELF）→ 更早退出 → 0x0A ✓。**四组观测全部无矛盾。** 0x45 的语义嫌疑从"身份层"改判"格式/元数据层为主嫌疑"（与 0x0A 的分界 = ELF/哈希段能否定位）。

**R28m 预注册（先于刷机立字）——最小实验 = 补测缺格**：
- M1：buffer = [ehdr+phdrs 0x94][哈希段 0x1a38 自 0x1000]（stock msm-4.14 打包形态，原厂链），phdr p_offset 原样 → 判据：ACCEPTED(0) ⇒ 位置即真凶，墙倒，接 MEM_SETUP/AUTH_AND_RESET 走 step 19 全路径；
- M2：同 M1 但 phdr[1].p_offset 补写 0x94（自洽打包）→ M1 拒而 M2 收 ⇒ TZ 按 p_offset 读（F6 修正版）；
- M1/M2 均 0x45 ⇒ 位置假设死，内容/身份层坐实 ⇒ secapp 反汇编成为唯一路径，tz.mbn 到手升硬前置；
- 0x0A/其它码 ⇒ 新证据回桌。
- 附带探针（同 boot 各一发 INIT_IMAGE，无副作用）：E4-cross：zap 文件 × pas id 1、adsp 文件 × pas id 13——id 绑定 vs 文件绑定的最后分诊。
- 红线：两参形态唯一（三参族 = SMC 即复位，R28 判决）；每个可复位调用前 put_the_log_away；判词行逐字预注册；R28l 相位二分轮不受挤占——M1/M2 属 GPU 镜像 step 19 路径，若 R28l 先执行则 R28m 顺延为独立轮。

**遗留 / 硬前置**：secapp 二进制（tz 分区 / R28g 官方包同款 hyp-tz-devcfg）不在两仓库。若 M1/M2 皆 0x45，需要用户提供 tz.mbn（官方包固件集或 dd /dev/block/by-name/tz）——静态侧能榨的已全部榨干，本条目即 T3 静态批的闭卷。

## T3 · 静态取证第二批：tz.img 落体解剖——0x300045 不在此镜像中，墙判上游（2026-10-26）

**输入**：tz.img 4,194,304 B（sha256 8022b6a66f41…，ELF64 AArch64，e_entry 0x14680000，23 phdr，无 section header = QSEE 签名镜像）。内核侧 scm() 核实为裸透传（(int32_t)x0 直印）⇒ -3145797 为安全侧原样返回。

**G1 镜像自证**：seg1（0x1000..0x6140）= v7 元数据段（头 19/6/100/1800，"QSEE"/"Beijing1"/"Xiaomi" 证书链，哈希表+0xff 填充）——与 ACCEPTED 的 adsp 同一小米生产链，同源设备固件判定通过。[0x548..0x1000) = 2744B 全零。

**G2 架构全解**：SMC 分发表 @0x1c1323f8（{0x02000201, arginfo 0x82 ←与内核判词互证, 0xa, handler 0x1c04f4a4}；205→0x1c04f488；202→0x1c04f4f8；206→shutdown；第二注册点 @0x1c290118）。INIT 薄壳 0x1c04f4a4 = `bl 0x1c04df1c; cneg w0,w0,gt`（负号化点）。真身 0x1c04df1c：pas_id≤0x1D、per-PAS 对象表 @0x1c165618（**镜像态 30 槽全空 ⇒ 运行时动态建**）、per-PAS 锁 @0x1c132100、默认错 0x30001B；主步进 0x1c04d75c（回调对 {0x1c04e344 范围检查, 0x1c04e3e0 状态映射}）→ 解析 0x1c0d6xxx → 注册 0x1c04d3b8 → 哈希段选择（bits[24:26]==0b010）→ blr 回调 → 二段 0x1c04f0c4。元数据引擎 0x1c0d6xxx：0x1c0d67b8 = NS 缓冲读取器构造（16B 头 + ELF 魔数，读败/-0x61 → 4）。**错误码拼装 = 减法算术族**：0x30005B − delta（0x51→0x30000A 零页 ✓、0x50→0xB、0x4F→0xC、0x4E→0xD、0x4D→0xE）。PBSB 对象系统（魔数 0x50425342，可插拔虚表 +0x410/+0x418，哨兵 0x2ECB770=默认）；cmd 0x49 调用点 0x1c0b30b0/28/50 全在 secboot 配置/KV 层（/secboot/oem_secapp、/secboot/deferred_oem 的 oem_id/model_id/sw_id_list）。seg3（0x14680000 独立 VA 域，含入口）= NS 访问桥（0x8110xxxx 族）。大解析器（0x1c0a9xxx-0x1c0aba40）内层码族 0x20-0x2b/0x44/0x45/0x4a；两 0x45 产地（表门控 0x1c0ab688——表空；区域验证器 0x1c0ab868 内部入口——精确包含→0x45）均无直接 BL 调用者。

**G3 决定性普查：0x300042-0x300049 整段缺席，0x300045 零合成点。** 方法：函数边界对齐（ret 后非零起点 ×6175）+ 失配重同步线性扫（382,506+ 指令）+ 常量追踪器（mov/movz/movn 别名、movk、reg-imm/reg-reg 算术、BL 清易失）。0x300000-0x300FFF 全谱落地（0x300000×1 … 0x300100×3）；字面量池/orr #0x300000/add-sub #0x300(0),lsl#12/64 位 MOVK 另行排除。交叉验证：0x30000A 唯一合成点 0x1c04d824 = 0x30005B−0x51，与零页判词 -3145738 逐位互证。工具教训：普查窗口两连错（0x30FFFFF/0x03000000，正确族 0x300000-0x300FFF）曾全滤正确结果——**纪律：自动工具结论须过一个已知锚点正向复算方可采信。**

**判决**：
- **J-T3-2a：墙不在 tz.img。** 零页对照证通道可达 TZ INIT（0x30000A 仅此镜像可产）；真 zap 返回 -0x300045 而本镜像产不出；同 fnid 同形态唯一变量 = 缓冲内容 ⇒ 拦截在上游（QHEE/hyp 或 EL3 = R28g 的"hyp 包装壳"），且该层读内容判形（零页放行、真 zap 被截）。保留项：0x0A 亦可能上游同族判出（不影响结论）。
- **J-T3-2b：0x45 = "此图不可收"类判词。** R23 考古：stock 上 spss 同码被拒、a640_zap 同字节文件 stock 过——同字节 stock 过/XNU 拒 ⇒ 变量在通道或调用者身份，非文件。
- **J-T3-2c："secapp cmd 0x49" 命名就 zap 墙退役**：0x49 在 tz.img 真实存在但服务于 secboot 配置/KV 层，INIT 直呼链无 0x49；责任层改判"上游拦截层（hyp 侧）"。
- **F6 升级**：header-pair-end 读者获得候选执行者（上游拦截层）。R28m 有效且升级：**M2（自洽打包）= 最强单发**（同时满足两种读者）；M1 拆分层归属。

**N 预注册**：N1 硬前置 = 确证 tz.img 出处（dd /dev/block/by-name/tz 的 sha256 比对 8022b6a66f41…）；N2 = R28m M2 优先：ACCEPTED ⇒ F6 双层成立墙倒；仍 0x45 ⇒ 转向 **hyp.mbn 取证**（普查器直接复用），hyp.mbn 升新硬前置。N3：artifacts 于 download/t3-batch2-artifacts/（census4b 全图等）；脚本侧 tz_parse/tz_disasm1-11/tz_census1-4 留档。

## T3 · 静态取证第三批：hyp.img 落体解剖——0x300045 真凶落网（2026-10-27）

**输入**：hyp.img 8,388,608 B（sha256 6b7ed7a40c16ffaa…，ELF64 AArch64，e_entry 0x85710000，4 phdr；seg3 = 唯一代码段 file[0x3000..0x77388)→VA 0x85710000 filesz 0x74388；seg1 @VA 0x85962000 = Xiaomi 证书链段——SecTools Test User + Attestation CA + Root CA，与 batch1 zap 元数据同源同构）。工具链全复用 batch2（hyp_parse/hyp_census/hyp_disasm1-5，capstone 5.0.7），普查窗口 0x300000-0x300FFF + 边界哨兵 0x2FF000-0x302FFF（无族外泄漏）。

**H1 普查判决 + 锚点正向复算**：0x3000xx 全族在 hyp 复活（0x300001-0x30001B、0x300033、0x300040、0x300042、0x300044、0x300045×6、0x300046-49×3/3/3/2、0x30005B；0x30000A @0x85725a24）。0x300045 六处普查命中经逐条反汇编核对 = **一真五伪**：真合成点 **0x85727050**（`mov w19,#0x45; movk w19,#0x30,lsl#16`，在 `pil_init_image_to_tz` 内）；0x85726c8c/c94、0x85726f40/f64、0x85727084 为线性扫描合并伪迹（真值 0x300043/0/w4 透传/0）。batch2"锚点正向复算"纪律第四次生效。

**H2 SMC 分发表全解（R28d 地址 0x85760B00 证实）**：表 @VA 0x85760B00，步长 24B，项 = {pad, fnid, arginfo, flags, handler VA(64b), pad}，37 项，表基指针存于 0x85761C10。PAS 家族：0x02000201 INIT(arginfo **0x82** ←与 batch2 tz 侧 INIT arginfo 逐位互证；handler 0x8572755c)、0x02000202 MEM_MAP、0x02000205 AUTH_RESET、0x02000206 SHUTDOWN、0x02000207 IS_SUPPORTED、0x02000208、0x0200010a；thunk 统一形态 `bl 服务体; cneg w0,w0,gt`（正码负号化返回——0x300045→-3145797 的符号翻转点）。内核 fnid 0x42000201 = 表内 0x02000201 + SMC64 位 0x40000000，conduit `orr w8,w21,w25,lsl#30` 现场重构同码。

**H3 INIT 全路径（pil_init_image_api_ns 0x85726498）**：门1 pas_id≤0x1d（0xd ✓）、门2 mdata 非空（0xff014000 ✓）→ 0x30001B；subsys 对象表 [0x858ACC00+pas*8]（RAM）；x21≠0 → reader 构造 0x85726cd8（impl={0x857269DC,0x85726A8C}=PILSourceNS）→ **PIL_parseMetadata 0x857258c4**（七步：16B 头读 / **memcmp(buf,"\x7fELF",4)** 常量@0x8575EF40 / ldrb[头+4]=EI_CLASS∈{1,2}→ELF32(0x34+0x20)或ELF64(0x40+0x38)解析器+e_version==1 / phdr 整读+PILSource_ctor(0x857254bc)快照并改写 subsys 流虚表 / 二次解析+0x85741324 TOCTOU 双读一致 / phdr 扫描 [&0x07000000]==0x0200000 哈希段标记 / getData 产出(ptr,len) 溢出守卫）错误码 = 0x30005B−δ 族（−0x51=0x30000A 零页码、−0x50=0xB、−0x4F=0xC、−0x4E=0xD、−0x4D=0xE）→ 0x85726fb4 `ldrb [x21,#0xBE]` 非零假成功 → pil_init_image_to_tz 0x85726ff0：arginfo 构造 0x85716b60(2,0,2)→0x82、拼 0x02000201、conduit 0x857182a4、**返回≠0 一律改写 w19=0x300045**（真错码只进 "pil_init_image_to_tz [%lx]" 日志环）。

**H4 传输层**：目的地查找 0x85717fcc——直通 EL3 清单 @0x8574A5B8（NULL 结尾 31 项实读：0x02000601/0x02000102/0x0400xxxx 架构族/0x02000305/0x02000501-2/0x02000902/0x02001502-3/0x02000c1f…）**无 0x020002xx** ⇒ PAS_INIT_IMAGE 走 Stargate 异步消息路径（"hyp originated msg tracker"，队列/中断会话系统 0x8571801c，"Interrupt session exceeds maximum support"）；直通路径才有裸 `smc #0`（0x857119c8 内 @0x85711a78/@0x85711acc 两发）。

**H5 零页互证 + 码源改判**：零页探针（GPU id + audio id 双查）得**裸 0x30000A** 而非包装码 0x300045 ⇒ 零页死于 hyp 验证器步骤 2/3（ELF 魔数/EI_CLASS）、原样透传 ⇒ **反证 GPU(0xd)/audio 的 subsys 对象运行时已注册**；真 zap 通过全部七步（否则见 0x30000A-E 族）⇒ **R24+ 整文件形态在 EL2 层完全合法，拒因在 Stargate 下游（EL3/TZ 侧），hyp 只改写码**。J1 时代"TZ 拒绝 -3145797"的码源层作废（真 TZ 码被吞，内核不可见）；"TZ 侧拒绝"因果保留。F6 整合：hyp 要求整文件 ELF 并自定位哈希段，TZ 收 hyp 产出的哈希段引用——batch2 "header-pair-end 读者" 获得机制解释。

**J-T3-3a（成立）**：0x300045 码源 = hyp pil_init_image_to_tz（0x85727050），tz.img 排除结论无损；**J-T3-3b（成立）**：真 zap 过 hyp 全部结构检查 ⇒ 拒因在 EL3/TZ；**J-T3-3c（新）**：EL3 真错码唯一白盒通道 = QHEE 日志环提取（N3，logger 0x857150d4，非刷机前置）。

**M2 布局契约（从 hyp 检查直接导出，R28m M2 按此打包）**：①缓冲区必须 \x7fELF 开头（整文件 ELF 是 EL2 硬门槛，裸哈希段/裸元数据方案步骤 2 即死 0x30000A）；②EI_CLASS/e_version/e_ehsize/e_phentsize 规范值；③必须存在 p_flags 高三位=0x2 的哈希段 phdr；④检查窗口内缓冲区逐字节稳定（TOCTOU 双读一致，0x30000C 红线）；⑤哈希段内容（v6 0x34+0x20，原厂链）hyp 不验、交 TZ——**认证变量全部集中在哈希段内容，ELF 外壳只需结构正确**；⑥hyp 把 M2 自由度压缩到"哈希段内容"单维度，与 batch1 缺格矩阵正交对齐：打包形态由 hyp 锁死为整文件 ELF，原厂链的正确载体 = ELF 内哈希段。

**N 预注册**：R28m M2 前提收窄按上述契约执行；M2 仍 0x300045 ⇒ TZ 对哈希段内容的认证拒绝实锤（证书链/签名结构层），secapp 反汇编（tz.mbn dd 或官方包）仍是终极路径；N3（并行支线）：QHEE 日志环提取可读出被吞的 TZ 真错码。artifacts：download/t3-batch3-artifacts/（census、分发表 37 项、直通清单 31 项、PIL_parseMetadata/api_ns/conduit 全文反汇编）；脚本：hyp_parse/hyp_census/hyp_disasm1-5/hyp_artifacts（普查器换 SRC 即可打下一镜像）。

## R28l 事故 + N3 静态侦察 + R28m 落地（2026-10-27）

**R28l 事故（run 40331159223 = build failure，从未产出镜像）**：R28l 的相位二分**从未执行**（无镜像可刷）。死因 = r28l-flash-build.sh **自己的收尾断言**：`assert gpu == base + ' adreno=9 adreno_skip=7168 ...'` 拿 daily 底板 cmdline（无 gpu=1）做基准，而 GPU 底板自带 gpu=1 ⇒ 期望串漏 `gpu=1`。**内核本身编译干净**：日志中 222 处 "exported name not in import list"（IORPC/UAT 符号族）在 R28k **成功**日志（run 40318421748）里逐字同样在场 = 良性 symbolset 警告，与失败无关；失败点在打包 step 的收尾对照段。R28m 修断言（`gpu == base + ' gpu=1 adreno=22 adreno_pause=0 adreno_watch=0'`）并取代该轮（全相位，相位二分的历史使命由 M1 判定附带结案）。R28l 内核 sha 无席位（无产物）。

**N3-1 静态侦察（hyp.img 日志环全解，判据全部落盘）**：
- 日志函数 = **0x857150d4**(level, fmt, args...)：栈哨兵 → 0x85715164（ring 初始化检查 0x858A5C90 旗标 + 0x858A5C88 指针）→ printf 核心 0x857146f4（% 解析器，回调 = 0x85715054）→ putchar 0x85714FE4（**环写入点**）；UART 侧 = 回调内 '\n'→'\r\n' 翻译 + bl 0x85714FE4 两发 CRLF。
- **环初始化代码 @0x857152a8**（"HYP Timestamp Log Init"）：`mov w9,#0xfb30; movk w9,#0x146b,lsl#16` = 登记表**物理地址 0x146BFB30**；`stp w8,w10,[x9]` 写入 {ring 基址 0x858E0000, 尺寸 0x2000}；magic `0x000000086879706D`（字节 "6D 70 79 68 08 00" = "mpyh"+8）写 [+0]、0x200 写 [+8]（头域）、0x1E00 写 [+0x10]（数据区宽 = 0x2000−0x200 自洽）、ring+0x200 写 [+0x78]（数据基址）。
- **putchar 环写机制**：`x9=[ring+0x78]`（数据基址），`x10=[ring+0xE]`（u16 行内偏移），`strb w0,[x9,x10]`；`[+0xE]+1` 对 `[+0x10]=0x1E00` 取模回绕；回绕到 0 时 `[+0xC]`（u16 轮次计数）+1。⇒ 数据区 = ring+0x200 起 0x1E00 字节纯环形，最新内容以 [+0xE] 为界。
- 全镜像 **893** 个 bl 0x857150d4 调用点；Stargate 管理区（0x85718000-0x8571B000）日志串全景：`hyp_manager_tzapp_send: SMC Call to qcom.tz.mssecapp Failed 0x%x` / `SK Command to qcom.tz.mssecapp Failed 0x%x`（**真错码 0x%x 直打日志环的路径存在**）、`skAction: HYP_IMG_AUTHENTICATE/HYP_IMG_LAUNCH`、`hyp_manager_handle_auth/launch` 族、`HYP_DIAG`（hyp 自带 minidump 登记通道）、`ADSP Table` 族。PIL 侧包装器日志只打包装码（w19=0x300045 覆盖 w0 原始值后才打 "[%lx]"）——真码是否到环由 Stargate 应答层决定，**环 dump 是唯一裁决**（预注册 N-a/N-b 两岔）。
- 恒等映射佐证：登记表把 VA 值 0x858E0000 写给外部物理直读者（XBL/crash handler 官方通道）⇒ QHEE VA=PA；真机由探针两级验证（登记表→magic）兜底。

**R28m 内核补丁（raphael_adreno.c，+236 行）**：
- **zap_header_m2(phys, size_out)**（M2 契约打包）：七步契约逐条前置校验（ELF 魔数/EI_CLASS=1/EI_DATA=1/e_version=1/ehsize=52/phentsize=32/phoff=52/phnum∈[2,16]）→ 逐段 memcpy 回声明 p_offset（段 span 越界拒打）→ **哈希段 0x02000000 旗标强制在位**（缺席拒打）→ 规范头重写（e_version/e_phoff/e_ehsize/e_phentsize/e_phnum/e_sh*=0；身份字段 e_type/e_machine/e_entry/e_flags/osabi 保留源值）+ **重写差异数打日志**（预期零差异 = 文件本已规范 ⇒ 墙铁定在哈希段内容）→ R23 链判词延续 → to_memory + kvtophys。
- **hyp_log_dump()**（N3 探针）：adreno_hyplog boot-arg（非零 = 直接指环）→ 默认两级验证（ml_io_map(0x146bfb30) 读 {ring,size} → magic "mpyh" 校验）→ 全环 0x2000 raw hex（32B/行）入内核日志。插入点：**refused: 路径 put_the_log_away 之后、give-up 之前**（give-up 与零页对照都会推环）+ step_zap_signature 失败路径；dump 后立即 put_the_log_away。
- R28m 代数戳 ×2（generation line / step-1 flush 自测）+ M2+N3 主目标宣告 ×1（R28l 相位判词以历史引用在场）；R28k run-goes-on 措辞原样（本轮 step 19 路径**可达**）；旧 zap_header 整函数保留（__unused 注解，矩阵叙事存档）；调用点两处换 zap_header_m2（step_zap_signature / step_zap_load，后者 whole 由 m2 回填，E1 mode=3 的 size 参数语义不变）。
- 静态自检：括号平衡 0；二进制 marker 预测（剔注释后）全对账：R28m 戳 ×2、M2+N3 宣告 ×1、M2 标记 ×3、HYPLOG 标记 ×2、adreno_hyplog ×1（PE_parse 串）、"the QHEE log ring (N3)" ×2、R28l/R28k 代数戳 ×0、R28j 措辞全保留。

**R28m 三件套（R28l 派生 + 外科修正）**：
- tools/r28m-flash-build.sh：GPU tokens = `adreno=22 adreno_pause=0 adreno_watch=0`（无 skip——step 19 必须跑）；**R28l 死因断言修复**（gpu=1 进期望串）；内核 gate 加 M2×3 + HYPLOG×2 + adreno_hyplog 标记、R28l 两标记进退役反查；三块内嵌 python 语法全过。
- tools/r28m-kernel-id.py：R28m 判据 = 代数戳 ×2 + M2+N3 宣告 + M2 三标记 + HYPLOG 三标记 + adreno_hyplog ×1 + R28k run-goes-on 在场 + R28k/R28l 代数戳缺席；KNOWN 表 R28l 无席位（注释死因）；**对真 R28k 镜像回归通过**（sha 916d7719 正确回判 R28k，cmdline/ramdisk 判词无恙）。
- .github/workflows/r28m-springboard.yml：缓存键全新（r28m-theos-v19L2-v1 / r28m-xnu-deps-v1——R28l 环境嫌疑一并清除）；③诊断/门禁 R28m 化（M2/N3 标记应计数 + R28l 退役反查 + sha 断言注明 R28l 无席位）；⑤ 打包 step 断言对齐（adreno=22、无 skip、gpu=1 计数）；⑥ README/release body = **M/N 判读树**全文（P0-P3 探针自检 → M1 墙倒 / M2 → N-a 真码分诊 / N-b 真码在 EL3/TZ ⇒ secapp 终极 / N-c 非恒等 ⇒ adreno_hyplog 手动 / N-d wedge 即证据）；YAML 解析通过（11 steps）。

**预注册（先于刷机立字）**：R28m GPU 镜像全相位单变量 = M2 打包 + N3 探针；T2 对照照旧先跑（ACCEPTED 0 = 链路无罪基础判词）；lsd 风暴若现 = R28l 相位二分的写相位判定附带入账（全相位 boot 的自然实验）。设备侧流程：`sh run_ios.sh flash r28m gpu -w 180`（观察 180s 过风暴首发窗）→ logdump 按 P3 截断 → 判读以自有区为准。

**R28m CI 三连与闭环（run 40475320018 failure → 40477016307 failure → 40478487680 SUCCESS）**：
- 第 2 步改判：run 40475320018 = gen_r28m2 派生残留——门禁 require 表仍带 `R28m: the phase bisection` 条目（内核二进制从未有该串），而**内核编译成功**且全部新 marker 在日志逐字对账（R28m generation=2、M2+N3 announce=1、M2 seg table/canonical/chain=1×3、N3 registry/ring/boot arg=1×3、R28k run-goes-on=1）——门禁表修复（M2/N3 条目 + gone 表 R28l 双戳 + gen_r28m2 残留防御条目）。
- 第 3 步教训（工具史第 N 课）：run 40477016307 = **编译器 dead-strip 吃掉退役函数**——`__unused` 只免警告，未引用 static 的 zap_header 整体被优化器剥离，其内的 R22/R23 门禁 marker（'the metadata is built as'、whole-file 形态行、one-page 判词、'the hashes carry'、'A TEST KEY (Qualcomm SecTools'、'no test-key marks and no Xiaomi marks'）随之蒸发。修复 = `__attribute__((used, unused))`：矩阵叙事强制留在二进制（门禁计数对象）且无调用者不警告。
- **run 40478487680 = SUCCESS**（11/11 步）：R28m 内核 sha256 = **911a69df7434ee76e004d2e5243b290cf73dc582e2f86f019bf5e986faf89c4e**（clean build，ccache 0 回填，内嵌 adsp .mdt 7,820 B @ 0x75ac 仅一份）；GPU 镜像 cmdline 实证 `...gpu=1 adreno=22 adreno_pause=0 adreno_watch=0`（全相位，R28l 死因断言修复后的期望形态）；双镜像同 kernel 同 R13 卷；Release r28m-springboard 七资产就位（双镜像 + flashboot + md1 + README + SHA256SUMS + toolchain-versions）。设备侧动作（预注册 M/N 树）：`sh run_ios.sh flash r28m gpu -w 180` → logdump 按 P3 截断 → 自有区判读。

## R28m 真机判决（M2 + N3 首发命中）+ run_ios.sh v2 四缺陷修复（2026-10-27）

**R28m GPU 轮判词（logdump 4,596 行，自有区干净）——M2 = 仍 0x45，N3 白盒探针首发命中，N-b 实锤**：
- **M2 重建实测（step 12）**：三段全部落声明 p_offset（seg0 type0 flags 0x7000000 @0 / seg1 type0 flags 0x2200000 @0x1000 哈希段 the mark is in place / seg2 type1 flags 0x8000007 @0x3000）；规范头重写 **0 of 52 bytes differ**（文件本已规范形态，M2 未移动真实字节）；链判词 = 生产链（"Xiaomi Attestation CA"/"Xiaomi Root CA"）在哈希段内。⇒ **hyp 格式层（七步布局检查）按构造满足，打包假设矩阵（R21 紧凑/R24+ 整文件/M2 自洽）三形态全部出局**。
- **仍 -3145797**（= −0x300045 的 SMC 负号化），answer 0。
- **N3 探针（核心战果）**：refused 路径全环 dump 落盘——登记表 PA **0x146BFB30** 实读 {ring 0x858E0000, size 0x2000} ✓；环头 **magic mpyh** ✓、rounds 0、写偏移 1355、宽 0x1E00 ✓——与 T3-batch3 静态解剖逐位互证（QHEE 恒等映射假设成立）。环已写 1355 B（rounds=0 无回绕），解码 89 行 ASCII：HYP boot 日志（TZ.XF.5.2-00209 / IMAGE_VARIANT YAQAANAAA / OEM CRM / psci 00010001 / SMMU VMID 4 not support ×2 / HYPX REGISTRATION FAILED 0xfa11 boot 期噪声 / SMMU Virt SMR/S2CR 无效值若干）。
- **环内真码判词（本轮最重科学产出）**：拒绝时刻 QHEE 写环两行——`pil_init_image_to_tz [300045]` → `pil_init_image_api_ns [300045]`（自底向上传播，各层打自己返回码）。**到得了 to_tz 的日志 ⇒ PIL_parseMetadata 七步全过**（静态链：parseMetadata 在 to_tz 之前，format 失败根本走不到 to_tz）⇒ 拒绝点钉死在 **to_tz 下游 = TZ 认证裁决**；Stargate 管理区 "SMC/SK Command to qcom.tz.mssecapp Failed 0x%x" **零出现** ⇒ secapp 交换干净完成（内容拒绝，非传输失败）；**真错码被 to_tz 的 mov w19,#0x45/movk #0x30 改写吞没**（环里两层都只有包装码）。⇒ 预注册树 **N-b 命中：真码在 EL3/TZ，secapp 内容/身份认证拒绝实锤**；hyp 层（布局/元数据/打包）全面排除。
- 拒绝后轨迹：step 12 FAILED → "the steps after it are not attempted" → steps over → 系统继续启动，SpringBoard 存活至 FLUSH 尾（软渲染：MiniIOMobileFramebuffer 24 frames/30s，0 GPU 等待）；无 lsd 风暴、无时钟跳变复现（M1 附带结案的自然实验：全相位 boot 桌面干净）。
- **下一步硬前置（secapp 终极路径）**：uefisecapp 分区 dd 取证（logdump 分区表实录 lba 6656..7167, 2048 KB；qcom.tz.mssecapp 即其内 TA）——tz_census4 普查器换 SRC 直接复用；目标 = cmd 0x49/HYP_IMG_AUTHENTICATE 拒绝分支的**真实判据**（哈希段哪一项不满足：链策略？fuses 绑定？anti-rollback 版本？）。备选白盒：Stargate 应答结构体在 hyp 内存无 NS 窗口，环已证只出包装码。

**run_ios.sh v2（四缺陷修复 + 16 断言回归全绿，测试挖出 Task 9 潜伏雷）**：
- ①**boot 容错**：新 fb_boot_safe()——boot 前**强制 oem stop**（>/dev/null，清 loader 残留 kernel-running 状态，修二次 boot "a kernel is running" 拒绝）；"Cannot send after transport endpoint shutdown" 分类为**对端接管 USB 的正常征兆**（警告不退出，wait_xnuloader 60s 裁决真伪）；"a kernel is running" 仍拒绝但 oem stop 已前置；未知错误带原文 die。
- ②**参数清洗 + 提示流分离真雷**：clean_str（[:space:] 全剥）/clean_cr（仅 \r，保路径内部空格）入所有读入点（flash/grab 的 ver+mode、resolve_ver/pick_ver_menu/pick_mode_menu 交互、menu 主选单、diag/split 路径）。**测试 sh -x 挖出 Task 9 潜伏雷**：pick_ver_menu/pick_mode_menu/resolve_ver 把交互提示打到 stdout，被 `v=$(...)` 连提示捕获（trace 实锤 `v=  1) r28m选版本(序号/版本号/回车=1): r28m`）——回车符只是症状之一；且 resolve_ver 的 die 在 $() 子壳里只杀子壳，**错误文本反被当成版本号**继续流转。修复 = 经典流分离（提示一律 >&2，stdout 只出值）+ 失败改 return 1 + 调用方 `|| die`。菜单路径从此真正可用。
- ③**等待逻辑**：fb_present 改 **fastboot devices 轮询为主**（fastboot|xnuloader 都算在场——grab 后可直接续 flash；getvar product 降为兼容兜底）；wait_fastboot/wait_fastboot_long 要求**连续两次检测到才算"真的出现"**（单次毛刺不复位），强关等待不再依赖单次 getvar 应答。
- ④**二次 boot 容错**：报错不再直接 die——oem stop 前置 + boot 分类放行后，仍尝试 wait_xnuloader + oem logdump + get_staged；**死点移到 get_staged**（loader 不响应拿不到日志才失败）。
- 回归：bash -n + dash -n 双过；假 fastboot 夹具 16 断言全绿（clean_str/clean_cr、fb_present 三态、fb_boot_safe 四分类 + oem stop 实发验证、grab/菜单端到端带 \r 污染全链走到取日志死点）；测试脚本 scripts/test_runios_v2.sh + 环解码器 hyplog_decode.py 落盘。

## R30 预注册 + 内核补丁 + 三件套（2026-10-27，先于刷机立字）

**R30 静态侦察新证据（本轮反汇编新落）**：
- **TZ DIAG 基址登记字锁定**：hyp.img 0x8571200c 起的映射函数实读——`mov w8,#0xf720; movk w8,#0x146b,lsl#16; ldr w8,[x8]` = **TZ DIAG 基址 = 物理 0x146bf720 处的 u32**，尺寸 **0x3000**，随 `bl 0x85737f6c` 映射进 HLOS 页表；失败分支打 "Failed to map TZ DIAG region to HLOS PT"（0x85748908）。孪生映射 = **HYP DIAG 基址 = u64 @ hyp 全局 0x857619e8**，尺寸 **0x2000**（失败分支 0x857488df）。
- **HypX 注册链**：0x85712144 起查询 "HypXVersion"/"HypXCodeAddr"/"HypXSize"/"HypXDataAddr"（FN 0x85728254），值落 0x858a59b8/0x858a59d8/0x858a59e8 与 0x85775dd8/0x85775de0——0xfa11（名字查找失败）与 0xfa0d/0xfa0e/0xfa0f/0xfa11 错误族在案。
- **R28m 真机环解码复核**（upload/r28m-gpu-logdump.txt 的 HYPLOG hex → hyplog_decode 同族脚本）：rounds=0 ⇒ 环内 29 行 = **hyp 全量日志史**（hyp_devcfg_init 噪声 → psci → HYPX REGISTRATION FAILED 0xfa11 → SMMU 噪声 → "Is TZ APP region mapped to HLOS VM = 1" → 拒绝时刻两行 [300045]）；**无任何 "Failed to map ... DIAG" 行 ⇒ 两个 DIAG 映射在真机 boot 时执行且成功**（登记字应非零）；环内无真码（N-b 复核成立）。⇒ R30 的 TZDIAG/HYPDIAG 捞取有真实预期。

**R30 内核补丁（raphael_adreno.c，+231 行）**：
- **scm_state_dump()**（被吞真码回收探针）：拒绝现场逐区读恒等映射物理内存，**每区 dump 后 put_the_log_away 再碰下一区**（后区 wedge 不吃前区记录）——
  - QUE：0x85775e00 + cpu*0x30，0x200 B（SCM 队列条目：+0x1d4 状态机 / +0x1e0 pending fnid / +0x200 计数）；
  - HND：0x858a6390 + cpu*0x18，0x40 B（per-cpu 句柄缓存 {fnid, valid}）；
  - GLB：0x858a5900，0xc00 B（HypX 全局 + 请求计数器 0x858a6100 + 八核句柄 0x858a6390..0x858a6450）；
  - SCMDMP：0x85775000，0x10000 B（SCM 区全量 64KB，2048 行；adreno_scmsweep=0 可关）；
  - TZREG+TZDIAG：读 0x146bf720 的 u32 登记字（打印之），合理则映射 0x3000 dump；
  - HDREG+HYPDIAG：读 0x857619e8 的 u64 登记字，合理（非零/4 对齐/<4GB）则映射 0x2000 dump。
  - r30_rows() 十六进制行辅助（32B/行，与 HYPLOG 同款）；cpu_number() 取当前核（<kern/cpu_number.h>，本树 psci_smp.c 同款用法）；ml_io_map 读法与 N3 环同款（R28m 已证该读法在真机拿得到新鲜状态）。
- **插入点**：refused: 路径 `hyp_log_dump()` 与其 flush 之后、give-up 之前；flush 结构改为三段（refusal 行 → N3 环 → R30 全区组）各自落盘。M2（zap_header_m2）与 N3（hyp_log_dump）机器原样随行；R28m 两处代数戳换 R30，宣告行换 R30（"R30: the swallowed-code recovery -- ..."，M2/N3 判决以历史引用在场）。
- 静态自检（scripts/r30_selfcheck.py）：括号平衡 0；字符串字面量 marker 预测全对账（R30 戳 ×2、R30 宣告 ×1、SCMDMP 九标记各 1、adreno_scmsweep ×2【boot-arg 名 + off 文案】、M2/N3/R28k/R28j 保留族全数在场、R28m 两串退役零残留、定义先于调用）；scm_state_dump 1 定义 1 调用点，r30_rows 1 定义 6 调用点。

**R30 三件套（R28m 派生，gen_r30*.py 三步）**：
- tools/r30-flash-build.sh：门禁表 R30 化（代数戳 ×2 + R30 宣告 + SCMDMP 九标记 + adreno_scmsweep ×2；M2/N3/R28k/R28j 保留族原样），gone 表收编 R28m 两串；GPU tokens 不变（adreno=22 adreno_pause=0 adreno_watch=0）；bash -n 过。
- tools/r30-kernel-id.py：docstring 重写（R28m 真机判决入史）、KNOWN 收编 R28m sha 911a69df…（run 40478487680）、MARKERS 表 R30 化（11 新项 + M2/N3 骑行项）、verdict 判据 R30 化（含 R28m 双串缺席断言）；py_compile 过。
- .github/workflows/r30-springboard.yml：缓存键全新（r30-theos-v19L2-v1 / r30-xnu-deps-v1）；③诊断 A 十四项计数（应值全预注）；门禁大表 R30 化 + gone 收编 R28m；sha 禁入表收编 911a69df…；README/release body = **S 判读树**全文（见下）；YAML 解析通过（11 steps，release_tag 默认 r30-springboard）。
- **工具史新课（本轮顺手抓到的潜伏缺陷）**：本系列此前所有轮的 README heredoc 终止符写 20 空格缩进，而 YAML 块标量 dedent = 10 ⇒ shell 见 "          EOF" **永不终止** ⇒ heredoc 吞到脚本尾：已发布各轮 README.txt 尾部带 3 行垃圾（EOF/echo/cat SHA256SUMS）且尾部两条命令从未执行（bash 对 EOF-of-file heredoc 只警告不失败，run 照样 SUCCESS）。实证：下载 release r28m-springboard/README.txt 尾 3 行逐字在案。R30 修复（终止符 10 空格 = 列 0），离线解析验证 bare EOF ×1。

**预注册（先于刷机立字）——R30 S 判读树**：
- 探针自检：P0 宣告行在场；P1 "steps up to 22 … mask 0x0"；P2 拒绝行后 N3 环 dump（对照基线）；P3 SCMDMP 区组逐区出现（QUE 8 行 → HND 2 行 → GLB 12 行 → SCMDMP 2048 行 → TZREG → TZDIAG 384 行（登记字合理时）→ HYPDIAG 256 行（同）），每区后跟 flush 行；P4 任一 "did not map"/flush 超时 = 记录之（继续跑）。
- 判决分支：S1 QUE/HND/GLB 捞到 0x02000201 残迹 + 状态/返回码字段 ⇒ 对照拒绝分支表直接得唯一死因；S2 64KB 区发现 0x3000xx（≠0x300045）⇒ 同表分诊；S3 TZREG 非零且 TZDIAG 出可读文本 ⇒ S3a 有 PAS/GPU/zap/fuse_read 痕迹 = 认证到达 TZ 内部（真码双源互证）/ S3b 无 PAS 痕迹 = 死在 QSEE 入口/EL3（下一步 devcfg/ATF，XBL 镜像）；S4 TZREG = 0 = TZ 从未发布 diag 基址（登记链断在 XBL）；S5 全 did not map/全零 = 非恒等映射假说复活或 hyp 已擦除（下轮 adreno_hyplog 手动 + 分块小读）；S6 wedge = 该区不可从 EL1 读，本身即证据。
- 真码判读表：0x30001B = api_ns 门；0x30001C/1D±n = 主认证流 0x1c04e410（拷贝/版本/哈希/魔数 0x2ec88ff0/元数据 elem 0xa,9,0x15,0x16,5,0xf,0x10/映射/防回滚/分配）；0x30000A..0E = mem pin（0x1c04d75c，sub 0x4d..0x51）；0x30004A 与 ret 8..14 → 0x30004E/4E/4E/50/1F/4F/4D 映射表 @0x1c11cee0；0x300042..49 = handler 错误带（0x300045 不在其中）；小值族（SCM_E_*）或 0xFFFFFFFF = ATF/EL3 入口；0x02000201 无残迹 = 调用未入队（逆向 hyp 队列状态机 0x8571801c）。
- 附带：T2 对照（adsp ACCEPTED 0）照旧先跑 = 链路无罪基础判词；logdump 本轮拒绝现场多次 FLUSH（refusal 行 / N3 环 / R30 每区各一次），**每次 FLUSH 自有区独立截取判读**。设备侧流程：`sh run_ios.sh flash r30 gpu -w 180`。

## R31 预注册 + fork 实证形补丁（2026-10-27，先于刷机立字）

**外部实锤（HeliumStudio fork，用户提交侦察）**：github.com/HeliumStudio-Dev/hackios-xnu-kernel 对【同一台 raphael / SM8150、同一条 XBL → QHEE → EL1 链、同一版本 xnu-8796.121.2】的移植已实现 A640 日常渲染（docs/RAPHAEL.md）："A640 全部初始化步骤、TrustZone zap shader 认证、freedreno/FD640/OpenGL ES 3.2、1080×2340 三角形回读无错误"；"加载、认证与结束卸载均返回成功"。其 zap 认证序列（xnu/files/bsd/dev/hakios.adreno.c）：
- **元数据 = 打包形**：`zap_header()` 硬校验 `52 + 32*segments + hashes == size`——ELF 头 + phdr 表 + 哈希段内容紧邻，**别无他物**（厂商 .mdt 原件形；ELF32，≤一页）；PAS_INIT_IMAGE 两参形（x4=0，与我们的 e1_mode=2 同形）对该页 **ACCEPTED**。
- **代码段分离**：`a640_zap.b02`（≤0x1000）写入落位，零填充到 0x1000。
- **落位 = dt 的 pil_gpu_region 0x98f15000**（MEM_SETUP 三参形，尺寸 0x1000）——推翻我们 R23 时代"pil_gpu_region 无人引用 ⇒ 新页才是 vendor 形"的判读。
- **a640 不预发 PAS_SHUTDOWN**（他们首试 step 18 后的 SHUTDOWN 同样 wedge——与我们 R20 一致；a640 分支直接跳过）。
- 附带同证：SMMU GPU stream 0/1 mask 0x401、GMU stream 5 0x400（与我们 R21 判读一致）；MPSS（PAS 4）与 WLAN 固件同样 ACCEPTED——PAS 通道本身无罪。

**全案改判（三形矩阵闭合）**：stock a640_zap.mbn = 合并量产形（ELF32 头 + 3 phdr @52；哈希段 @0x1000、0x1a38 B = v6 元数据 + 小米链；**表尾 0x94 到 0x1000 为 3948B 零 gap**；代码段 @0x3000、0x7b0 B）。历史两形皆错：R21 = 截断页 [0..0x1000)（头声明 0x1000 而数据在 0x94——自相矛盾；无哈希段内容）；R24+/M2 = 整文件（gap + 代码段全在元数据缓冲内）。**胜形 = [头 52B][表 96B][哈希 blob 0x1a38] 打包 0x1AC8 B，哈希段 phdr 的 p_offset 重写 0x1000 → 0x94**（厂商 .mdt 自身形态；v6 签名覆盖元数据 blob 而非 phdr）。T2 的 adsp ACCEPTED 之谜同解：adsp.mbn 本身就是 mdt 打包形。
- 附带发现（R30 结构性缺陷）：M2 形的拒绝发生在 **step 12**（step_zap_signature），R28m run 到 12 即停——**R30 的 SCMDMP 探针只挂 step 19 的 refused 标签，在 M2 形拒绝下永不执行**（R30 已发布未刷机，被 R31 取代；其 sha 4a708830… 入 KNOWN 与 sha 禁入表）。

**R31 内核补丁（raphael_adreno.c，+124/-18 行）**：
- **zap_metadata_packed(phys, msize)**：stock 文件 → ELF32 校验 → phdr 表解析 → 哈希段（旗标 0x02000000）定位 → 打包页构造（头 + 表 verbatim，p_offset 单字段重写 0x1000→0x94，blob 紧随），0x1AC8 B ≤ 16K 页；to_memory 落缓存；构建行打日志（"the packed metadata is built… the hash segment's p_offset rewritten %#x -> %#x… the code is NOT in this buffer"）。
- **step 12 换打包形** + 拒绝路径补 R30 大扫（refusal 行 → N3 环 → SCMDMP/TZDIAG 全区组 → 收尾，各段独立 flush）——M2 形拒绝死在 12 的结构性补洞。
- **step 19 换打包形**（e1_mode 语义不变，whole = 打包长）；**落位默认 0x98f15000**（adreno_zapmem 覆盖保留）；MEM_SETUP 尺寸 0x1000 不变；写码逻辑不变（文件 PT_LOAD 0x7b0 零填充至 0x1000）。
- zap_header_m2 → `__attribute__((used, unused))` 存档（dead-strip 纪律）；zap_page 加 unused 标注；R31 代数戳 ×2 + 宣告行（"R31: the fork-proven form -- …"）；静态自检全绿（括号平衡、marker 预测全对账、打包函数 1 定义 2 调用、scm_state_dump 两拒绝路径各 1、R30 双串零残留、落位常量在位）。

**R31 三件套（R30 派生，gen_r31*.py）**：
- tools/r31-flash-build.sh：门禁 R31 化（代数戳 ×2、宣告 ×1、打包构建行 ×1、p_offset 重写 ×2、SCMDMP 九标记骑行），gone 收编 R30 双串；bash -n 过。
- tools/r31-kernel-id.py：docstring 重写、KNOWN 收编 R30 sha、MARKERS/verdict R31 化；py_compile 过。
- .github/workflows/r31-springboard.yml：缓存键全新、诊断 A 十七项、门禁大表 R31 化 + gone 收编 R30、sha 禁入表收编 4a708830…、README/release body = **F 判读树**全文；严格重复键扫描 + heredoc 终止符验证通过（11 steps，r31-springboard）。
- **工具史新课（第 N+1 课，同坑二踩）**：gen_r31c 的 BODY 字符串再次自带 `body: |` 头行（与 R30 的 gen_r30c 同一错误），首次替换后 'body' 重复——严格扫描当场抓获，删头行重生成。R30 轮的同型 bug 已进史册（commit 87075a9），本轮仍复犯 ⇒ 新规则：**gen 脚本的 body 常量不得含 YAML 键行**，替换函数以 bstart 定位键行之后。

**预注册（先于刷机立字）——R31 F 判读树**：
- 探针自检：P0 宣告行 "R31: the fork-proven form"；P1 打包构建行 + p_offset 重写行（step 12 一条 + 宣告一条 = 二进制计 2）；P2 全相位 mask 0x0；P3 T2 对照照旧 ACCEPTED (0)。
- F1 = step 12 "TRUSTZONE ACCEPTS THE ZAP SHADER'S SIGNATURE FROM THIS KERNEL" ⇒ 墙倒；step 19 走 MEM_SETUP(0x98f15000/0x1000) → 写码 → AUTH_AND_RESET ⇒ "TRUSTZONE HAS AUTHENTICATED AND STARTED THE ZAP SHADER" ⇒ step 20/21/22 全相位（对照 fork 的 step 20 3D 核心序列）。
- F2 = step 12 仍 0x45 ⇒ fork 差异不止元数据 ⇒ 读 R30 大扫（SCMDMP/TZDIAG）按 S 表分诊真码（0x30001B/0x30001D±n/0x30000A..E/0x300042-49/小值族）。
- F3 = did not map / wedge = 证据。设备侧流程：`sh run_ios.sh flash r31 gpu -w 180`。

## R31 CI 取证与门禁表退役（2026-10-27，第三轮 run 的死因与修复）

**三轮 run 链**：40501869825（efbd832，诊断块 `\"` 转义引号 → 撇号吞噬 → line 160 语法错误）→ 40504377363（33d302a，门禁抓到探针起点行仍是 R30 戳 → 内核对齐 9054e4f）→ 40505873397（9054e4f，**新死因**：门禁断言 `源码构建的内核缺 R23 单一规范尝试`——内核本体已产出 16,199,776 B，make 的 check_all_exports Error-1 与 R30 SUCCESS 日志同族（222 警告），慢性容忍态不是本轮伤情）。

**验尸要点（assert 循环只教一个错）**：want 表 assert 循环死于**第一个**缺失 marker，其后条目全部未证。离线全表审计（scripts/r31_marker_audit2.py，marker-vs-源码树）三层过滤：
- 单文件审计 17 缺 → 全树审计 9 缺 → **真缺失 = 4**（全部 KILLED-BY-R31-DIFF，全部有意退役）；
- 5 条假阴性解释：`the zap attempt follows in this same boot` 与 `no zap shader can be asked for` = 跨行字符串拼接（源码 grep 不到、二进制完整在）；`\x9f\x4e\x00\x71` 与 `\x08\x01\xa0\x52` = 指令编码 marker（门禁查二进制机器码，源码无此文本属正常）；`whether it "supports" the GPU's image` = C 源 `\"` 转义形态（4449 行真实在场）；
- gone 残留 3 条 = 注释/未编译文件（raphael_adreno.c 两处注释 + redwood_adreno.c 未参与 raphael 内核编译），不进二进制，gone 断言不受累。
- **4 条退役入 gone 表（110 want / 41 gone）**：`one attempt, the form both working callers use`（R31 宣告改写为 fork-proven form）；`the place is a fresh page of ours`（落位改 dt pil_gpu_region 0x98f15000，fork 实证）；`dt's pil_gpu_region is referenced by nothing`（fork 证明该区正是 TZ 要的落位，判词语境消失）；`no memory for the zap image`（kmem_alloc 新页路径退役，分配失败措辞由 `no memory for the packed metadata` 承接）。每条 gone 的 what 注明退役理由与继承者。

**修复验证链全绿**：YAML 解析 11 steps；全树重复键扫描过；3 个 PY heredoc 编译过（门禁 221 行含两表 literal_eval 对账 want=110/gone=41、无交集）；6/6 run 块 bash -n 过；`assert d.count(fw)==1` 计数断言只涉 adsp.mdt 嵌入与退役 marker 无关；三件套另两件（flash-build/kernel-id）无该 4 串引用。

**工具史新课（派生表的盲区）**：R31 三件套派生时 want 表从 R28m 原样继承，而内核补丁改写了宣告与落位措辞——**改内核字符串的 commit 必须在 push 前对门禁全表跑一次 marker-vs-源码审计**（assert 首错即停的循环会让 CI 一轮只教一个错，四条缺陷要烧四轮才能暴露）。审计脚本（scripts/r31_marker_audit2.py：全树比对 + 拼接/转义/指令编码假阴性识别 + diff 归因）成为本轮标准工具。

**设备侧计划不变**：F 判读树照旧（F1 = step 12 打印 TRUSTZONE ACCEPTS ⇒ 墙倒；F2 = 仍 0x45 ⇒ R30 大扫按 S 表分诊真码；F3 = did not map/wedge = 证据）。流程：`sh run_ios.sh flash r31 gpu -w 180`。

**R31 CI 收官（run 40515492995，c54f78b，SUCCESS）**：门禁⑦全绿（R11-R31 全标记在场，16,199,776 B）+ 门禁⑤（flash-build 内嵌 count 门）首跑即过（adsp .mdt 7,820 B @ 0x6814 在内核内）+ kernel-id 双镜像双判 R31（v7 loader；GPU cmdline `gpu=1 adreno=22 adreno_pause=0 adreno_watch=0` 无 skip；日常镜像零 adreno token）。**R31 内核 sha256 = 8c86a90e45c8098b363cd797bb6ce31ea297e4255cb4d0ef4e91826f341230d2**（下轮入 KNOWN 与 sha 禁入表）。Release r31-springboard 七资产就位（raphael-xnu-r31-gpu-flash.img / raphael-xnu-r31-flash.img / r31-md1-root.apfs / raphael-r31-flashboot.apfs / README.txt / SHA256SUMS / toolchain-versions.txt）。设备侧：`sh run_ios.sh flash r31 gpu -w 180`，按 F 树判读（F1 = step 12 打印 TRUSTZONE ACCEPTS ⇒ zap 墙倒；F2 = 仍 0x45 ⇒ R30 大扫 SCMDMP/TZDIAG 按 S 表分诊真码；F3 = did not map/wedge = 证据）。

## R32 设备判决 + R33 GPU 常驻轮（2026-10-28，墙倒之后的"留"）

**R32 真机判决（用户 logdump 入账——fork 精确形的首次真机全相位测试）**：逐墙全倒。
- step 12 **"TRUSTZONE ACCEPTS THE ZAP SHADER'S SIGNATURE FROM THIS KERNEL"**（厂商 .mdt 原 pack：头+表 verbatim、p_offset 0x1000 不动、blob 紧随表后；签元数据 TZ 收下，answer 0；调用被抢占 1 次后应答，与 Linux 同形）。
- step 19 **"TRUSTZONE HAS AUTHENTICATED AND STARTED THE ZAP SHADER"**：MEM_SETUP 0x1000 @ dt pil_gpu_region 0x98f15000 → 写码 1968 B → AUTH_AND_RESET answer 0；空探针先答（"this point was reached alive"）。
- step 21 **"the page reads 0x600dc0de: THE 3D CORE WRITES OUR MEMORY ON COMMAND"**；step 22 **"A PICTURE DRAWN BY THE GPU IS ON THE PANEL: orange, white, blue from the top"**——GPU 画的画面真机上屏。
- **然后 step 23 + step-0 把这一切全拆了**：GMU slumber → zap give-up → SMMU 拆表 → CX collapse，"over; the system goes on starting"；SpringBoard 照旧软渲染（"SpringBoard: no GPU draws, vibrant views are plain"）。

**根因（本轮修的 bug，文档-代码契约断裂）**：驱动头注释一直承诺 `gpu=1` = "全部步骤，且 GPU 留给系统画"；但 keep 决策（raphael_adreno.c 步骤循环尾）只读 `adreno_keep`。`gpu=1` 不带 `adreno=` 的 by-itself 日常形确实内建 keep=1（redwood_adreno_init 的 else 分支），可它从未在本项目跑过——本项目 GPU 镜像一直带 `adreno=22`（探针形），`PE_parse_boot_argn("adreno_keep")` 落空 ⇒ keep=0 ⇒ 演示完必拆。R32 GPU 镜像（gpu=1 adreno=22 adreno_pause=0 adreno_watch=0）正是这一形态。

**参照系（用户指令"参考同机型正常使用 gpu 和 WIFI 的 HeliumStudio-Dev/hackios-xnu-kernel"）**：其 `raphael-bringup` 分支 = 同 XNU 基座（xnu-8796.121.2）的并行 raphael 移植，**日常 GPU 形 = 裸 `gpu=1`**（devices/raphael/device.conf BOOTARGS；即 by-itself keep 形），A640 常驻 + freedreno/FD640 GLES（真机 1080×2340 三角绘制+回读无错、桌面/控制中心/Safari 用户确认可用），WiFi = 5 GHz WPA3 SAE H2E 真机联网（独立大移植：ath10k/WCN399x + ICNSS/MPSS(PAS 4)/SMEM/SMP2P/GLINK/QRTR/RMTFS/TFTP/PD mapper/PDR/DMS/BDF/MSA-SMMU/五路 PMIC + SNOC 主机路径 + SAE 加密，~4.5k 行）。⇒ 本项目 R33 对齐其 GPU 常驻形（内核侧同构：keep=1）；GL 用户态与 WiFi 列为下一阶段工程（卷里无 GL dylib：R32 镜像 /redwood/lib 只有 5 个 dylib、无 libredwood.gl.dylib——injector 的 gpu_draws() 第一道 access() 门就挡住了，这正是 SpringBoard 软渲染的直接原因）。

**R33 改动（每轮一个变量：GPU 常驻）**：
- raphael_adreno.c：keep 决策改为 `gpu=1` 也算数（`adreno_keep=0` 显式否决权保留探针-即-拆的历史行为；`adreno_keep=1` 照旧）；代数戳 R32→R33（×2）+ `SCMDMP: R33 begins`；R32 主宣告（fork-exact form 十行块）退役为 R33 resident round 行（其判决已由设备 logdump 入账）；头注释补 R33 语义。step-19 厂商 .mdt 机制、M2 档案、N3 环 dump、R30 大扫、T2 对照全部原样骑乘（含 step-19 的 "(R32: the vendor .mdt below" 骑乘措辞）。
- GPU 镜像 cmdline：r32 token 组原样 + **追加 `adreno_keep=1`**（唯一新增 token，双保险：内核新语义 + 显式 token，boot-args 回显里意图可见）。日常镜像 verbatim。
- keep 路径核验（从 R32 探针状态安全续接）：fence 词页 bzero + to_memory（步骤尾照做）；gpu_free[0] = 0x20000000..0x0FFFFFFF+（探针块全部在 0x20000000 之下，无碰撞）；ring 状态由硬件 CP_RB_RPTR 实时跟踪（提交路径读它算 room），无软件残态；`redwood_adreno_stop()` 在 loader_return 前照常交接（RedwoodGPUStop → gpu_block_stop）。

**R33 三件套（r32 派生，本轮离线验证全绿）**：
- tools/r33-flash-build.sh：底板 = r32 release（内核条目 = R32 44b5a2b9…）；R32C sha 入律、v7 loader / R13 卷（372c7fcb…）原字节断言；GPU 镜像 cmdline = r32 token 组断言在场 + adreno_keep 断言缺席 + 追加 adreno_keep=1（重打包后再验"恰好多一个 token"）；内核门禁 R33 化（代数戳 ×2、resident round ×1、STAYS-ON 行 ×1、SCMDMP 起点 R33、R32 三戳 retired、骑乘串断言在场）。**离线 dry-run 全链通过**：以 r32 镜像内核条目打 R33 标记串的替身内核跑完整打包 → 双镜像产出（8,163,439 B 同尺寸）、跨镜像同内核同卷断言过、GPU cmdline 逐 token 断言过。
- tools/r33-kernel-id.py：KNOWN 收编 R32 sha（判决词：墙倒轮）；MARKERS R33 化（R32 三戳 retired + R32 step-19 骑乘行 + STAYS-ON 行新行）；verdict R33 分支（R33 代数戳 ×2 + resident round + STAYS-ON + R33 起点 + 骑乘串 + R32 三戳缺席）+ R32 回退分支保留；cmdline 判读三态（GPU+keep=1 = R33 正确形 / GPU 无 keep = R32 探针形 NOTE / 日常 = 零 adreno token）。三镜像实测（r32 GPU → R32+NOTE；dry GPU → R33+resident switch；dry 日常 → R33+daily）全对。
- .github/workflows/r33-springboard.yml：底板四件 SHA 硬校验 = r32 官方字节（a5a86f20…/f4b05044…/14485e7c…/372c7fcb…）；缓存键全新 r33-*；诊断 A R33 化；门禁大表 R33 化 + gone 收编 R32 三戳；sha 禁入表收编 44b5a2b9…；README/release body = **H 判读树**全文。验证链：YAML 解析 11 steps、全树重复键 CLEAN、6 run 块 bash -n PASS、3 PY heredoc 编译过（dedent 后）、**门禁块对替身内核实跑全绿**（53 项 grep 全中）。

**预注册（先于刷机立字）——R33 H 判读树**：
- 探针自检：P0 宣告行 "R33: the resident round"；P1 构建行 + p_offset 不动行；P2 全相位 mask 0x0；P3 T2 对照照旧 ACCEPTED (0)；P4 系统起来（R31d 极性修复保持生效）。
- H1 = step 22 之后 "adreno: THE GPU STAYS ON for the system to draw with: the service RedwoodGPU" 且其后再无 slumber/give-up/CX-collapse 拆除块 ⇒ **GPU 常驻路径首次落地**（RedwoodGPU 服务在册、提交环活着）。⚠️ 预期声明：SpringBoard 仍软渲染（"no GPU draws"）——GL 用户态不在卷里，这是下一轮工程，**不是本轮失败**。
- H2 = 任一步骤 FAILED 或 keep 行缺席 ⇒ 整份 logdump 送回按码分诊（12/19/21/22 已是设备实证基线，任何回退都是新变量）。
- H3 = GPU 常驻态系统死亡/风暴 ⇒ 单 token 回滚：`fastboot oem cmdline-append "adreno_keep=0"`（否决权）或直接刷主镜像。

**下一阶段（依 fork 参照立两项工程）**：
- GL 用户态：libredwood.gl.dylib（Mesa/FD640 over RedwoodGPU 的 msm-DRM 子集接口 + eagl/CA 胶水）进卷——R13 谱系卷 4 MB 里现有库共 ~420 KB，Mesa 裁剪后可容；fork 的 userland/src（gl_test/inject_sb/eagl）与其 mk-mesa 流程为参照。
- WiFi：raphael = WCN399x，路径 = ath10k-SNOC + ICNSS/MPSS(PAS 4) + SNOC 主机桥（fork 的 aa21593 为底本，其 docs/RAPHAEL-WIFI.md 列明五路 PMIC/BDF/NV 快照等私有输入）——独立大移植，按轮立项。

设备侧：`sh run_ios.sh flash r33 gpu -w 180`（GPU 镜像先行），按 H 树判读；主镜像为对照。

## R33 CI 收官 + 内核 sha 入账（run 40549952622，2026-10-28）

**run 40549952622（9d6178f，SUCCESS）**：Release r33-springboard 七资产就位（raphael-xnu-r33-flash.img / raphael-xnu-r33-gpu-flash.img / raphael-r33-flashboot.apfs / r33-md1-root.apfs / SHA256SUMS / README.txt / toolchain-versions.txt；双镜像 8,163,439 B）。四件底板 sha256：b0f9f6df…（主镜像）/ 70116827…（GPU 镜像）/ 14485e7c…（flashboot）/ 372c7fcb…（md1 卷）——与 R34 工作流的底板硬校验同值，互证一致。

**R33 内核 sha256 = 420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560**（16,199,776 B；R34 已收编入 KNOWN 与 sha 禁入表，内核字节复现门禁据此断言 R34 不动内核）。公网回读复验（Task 20 纪律）：双镜像下载 sha256 与 SHA256SUMS 全符；tools/r33-kernel-id.py 双镜像双判 R33——内核条目 = R33 resident round + STAYS-ON 行（R32 三戳缺席）；GPU 镜像 cmdline = 全相位 token 组 + 唯一新 token `adreno_keep=1`；日常镜像零 adreno token（对照轮正确）；R13 卷（372c7fcb…）与 v7 loader（ec15106d…）verbatim 骑乘。设备侧 H1 已由用户 logdump 坐实（resident round 全相位跑满，STAYS-ON 后无拆除块；SpringBoard 软渲染为预期行为，GL 用户态 = R34 工程，非本轮失败）。

## R34 设备判决入账（I1 坐实）+ R35 Metal 轮全链（2026-10-28）

**R34 设备判决（用户 logdump r34-gpu-logdump-own.txt，2534 行全文验尸）**：
- **I1 坐实**：`gl_test: freedreno; FD640; OpenGL ES 3.2 Mesa 25.0.7`——GL 全栈（OSMesa→freedreno(FD640)→内核 RedwoodGPU 服务）真机打通；三角 57 ms 画成、29 ms 读回、`674036 of 2592000 pixels inside the triangle (26%; 26% by geometry)`（像素计数与几何双证一致），上屏 8 秒。kernel 侧 R33 的 steps 1..22 + STAYS-ON 行照旧（常驻保持）。
- **本轮课题在案**：`backboardd: CoreAnimation: Failed to initialize Metal or OpenGLES contexts!`（line 2395）——窗口服务器的 Metal/GL 上下文创建失败：R34 卷只有 GL=test 三件（libOSMesa 无 libredwood-gl），injector 的 access 门挡住；Metal 侧发现链的 bundle/属性/开关全部缺席。
- WiFi 现状：`com.redwood.wifi`（net_test）空转退出 code=0；`com.apple.wifi.manager` lookup 失败——R36+ 工程不变。

**R35（Metal 轮）四件套 + 作业表落树（全离线验证绿）**：
- userland pin 前进：aba1e81 → **de5ef58**（新 rc.jobs.r35metal 骑在 R34 CI 期间的三个 Mesa 修复上：6832a54 zlib disabled / e76d965 SDK libc++ / 6025b9e SDK zlib.pc）；`rc.jobs.r35metal` = rwtouch wait 22（系统安静后才动注册表——发现属性存在期被 daemon 吃到的 abort 循环风险按时序隔离，10.14 的实测风险入注释）→ metal_props show/on → metal_test（五步发现链 + CoreImage）→ metal_drv_test --sweep（全类全方法清点）。
- **tools/r35_gl_metal_build.sh**：r34_gl_build.sh 派生 + GL=1 块（mkeagl.py → obj/eagl_gen.h；eagl.c + gl_osmesa.c over 同一 Mesa → libredwood-gl.dylib）+ Metal 块（redwoodmetal.m + redwooddrawable.m 的 **-bundle 形** → RedwoodMetal + metal_plugin.plist；metal_props 纯 IOKit/CF 不碰 Metal；metal_test 八框架行；metal_drv_test）。刻意不带：libredwoodmetal.dylib（dyld install_name 唯一化会破坏 bundle 的类同一性测试——metal_plugin.plist 的实测注释）与 libredwoodair.dylib（AIR 编译器缺席是驱动打印的一句话不是崩溃，compute 下一轮）。八件 Mach-O 断言。
- **tools/r35_volume_build.py**：底料 = **R34 谱系卷**（7e15aaa6…，GL 三件 riding）+ 六件增量（libredwood-gl.dylib、RedwoodMetal.bundle/{RedwoodMetal,Info.plist}、metal_props、metal_test、metal_drv_test）+ 一条链接（System/Library/Extensions/RedwoodMetal.bundle → /redwood/lib/RedwoodMetal.bundle——Metal step 3 自己拼的路径，目录是 iOS 的，链接跟卷走）+ 两处换装（etc/rc.jobs = rc.jobs.r35metal；etc/inject.conf +44 B = `/redwood/lib/libredwood-gl.dylib backboardd`，gpu_draws() 门在 RedwoodGPU 服务在场时打开 = CoreAnimation 经 EAGL→Mesa→freedreno→A640 画 UI 的通道）。selftest 往返 + diff 证明（R34 谱系 81 文件 byte-identical 除两换装）；dry-run 实测 70 files/22 dirs/2 links，卷 6.4 MB。
- **tools/r35_flash_build.py**：底板 = r34 release 双镜像（SHA 硬校验 b0f9f6df…/1a164418…/14485e7c…/7e15aaa6…）；GPU 底板 cmdline 断言 = R33 组 verbatim（无 metal）；新 cmdline = R33 组 + **恰好一个新 token `metal=1`**（IOAcceleratorES 服务接受 MetalPluginName/MetalPluginClassName 的唯一开关，MiniIOMobileFramebuffer.cpp 的门）；daily = r34 daily 逐字节原样（控制组）；payload 预算实测 23.0/64 MB；re-prove 全律。
- **tools/r35-kernel-id.py**：KNOWN 收编 R34 卷 sha（7e15aaa6…）；R35 卷名字表指纹九件（libOSMesa/gl_test/u2f_test/libredwood-gl/RedwoodMetal.bundle/metal_props/metal_test/metal_drv_test/rc.jobs）；cmdline 判词三态（R33 组+metal=1 = R35 正确形 / R33 组无 metal = R34 形 NOTE / 日常 = 零 token）；R33 标记五点断言照旧。
- **.github/workflows/r35-springboard.yml**：⑦段结构；底板四件 SHA 硬校验 = r34 官方字节 + 底板 ramdisk 对照两镜像（日常=R13 字节/GPU=r34 卷字节）；③内核 R33 字节复现门禁照旧；④GL+Metal 构建八件 + Info.plist 类名 grep；⑤卷构建；⑥打包 + r35-kernel-id 复证门禁表（R35 双判/v7 双查/日常 R13×1/GPU R35 卷×1/libredwood-gl=2/RedwoodMetal.bundle=5/metal_props=2/metal=1 恰一/cmdline 全组恰一）+ **J 判读树**全文（J1 发现链全通+sweep 计数 ⇒ Metal 真机打通，下一轮 compute/triangle_app；J2 metal=1 门；J3 bundle 缺位；J4 类注册分诊；J5 metal 绿但 backboardd 仍败 = 预期内；J6 daemon 循环；J7 早期崩；回滚 = 刷回 r34 GPU 镜像）+ ⑦b 发布八资产。验证链：YAML 13 steps、重复键 CLEAN、8 run 块 bash -n PASS、2 PY heredoc 编译过、门禁契约 16 项 grep 全中。
- **本地全链 dry-run（桩 Metal 件 + 真 R34 底板）**：卷构建 81 文件 byte-identical 证明绿 → 打包双镜像 re-prove 绿 → r35-kernel-id 双镜像判读全对（daily=R13 谱系+零 token；gpu=R35 卷+metal=1 恰一）→ R34 底板回溯判读（R34 形 NOTE + R34 卷识别）全对。dry-run 修出两 bug：inject.conf 记录对比误用 manifest dict（应为 sha256 对比）、R13_VOLUME 常量缺位——离线验证的价值实证。

**WiFi R36 材料就位**：helium/raphael-bringup 的 fork 资产 `hakios.mpss.c`（6395 行）/`hakios.wifi.c`（5666 行）/`hakios.qmi.c`（963 行）确认可达（git ls-tree 实证）；docs/RAPHAEL-WIFI-PORT-PLAN.md 的 R35→R36 编号让位 Metal 轮，MPSS 装载轮顺延为 R36（wifi_step 1..8 梯：SMEM→PAS 4→APCS→SMP2P→GLINK→QRTR→RMTFS→TFTP）。

## R36-R39 轮次轨迹 + R39 logdump 全文验尸：GPU 无声卡死（CP_RB_RPTR 停滞）定案 + R40 取证轮（2026-10-29）

**轮次轨迹**：R36 卡 3 色测试屏 → R37 黑屏 → R38 卡白苹果 → **R39 黑屏 + logdump（r39-gpu-logdump-own.txt，5353 行全文验尸）**。R36–R39 四轮全部由本 fork 的 r36..r39 工作流构建发行（origin/main 提交 2585032 = R38 显示轮+WiFi 栈轮、07243c8 = R39 GLES-only 锁屏轮；r39 run 40795022275 SUCCESS，内核 = R38 源码等价重建）。**出处勘误（2026-10-29 R40 轮修正）**：本节初稿判 "R39 内核 = 参考仓库 HeliumStudio-Dev HEAD（5772246）构建"——错。三重字符串证据（`raphael-display: three scanout buffers…`、`the run is about to end…`、`raphael needs WCN3990 SNOC/ath10k…`）当时只在 repo_helium 找到，是因为**本地 clone 停在 ba749ae（R35）**，而 R38 提交 2585032 已把参考仓库的显示/WiFi 代码**原样移植**进本树（raphael_adreno.c/redwood_display.c/redwood_wifi.c，字符串随移植逐字入境）；参考仓库 HEAD 与本地 r39 tag 同日无涉。教训入册：**定罪字符串前先 `git fetch`——stale 树上的"仅存在于"全是冤案**。cmdline = R33 组 + `metal=1 fb_scan=1 wifi=1 wifi_bdf=bd_f11cn.bin`（R39 组原样）。

**R39 症状阶梯判读修正**：黑屏 ≠ 系统死。带时间戳内核日志证明系统本体全活到 163s+（buttons 每轮统计、GPU 传感器 34.6→38.0℃、内存每分钟报、慢核 71s 上线）。**用户 117.7s 短按电源灭屏属正常**（swap 29 清层 = 灭屏黑帧）；致命的是其后渲染再也无法上屏。

**死因链（精确到秒）**：
- [63.0-63.9s] SpringBoard 首批帧：swap 1-5（RedwoodGPU submission 1-4 记 GPU 工时）、flip 1/2/3 latch（`frames++ < 3` 限流——**flip 4+ 成功也静默，"flip 停摆"为假象，排除**）。
- [114.1s] swap 6/7 清层（灭屏前奏）；[117.7s] 电源短按 135ms；[120.0s] selector 12 + swap 8 成功（2 submissions 全记工时）。
- [120.9-123.3s] SpringBoard 密集渲染：锁屏/图标 surface 连创（surface 5-18、图标缓存 180x180 批量）。
- **[123.89s] swap 30 起：`the GPU is not done with surface 4 after 500 ms; frame dropped`——surface 3/4 的渲染 fence 永久停滞**，swap 30-38 循环丢帧至 163s。
- 反证排除三项：ring 满（"the ring is full" 未出现）、≥5s 无限等待超时（GPU_PATIENCE_MS=5000 的 "no answer" 未出现）、fault 类中断（重启快照 `RBBM_INT_0_STATUS = 0`）。
- **[163.7s] 用户强制重启**，关机路径（hakios_gpu_stop→gpu_block_stop，行 3182）快照：`status 0xff9485, interrupts 0, ring read up to 1488 of 2439; the GMU sees it busy: 0x70ff9484`——CP 读指针 1488 处无声卡死（非页错/保护错），GMU 认为 GPU 忙。GMU slumber 无应答（fw_init_result 0xbabeface）、SMMU 拆表遇拒绝（status 0x80000402 @ 0x3500，L2 非己表）——关机拆解期的正常噪声。
- **取证缺口（R39 死得不明不白的直接原因）**：swap 路径 SWAP_GPU_MS=500 < gpuWait 的 1000ms report 门 ⇒ 30+ 次 500ms 超时全部静默；`surface->drawn = request->fence`（hakios.gpu.cpp:976）指向的停滞 fence 只被 500ms 等待盯着，永远够不到取证线。

**WiFi 侧（R39 双判）**：①启动零阻塞——R38 白苹果的"WiFi 早期探测阻塞"嫌疑解除（hakios.wifi.c 探测到固件缺失即干净退出，com.redwood.wifi/net_test 正常 spawn 退出）；②固件缺位确认：`no fw/modem.mdt in the small image: put the phone's /vendor/firmware_mnt/image/modem.mdt into fw-wifi/`——**WiFi 下一步 = 固件进镜像**（参考仓库 README 3.2-3.4：adb pull /vendor/firmware_mnt/image → modem.mdt + modem.b00-31 + wlanmdsp.mbn + bd_f11cn.bin → fw-raphael/；NV 四分区 modemst1/2+fsg+fsc 需 Root 只读备份）。参考仓库 aa21593 已真机验证 WPA2/WPA3 网页访问。

**R40（取证轮，零行为变更 + WiFi 固件轮并行）**：
- 补丁（repo_helium MiniIOMobileFramebuffer.cpp）：`show()` 的 !complete 分支首次触发 `hakios_gpu_report("frame dropped")`——R40 复现时 logdump 将携带 CP_HW_FAULT/CP_PROTECT_STATUS/IB1+IB2 剩余/RBBM_STATUS 全快照，一次定位 CP 停滞原因（WFI 死等/抢占/ME 状态机）。
- WiFi：用户侧提取固件 → 构建镜像带 `wifi=1 wifi_bdf=bd_f11cn.bin` + 固件文件 → 内核栈真跑（wifi_step 分步梯可诊）。
- 若 R40 复现：按快照修根因出 R41；若不复现：WiFi 单独验收。

## R40 GPU 取证轮（2026-10-29）：首次丢帧即出全快照 + WiFi 固件走 flashboot

**依据（R39 验尸的取证缺口）**：swap 路径 `SWAP_GPU_MS=500` < `redwood_gpu_report` 的 gpuWait 1000ms 报告门 ⇒ surface 3/4 的 30+ 次 500ms 超时全部静默（CP 停在 1488/2439、RBBM_INT_0_STATUS=0、GMU 认忙，却无一行快照）。R40 = 一文件补丁 + 零行为变更。

**改动**：
- `MiniIOMobileFramebuffer.cpp show()` 的 `!complete` 分支：首次丢帧调 `redwood_gpu_report("frame dropped")` ⇒ CP_HW_FAULT/CP_PROTECT_STATUS/RBBM_STATUS/IB1+IB2 剩余全快照；`__attribute__((used))` 的 R40 标记串（kernel-id 按它点名换代件，XNULIVE4 先例）。
- `tools/r40_kernel_gate.py`：R40-vs-R39 内核门（shadiff 内容等价门本轮**不可用**——本轮就该差一个补丁）：①尺寸带 0<d<64KiB；②strings 增量恰为 {R40 标记, Darwin 版本行}、损失全为 Darwin 行；③字节簇 ≤32 区 ≤256KiB；④R33 谱系 4 + R38 标记 5 + XNULIVE4 全在场。
- `tools/r40_flash_build.py`：单条目手术——GPU 镜像只换 kernel，loader/cmdline/md1/usb 从 r39 基座原字节提取骑乘（4bf828e4/24ec4e42/5b9bbbcb 硬校验）；日常镜像 = r39 逐字节。
- `tools/r40_flashboot_build.py`：**WiFi 固件走 flashboot（md0）**——R39 log 的 "no fw/modem.mdt" + 固件实重 81.6MB vs 64MB payload 窗 ⇒ 随行小卷装不下，resident 镜像是唯一载体（`oem ramdisk` 后 stage 上限 0xffffffff；choose_window 布局数学：top≈226MB ≪ 数 GB 窗口）。mkapfs `--plain fw`（redwood_image.c 的原位读取形态）；firmware-5.bin + 0-rmtfs-ram 按参考 README 3.4 配方本地生成；NV 四分区缺席可接受（链停在 RMTFS 有名行，L2 预注册）；**私有件不进库不进 release**。
- `tools/r40-kernel-id.py`：KNOWN 收编 R39 kernel/loader/volume 三 sha；R40 判词 = R33 谱系 + R38 标记 + R40 标记 ×1。
- `.github/workflows/r40-springboard.yml`：8 步——**loader 重建与卷构建两步砍掉**（r39 原字节骑乘，少两个 CI 故障源）；底板三件 SHA 硬校验 = r39 release 官方字节；验证链：YAML 12 步/名字 218 字符/同映射重复键 CLEAN/7 run 块 bash -n/3 PY heredoc 编译全过。

**本地全链干跑（替身内核 = r39 字节 + 标记串）**：kernel_gate +58B/1 簇/strings {标记} 全绿；flash_build payload 24.1MB/64MB、daily sha == r39 release 的 b0f9f6df、re-prove 过；kernel-id 双镜像判读全对（daily=R33/R13/无 token，gpu=R40 判词/R39 loader/卷/cmdline）。**flashboot 实构建**：用户固件包（fw-raphael.tar.gz：modem.mdt+b00–b31+wlanmdsp.mbn+bd_f11cn.bin，34 件）→ 35 文件 80.9MB 全 plain（仅 launchd 走压缩），roundtrip 逐字节证明，NV 缺席告警符合预期。

**R40 判读树 L（预注册）**：L1 = 黑屏复现 ⇒ logdump 必含 `adreno: gpu: frame dropped: submission …` 两行快照 ⇒ R41 定点修；无一次丢帧 ⇒ GPU 观察通过结案。L2 = WiFi 双态：原样 flashboot ⇒ R39 的 firmware-missing 行（原地踏步，正常）；固件版 ⇒ `SM8150 MPSS/ath10k is being brought up` ⇒ 走 6..15 步停在 RMTFS（NV 缺席，预期停点）；意外联网 ⇒ 收获轮。L3 = `raphael-display: three scanout buffers` 在场 + frames 越过 4 ⇒ 显示无回归。

## R40 CI 修正（2026-10-29）：门禁按真 CI 字节重建 + pipefail 补课

**run 40826230850（R40 首跑）验尸**：步①–⑤全绿（内核 dd5b7d43 真构建、双镜像、kernel-id 复证全过），死在⑥b 发布——`release/r40-kernel-gate.txt` 资产缺席。根因两条叠加：
1. **门禁律三按替身字节起草，被真构建证伪**：`r40_kernel_gate.py` 的"字节簇 ≤32 区 ≤256KiB"与"strings 增量恰为{标记,Darwin 行}"在替身内核（r39 字节+标记串拼接）上全绿，但真构建 = 一文件补丁 → 一个 section 变长 → 后续所有 section 的 addr/size/offset 级联位移 → 链接器重指所有绝对指针：真差异 = 541,900 区 / ~5MB（99.99% 是簿记）；strings 增量 26 条全是 LC_SYMTAB strtab 里的符号名（新静态变量 `told`、kalloc_type_view、重编号的 TUNABLES）⇒ 律三断言崩溃。
2. **`set -eu` 无 pipefail**：门禁 `python3 … | tee 报告` 的退出码被 tee 吞掉——门禁崩溃后构建照常走完，报告文件从未落盘，发布步因资产缺失而死。门禁形同虚设的根因不是门禁逻辑而是管道。

**修正（bae5ac4）**：门禁六律按真 CI 字节（下载 run 的 r40-diag artifact，stripped 内核 dd5b7d43）重写并本地全绿——①尺寸带 0<d<64KiB（实测 +128B 全在 __LINKEDIT）；②R40 标记 ×1；③R33 谱系 4 + R38 标记 5 + XNULIVE4；④**段表律**（真字节教出来的新律）：18 段名序列全同、全部载荷段 fileoff/filesize 逐字节相同、__LINKEDIT 增量 == 文件增量（外来构建必动载荷段）；⑤**掩码 strings 律**：头表+符号表区先清零再扫描，增量恰为 {R40 标记, 补丁自己的 "frame dropped" 报告字面量, 2 条 Darwin 戳}、损失全为 Darwin 戳，标记钉在真 rodata（0x2a116）不在掩码区；⑥符号数不减（72984→72986）。负样本：r39 冒充 r40 被正确拒绝。步③④⑤全部 `set -euo pipefail`（flash-build 与 kernel-id 的 `| tee` 同病同治）。

**新教训入册**：①替身验证不会重现真编译器的布局级联——"从未见过真构建输出的门禁律默认有毒"，上 CI 前必须拿真 artifact 字节复验（R30"从未跑过的代码默认有毒"的门禁版）；②管道里的门禁必须 pipefail，否则门禁只是装饰；③GitHub API 下 artifact 要手动处理 302 并在重定向请求里去掉 Authorization 头（blob storage 拒绝带签名的二次请求）。

## R41 flush 轮（2026-10-29，先于刷机立字）：快照自己落盘

**R40 真机采集（2649 行）定案的两个事实**：
1. **采集缺口坐实**：loader 的 2 秒 notes 到 ~34s 收摊（`NOTE_SECONDS` 后 watch 核只中断屏蔽地看），此后 logdump 分区只收"内核主动 flush"（loader_return.c 的 XNULIVE3 flush_seq/flush_ack 通道）——而内核只有 adreno 探针的危险调用（`put_the_log_away`）与退出手势的拆除会发起。R40 的采集停在内核时刻 ~44s（Darwin 84s；Darwin = mono + ~40.5s，本 boot 的 USB 下载偏移），恰在 backboardd 显示初始化边缘——远在 ~124s 的停摆案发窗之前：**redwood_gpu_report 的快照打在 ring 里、死在 ring 里**。
2. **退出手势立功入册**：R39 的记录之所以能到 165.05s（拆表/CX collapse 半途），是操作员五指长按 1.5s 的退出手势跑了 `redwood_adreno_stop()` → 拆除路径的 `put_the_log_away()` 多次 whole-ring flush——设计上的"回 loader 通道"意外兼任了唯一的晚期采集通道。R39 尾部 `adreno: 0:` 序列 = 手势拆除（`redwood_input.c` 的 EXIT_FINGERS=5/EXIT_HOLD_MS=1500），不是自动故障。

**R41 内核（一调用补丁 + 同长标记）**：
- `redwood_gpu_report()` 打完两行快照即 `(void)loader_log_flush();`——三个调用点全被 static 门控（`gGPUReported`/`told`），每 boot 至多一次、只在已发生的失败路径；答案不检查（丢一次 flush 不得变成丢一次实验）。
- 标记同长换代：`R41: the snapshot buys its own ride onto the storage too`（56 B == R40 标记长度）⇒ __TEXT/__rodata 尺寸不动，段表律可存活；R40 标记退役（源码零残留）。

**R41 三件套**：r41_kernel_gate（六律重指 r40 release 内核 dc03efe0：尺寸带/双向标记/谱系含取证字面量 ×2——真 r40 字节实测 IOLog 格式串与报告字面量各一/段表/掩码 strings（增={R41 标记,Darwin}，失={R40 标记,Darwin}）/符号数不减）；r41_flash_build（r40 release 单条目手术，4bf828e4/dc03efe0/5b9bbbcb 硬钉，cmdline verbatim，日常= r40 逐字节）；r41-kernel-id（KNOWN += R40；R41 判词 = R41 标记 ×1 **且** R40 标记 ×0）；r41-springboard.yml（底板 = r40 release SHA 硬校验；pipefail 纪律骑乘；诊断 artifact 恒上传内核——门禁真字节翻车时的 R40 恢复路径）。

**离线验证全绿（22 项）**：YAML 12 步/重复键 CLEAN；7 run 块 bash -n；heredoc 编译；门禁替身绿（同长标记 + LINKEDIT 增长且 load command 同步——替身初版没更新段 filesize 字段被律四正确拒绝，真实链接器会更新）；负样本拒绝（原 r40 冒充 r41）；源码标记审计（`frame dropped` ×2 实钉、`THE GPU STAYS ON` 源 ×2/二进制 ×1——redwood_adreno.c 为在册非参与件）。

**预注册（先于刷机立字）——R41 M 判读树**：
- **设备侧采集协议（R40 的教训写成步骤）**：①boot R41 GPU 镜像，让内核跑 ≥4 分钟（越过 ~124s 案发窗）；②黑屏在展时 `fastboot oem screenshot` + `fastboot getvar screen` + `fastboot upload > shot.raw`——loader 一直在 USB 上守着，内核不需要配合，面板实拍直接回答"面板真黑 vs 内容在面板上而背光/合成出问题"；③五指长按 1.5s 退回 loader（拆除 flush 双保险）；④二次 boot 抓 logdump。
- **M1** = 黑屏复现（"frame dropped" 再现）⇒ logdump **必含** `adreno: gpu: frame dropped: …` 两行快照（R41 的 flush 保证落盘）⇒ 快照送回 = R42 定点修（或 gpu_recover=1 轮）。整轮无一次 "frame dropped" ⇒ GPU 观察通过（R39 卡死非稳态），显示线转向 shot.raw + 亮度/合成路径。
- **M2** = WiFi：NV 全量固件版 flashboot（modemst1/2/fsg/fsc 已由设备提取交付，尺寸律全过、三分区互差为活副本特征）⇒ `SM8150 MPSS/ath10k is being brought up` ⇒ 链走 6..15 步越过 RMTFS；意外联网 = 收获轮；新停点 = 新输入。
- **M3** = 回归护栏：three scanout buffers + frames > 4 + flip 行持续 + 系统本体活。
- 回滚 = 刷回 r40 GPU 镜像。

## R41 CI 修正（2026-10-29，run 40838276403 验尸）：门禁必须 grep 工具的真实判定词

**死点**：步②（取 r40 资产 + 基座条目提取）尾部的 ramdisk 门禁——`grep -c "the ramdisk entry is R40"` 得 0（要求恰 1）而退出。此前所有硬校验与判读**全对**：底板三件 SHA == r40 release 官方字节；loader/kernel/ramdisk 三条目提取 sha 逐一命中官方（4bf828e4/dc03efe0/5b9bbbcb）；kernel 判词 R33（日常控制组）/R40（GPU 底板）、cmdline 判词 R39 组 verbatim / 日常无 gpu token 全过——唯独卷判读文本不合门禁预期。

**根因（文本失配，非数据失配）**：r41-kernel-id.py 的 `KNOWN_RAMDISK[5b9bbbcb]` 判定词是双名 **"R39/R40"**（这批卷字节是 R39 底探针集，R40 GPU 镜像原样重装——工具如实双名）；而门禁起草时想当然写了 `the ramdisk entry is R40`——"is R39/R40" 里不存在子串 "is R40"，grep 必 0。同毒第二处预埋在步⑤（`r41-kernel-id.txt` 产品复证）——本轮一并拔除，否则步②修完死步⑤、内核白构建 15 分钟。

**修正（72c3924）**：两处门禁改 grep `the ramdisk entry is R39/R40`，行内注释钉死教训：**门禁必须 grep 工具的真实判定词，不能写起草者假设的那句**。工具侧不改——"R39/R40" 双名是对字节谱系的如实陈述。

**本地全链复验（真 r40 release 字节，20 项全绿）**：同长换标替身内核（R40 标记→R41 标记，56B==56B）过 r41_flash_build 全部输入断言 → 双镜像产出（日常 == b0f9f6df 逐字节，控制组律自证）；r41-kernel-id 对底板与产品双输出跑真实判定 → 修正后的步②/步⑤ grep 集全数命中；**负样本**：旧 pattern 在两份判定输出上恰 0 命中——run 40838276403 的死点原样复现并证明治愈。YAML 12 步/重复键 CLEAN/7 run 块 bash -n。

**新教训入册**：门禁是"对工具输出的断言"，不是"对世界的断言"——判定词改一个字，门禁就从不信任变成假阴性；步②修门禁时必须同步审下游所有同族 grep（本轮步⑤同毒，一次拔除省一轮 CI）。

**重派发**：run 40845645409（72c3924，dispatch 204 后 12s 确认 in_progress）。

## R41 CI 修正 #2（2026-10-29，run 40845645409 验尸）：尺寸律第三次教训 + 通用工作流立法

**死点**：步③内核构建末尾的门禁三——`AssertionError: the r41 kernel is not bigger than r40 (delta 0) -- the patch did not land?`。**补丁其实全上齐了**：门禁一/二全过（R33 谱系 + R38 标记 + 取证字面量 ×2 + R41 标记 ×1 + R40 标记零残留），新内核 sha 6d2463c9 ≠ dc03efe0——字节全变，**字节数恰好不变**（17,008,864 B == r40）。机理：同长标记换代贡献零增量，新增的 loader_log_flush 调用目标早已 extern（无新符号），一个 call 塞进 __TEXT 既有对齐填充 ⇒ 文件尺寸不动。R40 实测 +128B 是因为 R40 有新 static 的符号表条目；R41 一个都没有。

**根因（老毒第三次）**：尺寸律 `delta > 0` 起草于替身字节（替身模拟了 LINKEDIT 增长），不是编译器的律。R40 教训（"替身验证不会重现真编译器"）的复读——同一课，第三种死法。

**修正**：r41_kernel_gate 律一改两条——①新 sha ≠ 底板 sha（字节必须不同 = 补丁确实落上）；②|delta| < 64KiB（δ=0 是真构建的预期结果，注释里写明机理）。**修完立刻拿 run 的真字节（r41-diag artifact 的 6d2463c9）重跑全门禁：六律全绿**——尺寸 δ=0/标记/谱系/段表（18 段全同、载荷逐字节相同、LINKEDIT +0==δ）/掩码 strings（增恰 {R41 标记+2 Darwin 行}，失恰 {R40 标记+Darwin 行}，标记在真 rodata 0x2a116）/符号数 72986→72986。负样本：r40 冒充 r41 被新律一正确拒绝。

**通用工作流立法（用户规则变更：推翻"一轮一个工作流文件"）**：
- `.github/workflows/springboard.yml` = **唯一工作流**，45 个旧轮次文件（r6–r41）全部删除。
- 轮次差异全部收进 dispatch 输入（26 项）：round/release_tag/release_name、底板三件直链+SHA（+三条目 sha 可选钉）、base_id_cmd/base_id_gates（底板判读可选）、pre_gate_py（门禁一/二）、kernel_gate_cmd（门禁三）、flash_build_cmd（手术）、kernel_id_cmd/id_gates（判读+复证）、pre_release_cmd（转发/台账）、release_readme/release_body、checksum_files/release_files。默认值 = 当前 R41 轮 ⇒ 空参 dispatch = 重发 R41。
- 固定骨架：①磁盘/工具链审计 → ②取树+取底板三件（SHA 硬校验，统一名 base/{daily,gpu,flashboot}）+ 通用 XNUP 条目提取（可选 sha 钉）+ 底板判读（可选）→ ②c/d/e 随轮缓存 → ③内核构建（BUILD_DSYM=0+pipefail）+ 门禁一/二/三（可选钩子）→ 诊断 artifact（恒传）→ ④手术模板 → ⑤判读+复证门禁模板 → ⑥SHA256SUMS+README（printf 不走 heredoc，防注入/防 EOF 相撞）→ ⑥b Release（files = 输入）→ ⑥c 直链汇总。
- 教训入册：模板占位符（$K/$REL/$R/$KSHA/$BASE_*/$IDFILE）在各自 run 块内 export，模板经临时文件 `bash -e` 执行——轮次文本永不接触 YAML 结构。

**通用工作流离线证明（harness = 从 YAML 本体抽取 run 块渲染后实跑，scripts/r41_universal_dryrun.py）**：步②全链（真实网络下载 r40 三件 + sha 钉全中 + 三条目提取全中 + 底板判读门禁全绿）；步④替身内核产双镜像；步⑤判读 + 复证门禁全绿；步⑥转发/SHA256SUMS(4 行)/README 全就位；步⑥c 11 条直链。YAML 12 步/26 输入/重复键 CLEAN/7 run 块 bash -n。步③（真构建）只能在 CI 验证——但其内容是 r41 工作流步③的逐字移植 + 两个可选钩子。

## 通用工作流落地（2026-10-29）：springboard.yml 唯一化（45 → 1）+ harness 干跑全绿

（接上节立法记录；提交内容 = springboard.yml 新增 + r6–r41 45 个旧轮次工作流全删 + README CI 段/仓库结构表改写。harness 五步干跑 + YAML 纪律检查全绿的证据链见上节。）

## R41 通用工作流修正 #3（2026-10-29，springboard run#2 / run 40849635001 验尸）：发布步 glob 基准面错位

**战况**：前 11 步首次全绿——步③真内核构建（sha 7a2564bd）+ 门禁一/二/三、步④手术（GPU 镜像 b52fdb48 / 日常镜像 b0f9f6df==r40 逐字节，控制组律成立）、步⑤判读+复证门禁——全部通过，死在 **步⑥b 发布**：`softprops/action-gh-release@v2` 报 `Pattern 'raphael-xnu-r41-flash.img' does not match any files.`，`fail_on_unmatched_files: true` 把它变成致命错。

**根因**：基准面错位。action 的 files glob 相对 **workspace 根**解析，而 `release_files` 输入契约是"**裸名，相对 release/**"——镜像等八件全在 `<workspace>/release/` 下，裸名在根下零命中。工作流前文（SHA256SUMS、直链）都按"相对 release/"语义写，只有 ⑥b 把裸名直接投给了 action，两套基准面无人翻译。

**修正**：步⑥ 内把输入逐行翻译成 `release/` 前缀 glob（GITHUB_ENV heredoc 传 `RELEASE_GLOBS`，不占 25 输入上限），⑥b 改用 `${{ env.RELEASE_GLOBS }}`；⑥ 增加逐项存在性预检，缺件直接报名退出（比 action 的笼统 Pattern 报错好定位）。⑥c 直链保持裸名（release URL 不带 release/ 目录）。

**新律入册**：凡 `uses:` 步接收文件清单，必须先问清单的相对基准面是哪个目录——输入契约相对谁、action 从谁解析，两者不同时由前置 bash 步显式翻译，禁止直接透传。

**离线复验（scripts/r41_universal_dryrun.py 的接棒者 = 渲染步⑥真块实跑）**：15 项全绿——YAML 无重复键/12 步/24 输入/7 run 块 bash -n；步⑥真块在假 workspace（8 件齐 + base 转发替身 + kernel-sha 占位）实跑退出 0，GITHUB_ENV 产出 8 行 release/ 前缀 glob 且 8/8 在根可解析；**负样本一：旧接法裸名在根 0/8 命中（run#2 死点复现）**；负样本二：缺件时步⑥以"资产缺失"退出非零；⑥b 接线/⑥c 裸名直链两断言全过。

## R41 通用工作流修正 #4（2026-10-29，springboard run#3 / run 40853122934 验尸）：子 bash 占位符空串 + pipefail 吞崩溃

**战况**：步⑥ 预检（run#2 修复的产物）首次开火即立功——`release 资产缺失: release/r41-kernel-gate.txt`，比 action 的笼统报错提前且点名。这一缺件揭出了比 run#2 更深的连环伤：**门禁三在步③就崩了，且崩溃被吞，步③假绿**。

**死点机理（两洞叠加）**：
1. **占位符导出面**：门禁三模板 `python3 tools/r41_kernel_gate.py --r40-kernel "$BASE_KERNEL" --r41-kernel "$K" --report ...` 经 /tmp/kernel-gate.sh 交给**子 bash** 执行。`BASE_KERNEL` 显式 export（作者知道子进程要环境变量）、`REL/R` 是 job 级 env 天然导出，唯独 `K` 是步内**普通赋值**——门禁一/二由步 shell 自己展开 "$K"（pre_gate.py "$K"）所以全过，门禁三在子 bash 里 $K 展开成**空串** ⇒ `open('')` FileNotFoundError，工具秒崩。
2. **管道吞崩溃**：模板以 `python3 ... | tee gate-run.txt` 收尾且步内无 pipefail——管道状态 = tee 的 0 ⇒ 步③带着 0 字节的 gate-run.txt **假绿**继续跑。铁证：日志中 `r41-kernel-gate-run.txt` 0 字节 + 步③内 traceback。

**修正**：③ 内 `K=` 改 `export K=`（附机理注释）；**全部 7 处**模板调用（②base-id/②base-id-gates/③kernel-gate/④flash-build/⑤kernel-id/⑤id-gates/⑥pre-release）`bash -e` → `bash -eo pipefail`——门禁工具崩溃 = 步当场死，不再有 tee 垫背。占位符导出面全查一遍：②的 BASE_DAILY/BASE_GPU/BASE_FLASHBOOT/BASE_IDFILE、④的 K/KU、⑤的 IDFILE、③的 BASE_KERNEL/K 均已 export，KSHA/REL/R 为 job 级——无第二只漏网之鱼。

**新律入册（模板占位符定律的第二条）**：凡模板经临时文件交子 bash 执行，其占位符**逐一**核对可见性——export 或 job 级 env 二选一，普通赋值=子进程空串；凡模板内含管道（tee 收尾是常态），调用必须 `bash -eo pipefail`——管道尾命令的 0 不是工具的 0。

**离线复验（scripts/r41_univ_globfix_verify.py，21 项）**：全绿——结构断言（③ 无裸 K= 赋值/全仓 0 处 bash -e /tmp/ 恰 7 处 pipefail）+ run#2 全套 15 项回归 + **子 bash 展开双态**：K 不在环境 ⇒ 模板收到空串（run#3 bug 复现，open('') 崩）；K 在环境 ⇒ open() 拿到真路径。另：本地容器曾回滚到 R35 快照一次（worklog 首次入册的老坑：** convict 之前先 fetch**——远端未失，reset --hard origin/main 即愈）。

## R41 收官 + 真机判决（2026-10-29，run#4 = 40858174758 SUCCESS；用户刷 GPU 镜像：卡白苹果）

**CI 收官**：run#4 8.5 分钟全绿——通用工作流 springboard.yml 首个成功轮（run#1 派发 422 / run#2 发布 glob 基准面 / run#3 占位符空串+pipefail，三修四跑）。Release r41-springboard 八资产齐（双镜像/flashboot/md1/gate 报告/SHA256SUMS/README/toolchain）。镜像复验（tools/r41-kernel-id.py）：GPU 内核判 R41、cmdline = R39 组 verbatim、卷 = R39/R40（5b9bbbcb）、loader = 4bf828e4 verbatim；日常镜像 sha b0f9f6df 逐字节 == r40（控制组律成立）。**发布内核 sha = f17119fb**（CI gate 报告六律全绿）；run#3 的 7a2564bd 同源不同 UUID——不可复现律应验，判读时不得跨 run 钉 sha。

**真机判决（用户 logdump 4546 行，单 boot 全程至用户态 00:03:17）**：
- **M1 = 未触发**：全篇 0 处 "frame dropped"——R41 快照通道武装完毕但前提（首次丢帧）从未发生；loader 记录 27 次落盘（2 秒报 16 + 内核主动 11，后者含 4-6 秒窗的 #4-#13 密集段——快照通道的存储腿工作正常）。
- **M2 = 未到窗**：WiFi 仅内核横幅一行，radio 任务没有拿到窗口（用户态卡死在 SB 之前）。
- **M3 = 绿**：显示 scanout 三缓冲在场、logo 经 redwood-gl 帧 1-4 真渲染、触摸/按键全程存活。
- **R40 的 GPU 停摆墙未复现**（也可能未到达——SB 从未开始绘制）。
- **新墙 = lsd SIGBUS ×78 连续崩溃循环**：00:01:59.712 backboardd 阻塞请求 LaunchServices 数据库 → .713 lsd[29] 立即 SIGBUS；此后每秒一崩至记录尾（00:03:17）；backboardd 自身映射同库也报 "Database mapping failed"。**唯一崩溃者**（其余全部守护进程零异常）；SpringBoard[7] 00:00:48 拉起后活着但全程沉默（无绘制无退出）。
- **判读**：两进程同一文件同死 ⇒ 矛头指向内核侧文件映射页填充路径（vnode pager / mmap 页入），csstore/mapdb 为首个受害者；SB 的沉默与 backboardd 的重试同源。R40 的 GPU 墙与本墙互相独立，boot 间服务调度次序决定先撞哪面。
- **R42 预注册（pager 轮）**：内核在 SIGBUS 交付点记录文件身份（vnode 路径/偏移/长度/返回值）——一次 boot 定罪到具体文件；嫌疑三选一：mmap 页入未实现簇读 / csstore 稀疏或压缩场景下 extent 读零填充 / 文件尺寸报告错误（lsd 映射越 EOF 必 SIGBUS）。修复落点在定罪后开刀。

## R42 开工（2026-10-29，pager 取证轮从预注册到派发）：vm_fault 唯一失败出口的定罪钩

**R42 的一刀（内核三件）**：R41 真机判决定罪的新墙（lsd SIGBUS ×78 + backboardd 同库映射失败 = 两进程一个文件）⇒ 取证轮。落点选择有据：`vm_fault_internal()` 的**最终 return**（osfmk/vm/vm_fault.c ~L6331）是全内核一切用户态页填充失败的**唯一漏斗出口**——越 EOF 检查失败、pager data_request 失败、无 entry 三种嫌疑全部流经此处（pager 层钩不住"越 EOF"情形，因它在调 pager 之前就死了）。三件：
1. **新文件** `files/osfmk/vm/redwood_fault_convict.c`：`redwood_fault_convict(map, vaddr, kr)`——kalloc 先行（Z_WAITOK 不得持锁睡眠）→ map 读锁 → lookup entry（store 层 lookup **不自加锁**，vm_map.c 实证）→ 影子链自顶向下逐级加锁（collapse 的加锁次序，防竞态 UAF）→ 找带 pager 的最深对象 → `vnode_pager_get_object_name()` 取文件名（实现经 osfmk/vm/bsd_vm.c→vnode_pager_get_name→safe_getpath；**持对象锁调它是既有先例**：vm_fault.c 的 CODE SIGNING 拒绝路径同构）→ 全锁释放后 printf + flush。判词格式 `redwood-pager: verdict N: pid …[lsd] address … kr … file "…" fileoff … size …` ×4 种分支 + chain 行 ×1；**节流**：前 3 判词自带 flush（R41 通道），此后每 16 次一跳——lsd 的 1/s 重生循环下全 boot 约 7 次 flush，不啃穿 logdump 分区。
2. **钩子**：redwood.patch 追加 vm_fault.c 单 hunk（26 行）——出口处 `kr != KERN_SUCCESS && map != kernel_map` 调定罪函数（函数内 extern，单 hunk 最小 diff）。
3. **标记换替**：MiniIOMobileFramebuffer.cpp `r41_frame_drop_report_mark` → `r42_pager_forensics_mark`，同长 56 字节（"R42: the fault names the file that died, pager forensics"）。

**六律改形（R42 段落律新形）**：R41 的"payload 段 fileoff/filesize 逐字节不动"对 R42 **不可满足**——R41 加一调用藏进 __TEXT padding（delta 0 是实证结果），R42 加一整个函数 ⇒ __TEXT 必然增长。新律：段名序列不变 + __TEXT 增长 <16 KiB + 其余 payload 段**尺寸不变、fileoff 平移恰等于 __TEXT 增长** + __LINKEDIT 吸收余量（delta = shift + linkedit_delta）。strings 律的允许增益集扩为 {R42 标记, redwood-pager:* 格式串, Darwin 戳}。

**上游接口实证清单**（全部对 xnu-8796.121.2 原文核过）：`vm_map_lookup_entry` 不自加锁（vm_map_store_rb 走裸树）；`vnode_pager_get_object_name` 声明在 **osfmk**/vm/vm_protos.h（osfmk 直调合法，vm_fault.c 同文件先例）；printf→内核 msgbuf→loader 环（loader_return.c 头注释 + R40/R41 设备实证）；kalloc.h 拉 zalloc.h（Z_WAITOK）；printf 声明在 kern/misc_protos.h。

**工具三件**：r42_kernel_gate.py（六律新形 + PAGER_LITERAL x4 断言）、r42-kernel-id.py（KNOWN +R41=f17119fb；新内核分支加 pager 字面量 x4 + R41/R40 双退役断言）、r42_flash_build.py（基准全换 r41 release 锚点：daily b0f9f6df / gpu 0d715343 / kernel entry f17119fb）。springboard.yml 默认值 21 处换代 R42（空参派发=重发 R42）。

**离线证明（scripts/r42_univ_round_proof.py，13 项全绿）**：YAML 健康（24 输入/7 run 块 bash -n/默认值=R42 逐项）+ 源码三件互证 + **全链 apply（redwood.patch + files/ 在上游真树副本落地，vm_fault 钩子/标记/convict 三验）** + 工具 py_compile ×3 + **合成内核六律 GREEN**（R41 形基准 + R42 形新件：__TEXT +0x200、中段平移、标记换替、x4 判词、掩码 strings）+ **四负样本各被对的律点名**（外来构建改段尺寸→段落律；R41 标记未退役→标记律；判词 x3→谱系律；__TEXT 越带→16KiB 律）+ 手术工具合成底板打包自证 + 判读工具双镜像判词（GPU=R42+R39 组 verbatim；日常=R33 控制组）+ 判词词对齐律（workflow 门禁 8 个 grep 词与工具真实判定词互证）。合成的三次返工全被门禁自己逮住（漏 R33 第 4 标记/段布局重叠/双计偏移）——工具的牙口在离线阶段就已验过。

**新律入册（取证轮落点律）**：取证钩必须埋在**失败汇合的唯一出口**，不埋在可疑路径的某一处——越 EOF 类失败根本不进 pager，pager 层钩子必然漏；出口钩要自带锁安全论证（map 读锁 + 影子链 collapse 序加锁 + 取名持锁的既有先例）与节流（重生循环会把无节流的取证变成日志洪水）。

## R42 run#5 验尸（2026-10-29，40885924498 红）：新增内核源文件未注册 conf 清单——链接器点名 undefined symbol

**死亡现场**：步③构建 6 分钟后在最终链接步死——`LD mach.development.bcm2837.unstripped` → `Undefined symbols for architecture arm64: "_redwood_fault_convict", referenced from: _vm_fault_internal in vm_fault.o` → `ld: symbol(s) not found` → "内核没有产出"。apply.sh 全绿（74 changed + 118 new files），编译阶段零 error——`-k` 模式把死期推到链接。

**根因**：R42 是**史上第一个新增"需要 conf 注册的内核源文件"的轮**。`files/osfmk/vm/redwood_fault_convict.c` 由 apply.sh `cp -a` 落树，但 XNU 只编译 conf 清单（osfmk/conf/files 等）登记的文件——注册行缺席 ⇒ 文件不编译 ⇒ vm_fault 钩子的 extern 无定义 ⇒ 链接死。此前轮次的"新增"只有两种：改 Apple 既有文件（无需注册）、被 `#include` 进别的编译单元的库源（libdeflate/libzbitmap 四件，无需注册）、或覆盖上游同名文件（Apple 自带注册行，kpc.c/tunables.s 属此）。离线 13/13 证明验的是"patch 应用 + 合成内核过门禁"——从未问"构建系统认不认这个文件"。

**修复**：redwood.patch 的 osfmk/conf/files hunk 追加 `+osfmk/vm/redwood_fault_convict.c	standard`（minicrypto 三件同列先例），hunk 头 `+361,6` → `+361,7`（手改，字节级 tab 对齐）。

**复验四律（scripts/r42_conf_registry_proof.py，GREEN）**：
- **HUNK-MATH**：全 patch 193 hunk 头行数与体行数自洽（防手改错行数）。
- **R42-REG（本轮新律）**：fcd1133 新增的每个可编译物（.c/.cpp/.s）必须出现在 patch 的 `+` 注册行里——上游树是纯 Apple tag，故"我们加过的全部注册"必然都在 patch `+` 行中，此断言不需上游树。
- **REG-EXPLAINED**：未注册文件必须可解释——#include 消化（4 件实锤）或历史遗留（redwood_adreno/buttons/goodix = 上游 POCO X5 Pro 死文件被 raphael_* 替代；kpc/tunables.s = 覆盖件），r22 引入、R22→R41 全绿链背书，降 warn。
- **LINK-CLOSED**：patch 新增的每个 extern 必须在**已注册**文件里有非 static 定义（static 不解 extern 引用；死文件里的定义不链接）。`redwood_fault_convict() → osfmk/vm/redwood_fault_convict.c（已注册）` ✓。
- **真树定向 apply-check**：从 apple-oss-distributions/xnu tag xnu-8796.121.2 拉 osfmk/conf/files 上游原文（363 行，`-361,3` 三行逐字节对齐），单文件 hunk `git apply --check` PASS + 实应用后清单尾部即新注册行。其余 73 hunk 的 apply 能力由红 run 本身背书（apply.sh 曾全绿）。

**新律入册（构建接纳律）**：向内核树新增源文件，注册行与文件本体是**同一刀的两半**——文件进 files/ 的同一提交里必须让 conf 清单认领它；离线证明必须覆盖"构建系统接纳"（注册完整性 + 链接符号闭环），门禁合成内核证明不了链接器看不见的东西。链接器是最后一道 也是最诚实的门：它报 undefined 时，缺的从来不是声明，是编译单元。

## R42 run#6 验尸（2026-10-29，40893195847 红）：编译器点名成员词表——conf 修复生效，死点前移一级

**死亡现场**：步③构建在编译 `redwood_fault_convict.c` 时死——`no member named 'offset' in 'vm_map_entry'`、`no member named 'object' in 'vm_map_entry'`。相对 run#5 的链接死，死亡点**严格前移**（链接器→编译器）：conf 注册修复生效，文件已进编译。

**根因**：文件是照"理想化 vm_map_entry"写的，而 xnu-8796 的真实表示是 packed 形态——(1) offset 成员真名 **`vme_offset`**（位域，声明跨行：类型在上一行）；(2) **没有 `entry->object` union**——对象打包在 `vme_object_or_delta` 里，唯一合法出口是 `VME_OBJECT()` accessor（vm_map.h 内 static inline `_VME_OBJECT`，含 VM_OBJECT_UNPACK + assert !is_sub_map，调用点已先查 is_sub_map 并 return，同 vm_fault.c 用法）；`vme_start/end/prev/next` 过关纯属它们是 `links.*` 的宏别名。附修：`get_bsdtask_info` 隐式声明转文件内 extern（proc_selfpid 先例）。

**复验（scripts/r42_struct_members_proof.py，GREEN，机械化）**：从上游原文（apple-oss-distributions xnu-8796.121.2 的 vm_map.h 1774 行 / vm_object.h 1166 行）提取 `struct vm_map_entry` 39 成员（含跨行位域行：成员行可带前导注释、类型在上一行）与 `struct vm_object` 30 成员，文件内每个 `entry->`/`object->` 访问必须落进词表。提取器自身也被上游原文校准两次（正则对 `ln[1:]` 又用 `^\+` 的恒空集合 bug；跨行位域漏提取）。

**新律入册（STRUCT-MEMBERS 律）**：触及结构体内部的轮，证明必须交出结构体**自身词表**——从上游头文件机械提取成员集逐个核对，不靠记忆。接口与声明核过了，成员名没有核；clang 的诚实不亚于链接器，两者都必须在 runner 收费之前在线下答完。

## R42 run#7 验尸（2026-10-29，40895517421 红）：门禁自己成了最后一堵墙——构建全绿，死的是"__TEXT 必须增长"断言

**死亡现场**：内核产出（17,009,200 B，diag 副本 sha 61d2c2de），门禁 1/2 过，死在门禁 3 段落律断言 `shift > 0 -- the new function did not land?`——实测 delta = 0。链接器墙与编译器墙双双已倒。

**根因**：整轮增量装进了 __TEXT 页对齐尾部的 padding（R41 真实结局重演），+336 B 全是 __LINKEDIT 符号/串增长（nsyms 72986 → 72993，新函数的符号）。run#7 立项时改写的段落律（"R42 加一整个函数 ⇒ __TEXT 必然增长"）编码的是**对本轮量级的预测**，而预测死于构建找到更便宜的合法形态。

**修复**：段落律改成只断言一切合法形态共有的性质——`shift >= 0` + 16 KiB 带 + 中段平移链（各 payload 段尺寸钉死、平移量彼此一致）+ `__LINKEDIT 吸收余量`（delta = shift + linkedit_delta，delta=0 时自动成立：336 == delta − 0）。对**真镜像对**离线复验（非合成）：r41 GPU release 镜像（0d715343）经 r42_flash_build.unpack 解出内核 f17119fb × run#7 diag 内核 61d2c2de，修后门禁六律全绿（size/marker/lineage/segment/strings/symtab）。

**新律入册（门禁预测律）**：门禁断言若编码了对构建的**预测**（"它必须增长"），它死于构建找到更便宜合法形态的那一天；只断言对每个合法形态都为真的性质——不缩、尺寸钉死、平移自洽、LINKEDIT 归账——永不断言本轮的预期量级。

## R42 收官（2026-10-29，run 40900710871 SUCCESS，13d4fca）：Release r42-springboard 发布，pager 取证轮交付

**发布**：run 40900710871（15:21:24Z 起，13d4fca = 门禁段落律新形）**SUCCESS**，Release **r42-springboard** 八资产 8/8（raphael-xnu-r42-flash.img 8,163,439 B / raphael-xnu-r42-gpu-flash.img 12,664,943 B / raphael-r42-flashboot.apfs 4,194,304 B / r42-md1-root.apfs 7,016,448 B / r42-kernel-gate.txt / SHA256SUMS / README.txt / toolchain-versions.txt），下载件 sha 与 SHA256SUMS 逐字节吻合。

**六律绿（真发布对）**：r41 内核 f17119fb（17,008,864 B）× r42 内核 **ef204f28**（17,009,200 B，**+336 B**）——size 律（带内 +336）；marker 律（R42 标记 x1 / R41 标记退役 x0）；lineage 律（R33 gen4 + R38 display/WiFi 5 + XNULIVE4 + "frame dropped" 全在 + **"redwood-pager: verdict" 字面量 x4**）；**segment 律新形实测 = 18 段名序列不变 + __TEXT +0 B（整轮装进页尾 padding）+ 其余 payload 段 +0 平移 + __LINKEDIT +336 B 恰归账**；strings 律（+8/−3，标记落 0x29f36 真 rodata）；symtab 律（nsyms 72986→72993，strtab 0x202ce0→0x202d68）。**GATE GREEN**。

**双镜像复验（tools/r42-kernel-id.py）**：GPU 镜像 = loader R39..R42 4bf828e4 verbatim + 内核判 **R42**（ef204f28，R40/R41 双退役成立）+ 卷 5b9bbbcb verbatim + cmdline R39 GPU 形 verbatim（fb_scan=1 + wifi=1 wifi_bdf=bd_f11cn.bin）；日常镜像 = 控制组原字节（b0f9f6df 逐字节，R33 内核 420f4b71 + v7 loader，cmdline 无 GPU/WiFi token）。

**发布内核 sha 记录**：`ef204f284b5825d3598d7b2630159945dbb11e990f0eac75bcbff30f3f15c021`（17,009,200 B；日常控制组 b0f9f6df 不变）。

**R42 修复链全记录**：fcd1133（立项）→ run#5 红 → 2bf6fde（conf 注册行，链接器墙倒）→ run#6 红 → 2f92ef0（vme_offset/VME_OBJECT，编译器墙倒）→ run#7 红 → 13d4fca（门禁段落律去预测化，最后一墙倒）→ run#8 绿。死亡点轨迹：链接器 → 编译器 → 门禁——三层门逐一诚实，三律逐轮入册（构建接纳律 / STRUCT-MEMBERS 律 / 门禁预测律）。

**下一步（用户侧）**：刷 raphael-xnu-r42-gpu-flash.img（fastboot flash boot）→ ≥4 分钟观察窗（lsd 1/s 重生循环自然喂判词，前 3 判词自带 flush）→ 五指长按 1.5s 退回 loader → 二次 boot 抓 logdump 回传。判读树：MR1 = `redwood-pager: verdict N:` 定罪文件（文件名 = R43 开刀对象）；MR2 = SIGBUS 复现而判词缺席 → 变体行（no map entry / no pager in the chain / kept its name）定假设之罪；MR3 = 回归护栏（零丢帧/GL logo/触控/WiFi 窗口不回退）；MR4 = WiFi。

## R42 真机判读（2026-10-29，logdump 5600 行）：取证钩活了、lsd 假设被证伪、钩子自己引发了内核 panic

**三棵判读树落地：**
- **MR1 未达成**：160 条判词（编号 = 累计 fault 计数，79.2s → 148.0s）**没有一条定罪 lsd**——被告全是 init[4/18/23/33]、launchd[1]、xpcproxy[9/20/29/36]，形状清一色 `kr 0x1: no map entry`，地址高 32 位异常（0xff700001/0xffc00001，非法用户地址，probe 被正常拒绝 = 良性；打印形状存疑，R43 核对 printf 参数序）。这些 fault 钩子在场外看不见它们的年代就一直存在，只是 R42 第一次看见了。
- **MR2 命中（假设证伪）**：lsd SIGBUS 重生循环**照旧**（pid 34→111，00:03:04 时已 "crashed 65 times in a row"，每次 **7 ms 内**死亡：running → exited due to SIGBUS | sent by exc handler），而 160 条判词里**零条 lsd**——lsd 的死法**根本不经过 vm_fault_internal 的失败出口**。R41 的嫌疑清单（页入未实现/extent 零填充/尺寸报告错）三选一全错方向。log 内两处自家历史注记点名真凶候选：`minisandbox: the CPU monitor's override goes to every process (the refusal was fatal to lsd -- R28d)`——**7 ms 死亡 + SIGBUS by exc handler + 无 fault 判词**，与 sandbox/CPU-monitor 拒绝路径（Mach exception → exc handler → SIGBUS）完全吻合；backboardd 的 "Database mapping failed" 是 lsd 死的下游效应而非同因。
- **MR3 破防**：R41 的零丢帧纪录破——9 处 `frame dropped`（swap 30-38，"the GPU is not done with surface 3/4 after 500 ms"，123.9s 起）；RedwoodGPU 统计 34 submissions in 37,568 ms（提交队列堵死）→ 164.9s adreno 复位路径（"THE SMMU REFUSED the 3D core"）→ END OF LOG。黑屏链 = GPU hang（123.9s 起渲染停摆）→ 复位路径 → panic 终局。
- **MR4 无回归**：WiFi 固件缺失依旧（"modem/WLAN firmware is missing from the RAM image"，用户侧固件未入 RAM 镜像，非本轮问题）。

**黑屏终局主犯 = 内核 PANIC，panicked task = pid 7 SpringBoard**：
```
panic(cpu 1 caller 0xfffffff0072d32c0): lock_rw.c:877 Assertion failed:
lock->lck_rw_owner != self->ctid : Lock already held state=0x4a0000, owner=0xfffffff3eb94cc10
```
owner = panicked thread **自己** = **RW 锁递归死**。链条：vm_fault_internal 的失败出口调 redwood_fault_convict() → 钩子 lck_rw_lock_shared(map 锁) → 但调用者已在**持同一把 map 锁**的路径上（wire/COW/fork-exec wiring 类路径在持锁状态下进 fault；SpringBoard 的 IOSurface wiring 是当场捕获的现场：surface 19/21/22/23 就在 panic 窗口创建）→ 同线程递归拿锁 → 断言死。R42 立项的锁安全论证（map 读锁 + 影子链 collapse 序 + 取名持锁先例）论证了**与 lookup/影子链的协调**，**漏了"调用者自身已在持锁"的调用方状态枚举**——而失败出口的调用方锁状态恰恰不可枚举（这正是把钩埋在"一切失败汇合的唯一出口"的代价：唯一出口的唯一性也意味着调用方状态的多样性）。

**R42 真机收获盘点**：取证钩全链在真机活了（钩子触发、判词编号、节流 16 一跳、前 3 判词自带 flush、判词进 logdump）——**基础设施全绿，只是第一批"被告"不是我们要的**；160 条 no-map-entry 判词顺带证明了节流参数可用。

**R43 立案（panic 修复轮，单文件单刀）**：redwood_fault_convict.c 的 map 锁改 **try-lock**——lck_rw_try_lock_shared 失败（含同线程已持任何模式）即放弃深查、交最小判词或静默返回，**绝不阻塞、绝不递归**；同时对 no-map-entry 判词做形状去重（同 (pid,kr,变体) 只报首例 + 计数，160 条洪水 → 数条）。取词语义与断言路径须对 xnu-8796 原文核对（lck_rw_try_lock 的 shared 分支不得携带 owner==self 断言——STRUCT-MEMBERS 律同款纪律）。lsd 死因二次取证留 R44：若 R43 后 SpringBoard 存活、logdump 变长，先看 lsd 完整死亡序列再决定钩子是否搬去 minisandbox 拒绝出口。

**新律入册（唯一出口律的对偶）**：埋钩在"一切失败的唯一出口"是对的（取证完备性），但**唯一出口同时是调用方状态最多样的出口**——钩子在其中持有的每一假设（"此刻无人持锁"）都必须枚举为假的可能；探针只许用 try 类原语，try 败即退——取证代码自己死 = 取证结束。

## R43 run#8 验尸（2026-10-30，run 40917994778）：门禁 lineage 律的陈旧断言——R42 轮复制件漏重锚

**死亡点**：步骤 ③/⑧ 内核构建 + 门禁，`tools/r43_kernel_gate.py:169` —— `assert old.count(PAGER_LITERAL) == 0, 'the r41 base already carries the pager literal?'`。构建本体全绿（新内核已出炉、KSHA 已定），死在六律第三律的**基线侧**断言上。

**根因**：0f38500 重锚 R42→R43 时改了三处（base sha 断言 `osha == R42_KERNEL`、marker 律 R43-in/R42-retired、新侧 pager 字面量 x6），**漏了第四处**——`old.count(PAGER_LITERAL) == 0` 是 R42 轮门禁（彼时 old = r41 基线，确实 x0）的原句复制。而 R43 的 old = **R42 发布内核**，R42 轮恰恰是四个 pager 判词格式落地的轮次——基线自带 x4。本轮的法律是 **4 → 6**，不是 0 → 6。docstring 里的"x0 in the base"同病同源，一并修。

**修刀（单文件 tools/r43_kernel_gate.py）**：`old.count(PAGER_LITERAL) == 0` → `== 4`（注释刻入 run-8 教训）；docstring x0 → x4；顺带清三处陈旧标签（'r41 kernel'/'r42 kernel' 的 say 标签实指 R42 基线/R43 新内核，"frame dropped" 断言消息里的 'r41 base'）。

**离线三重复验（scripts/r43_run8_gate_verify.py）**：A) 从收割的 r42 GPU 镜像抽出真基线内核（sha 逐字节 = ef204f28，17,009,200 B），实测 pager 字面量 **x4** = 修后断言值；B) byte-identical 对仍被 size 律拒绝（law 1 先于 law 3 开火，负例不倒）；C) 合成合法对（同长标记换装 + 两条 Darwin 版本行覆写为 pager 字面量，delta-0）走完全部六律到 **GATE GREEN**——修后的 169 行在真基线上被真实执行过。全工具链同病扫描：r43-kernel-id.py:210（新侧 x6）、r43_flash_build.py:127（内核侧 x6）、springboard.yml pre_gate_py（x6）/base_id_gates（基线判 R42）——全部正确，陈旧断言仅此一处。

**新律入册（重锚律）**：把门禁从 N 轮重锚到 N+1 轮时，**基线侧的每一个字面量计数都是一条必须重新推导的律**，不是可以继承的家具——复制件幸存者恰恰是那个值变了（0→4）的断言。重锚完成的标志是：对真基线字节逐条跑过基线侧断言（本轮 A 步从真镜像抽内核实测 x4 即此律的执行），而非仅对新侧做负例烟测（run 0f38500 的离线烟测只打到 law 1，未及 law 3——**负例烟测的覆盖必须包含每一条改过的断言**）。

## R43 run#10 验尸（2026-10-30，run 40920542879）：下载超时预算——单 URL 180 秒的网络彩票

**死亡点**：步骤③/⑧ 的**第一段**（WiFi 对象），`tools/mk-ath10k.py:37` `archive('freebsd')` —— curl 拉 FreeBSD 15.1-RELEASE src.txz（253,039,000 B ≈ 241 MB）在 `--max-time 180` 处超时（curl exit 28），收到 225,951,196 B = **89 %**，节点速率 ~1.2 MB/s（需 ~3.5 min）。**门禁本轮未上场**（mk-ath10k.py 在内核构建与门禁之前，run#8 的门禁修复仍待真 CI 对检验；diag artifact 里的 .bin 清单是静态上传配置，非产出证明）。

**根因**：下载预算是**网络彩票**——run#8 的节点 180 s 内拉完（侥幸），run#10 的节点慢 20 %（被杀）。脚本对单 URL 单次尝试零重试零断点，241 MB 级大文件在 CI 节点速率波动下必然复发；linux-7.0.14（~150 MB）同暴露。run#12（40900710871）能过纯属节点快。

**修刀（单文件 tools/mk-ath10k.py 的 archive()/fetch()）**：①单 URL → 镜像组（freebsd 主源 + Leaseweb 官方镜像，本容器实测 200 OK；MIT/Clarkson 404 弃用；**下载后 sha256 铁闸不变，错字节永不静默**）；②180 s → 600 s/次 × 4 次外层重试，镜像轮换；③`-C -` 断点续传（.partial 跨次增长）；④停滞探测器（<10 KB/s 持续 60 s 即弃投下轮，不死等预算）；⑤sha 不符即**毒杀** .partial（删除重下，坏字节永不复用）；⑥完整未收编的 .partial 直接收编（防 `-C -` 吃 416 死循环）。**端到端真字节验证（scripts/r43_run10_fetch_verify.py）**：hostap（3.8 MB 真下载）sha 落盘 ✓、sha 快路径零网络 ✓、完整 .partial 零 HTTP 收编 ✓（416 守卫）、错 sha 毒杀 + RuntimeError ✓（真续传演练：curl 从 33 B 断点续完）。mk-ath11k.sh 的 freebsd 下载同源不同病（无超时上限，会挂不会炸）但不在 R43 路径，本轮不动。

**新律入册（下载预算律）**：CI 里的钉版大文件下载，预算必须按**最慢合法节点**定（实测最差 ~1.2 MB/s → 241 MB 需 ≥ 7 min 预算），且必须带断点续传 + 重试 + sha 铁闸——单次单 URL 的 180 s 是把构建成败交给 CDN 节点分配器。镜像冗余是第二道，sha 校验是唯一裁决。

## R43 收官（2026-10-30，run 40922899548 SUCCESS，fa37d34）：try-lock 探针轮发布

**修复链全记录**：0f38500（R43 立项 + 工具链重锚）→ run#8 红（40917994778，门禁 lineage 律陈旧断言 `old.count(pager literal) == 0`——R42 轮复制件漏重锚，R43 的基线是 R42 发布内核自带 x4，法律 4→6 不是 0→6）→ 2aa0542（基线断言 ==4 + docstring/标签三处陈旧件，重锚律入册）→ run#10 红（40920542879，mk-ath10k.py 拉 FreeBSD 241 MB 在 180 s 预算 89 % 处超时——下载预算律入册）→ fa37d34（镜像组 + 续传 + 重试 + sha 毒杀 + 416 守卫，真字节四路径验证）→ **run#11 绿（40922899548）**。死亡点轨迹：门禁（基线侧）→ 下载（预算侧）——本轮两刀都不是内核代码，内核与探针本体一次编译通过。

**六律绿（真发布对）**：r42 基线 ef204f28（17,009,200 B）× r43 内核 **cddf7a81**（17,009,200 B，**+0 B**）——size 律（带内 delta +0，本轮整轮装进 __TEXT 页尾 padding）；marker 律（R43 标记 x1 / R42 标记退役 x0，同长换装 56 B，落 0x29e76 真 rodata）；lineage 律（R33 gen4 + R38 display/WiFi 5 + XNULIVE4 + "frame dropped" x2 全在 + **"redwood-pager: verdict" 字面量 x6 = 基线 x4 + 两条新 busy 格式**——run#8 修复在真 CI 对上兑现）；**segment 律实测形 = 18 段名序列不变 + __TEXT +0 B + 其余 payload 段 +0 平移 + __LINKEDIT +0 B 恰归账（全程 delta 0，symtab 一字未动：nsyms 72993→72993，strtab 0x202d68→0x202d68）**；strings 律（+5/−3，掩码区外）；symtab 律（nsyms 持平）。**GATE GREEN**。

**双镜像复验（tools/r43-kernel-id.py）**：GPU 镜像（e4860be9）= loader R39..R43 4bf828e4 verbatim + 内核判 **R43** cddf7a81（pager x6 + R42/R41/R40 三退役）+ 卷 5b9bbbcb verbatim + cmdline R39 GPU 形 verbatim（fb_scan=1 + wifi=1 wifi_bdf=bd_f11cn.bin）；日常镜像 = **b0f9f6df 整文件逐字节 = R42 日常控制组原字节**（R33 内核 420f4b71 + v7 loader + R13 卷，cmdline 无 GPU/WiFi token）。

**发布内核 sha 记录**：`cddf7a81362d85f2fe2d991e21936d0ba2b4ee5e680eace85719e2fa5bc93306`（17,009,200 B；日常控制组 b0f9f6df 不变；tag r43-springboard，八资产 8/8，SHA256SUMS 逐字节吻合）。

**R43 轮的真机使命（对照 R42 验尸）**：R42 黑屏主犯 = 探针自己的 RW 锁递归（shadow-chain 尾部预锁 + 顶部再锁，lock_rw.c:877 owner==self，SpringBoard 的 IOSurface 影子链当场捕获）。R43 的单刀：每把锁都是 try（vm_map_try_lock_read / vm_object_lock_try_scan，非阻塞无断言原语），每层链顶部只锁一次，busy 即最小判词走人（`map lock busy` / `chain lock busy at depth N` 两条新格式）；判词去重治理 160 条 no-map-entry 洪水。**判读预期：MR3（无 panic）是底线，MR1（lsd 定罪）预期仍缺席非失败**——lsd 的 7 ms SIGBUS 死不经过 vm_fault_internal 失败出口（R42 判词已证伪页填充假设），sandbox/CPU-monitor 拒绝路径是 R44 的线索。

**下一步（用户侧）**：刷 raphael-xnu-r43-gpu-flash.img（fastboot flash boot）→ ≥4 分钟观察窗 → 五指长按 1.5s 退回 loader → 二次 boot 抓 logdump 回传。判读树：MR1 = `redwood-pager: verdict` 定罪文件（lsd 预期仍缺席，非失败）；MR2 = busy 变体行（`map lock busy` / `chain lock busy at depth N`——探针在真实持锁路径上让路的直接证据）；MR3 = panic 护栏（全 logdump 不得出现 `Lock already held`、`panic(cpu`）；MR4 = 丢帧与 R42 相若（9 处/123.9s 起的 GPU hang 链是否复现）；MR5 = WiFi（固件缺失注记依旧非失败）。

## R43 真机判读（2026-10-30，logdump 11215 行，292 s 窗）：两害尽除，只剩一凶——用户态 SIGBUS 浪潮

**判读树落地**：
- **MR3 达成（底线守住）**：全 logdump **零 panic、零 "Lock already held"**——R42 的探针自杀（RW 锁递归）被 try-lock 单刀根治，内核在 292 s 观察窗内全程存活（"the kernel is still running"），heartbeat 稳定。
- **MR4 达成**：**零 "frame dropped"**——R42 的 GPU hang 链（9 处丢帧 → 37.5 s 提交停摆 → adreno 复位）整个消失；redwood-gl（Mesa/Freedreno 栈）扩展枚举齐全，GL 基础设施活着。
- **MR1/MR2 空白**：**零判词、零 busy 行**——pager 钩全程静默。SIGBUS 死亡全部不经过 vm_fault_internal 失败出口（再次证实页填充假设证伪、sandbox 拒绝路径是唯一嫌疑）；R42 的 160 条良性 no-map-entry 洪水也未复现。
- **MR5 不变**：WiFi 固件依旧不在 RAM 镜像里（"modem/WLAN firmware is missing from the RAM image"——镜像侧装包问题，非内核）。

**"卡3色" = adreno step 22 的三色测试帧**：`redwood_adreno.c` step 22 "have it fill three bands of colour"（R27 起），flip 1 latched 后 GPU 三色帧是面板上最后一幅成功呈现的图像；SpringBoard 十次重生全部死于接管面板之前，CoreAnimation 的 MTLCompilerService PETRIFIED——真实 UI 从未上屏。**这是 R41 白苹果（内核活/显示活/lsd 卡死）之后的又一格进步：GPU 已画出自己的帧，只剩用户态接管这最后一跳。**

**唯一真凶 = 用户态 SIGBUS 浪潮（单点）**：194 次 "exited due to SIGBUS | sent by exc handler"——lsd ×107、powerd ×22、analyticsd ×22、backboardd ×21、mobilegestalt.xpc ×12、SpringBoard ×10；redwood 信号钩捕获的 21 次 signal 10 里 **20 次同一 PC 0x1a45c313c**（dyld 共享缓存内同一指令，同一 unslid 回调链）。与 log 内 R28d 注记（"the CPU monitor's override goes to every process (the refusal was fatal to lsd)"）完全吻合：**minisandbox 的 CPU 监视器拒绝路径把每个跑过它的进程送进 Mach 异常 → exc handler → SIGBUS**。六个守护进程、一条指令、一处拒绝——R44 的靶心。

**R44 立案（sandbox 拒绝轮）**：靶 = minisandbox CPU-monitor 拒绝路径。用户侧请求同轮评估 HeliumStudio-Dev/hackios-xnu-kernel（同家族同设备，自称到桌面 39 init 全过 + GLES + WiFi WPA3；license NOASSERTION，8 提交 10-25 前后）——不整仓照搬（内核与镜像根文件系统是一对、43 轮取证设施不可弃、license 待读、README ≠ 真机证据），定向挖矿三处：①其 sandbox/CPU-monitor 处理（对照我们的 SIGBUS 凶点）；②其镜像构建的 WiFi 固件装包（MR5 便宜解）；③其 launchd/backboardd shim 差异。挖矿成果按单刀纪律入本轮门禁。

## R43→R44 之间：HeliumStudio 上游定向挖矿报告（2026-10-30，repo_helium @ raphael-bringup 5772246）

挖矿对象：HeliumStudio-Dev/hackios-xnu-kernel 的 `raphael-bringup` 分支（HEAD `5772246`，与 origin 同步；工作区仅 785 个文件的权限位翻转，0/0 行差异，可当净基线读）。三处靶点逐一定案：

### ① sandbox/CPU-monitor——R43 判词翻案（本轮最重要产出）

**上游与我方在 sandbox 层的唯一真分歧**：`xnu/files/bsd/kern/kern_minimac.c`（AMFI/Sandbox 替身，两树同源）忽略空白后 diff 只有一处——我方多出 R28d 块：**无条件放行** `PRIV_PROC_CPUMON_OVERRIDE`（return 0）；上游没有这块，该 priv 走 entitlement 表行 `{ PRIV_PROC_CPUMON_OVERRIDE, "com.apple.private.kernel.override-cpumon" }`（按签名 entitlement 决定）。**我们的 priv 层比上游更松**——"CPU-monitor 拒绝"在代码层面不可能是我们的凶点。

**R43 日志自身定案**：全 11215 行 **零 minisandbox 行**——既没有 R28d 的一次性注记 printf（"the CPU monitor's override goes to every process"），也没有任何 "privilege N refused"。R28d printf 只在**第一次**有人问 cpumon override 时打一行：它整轮缺席 = **292 秒内没有任何进程到过这个 priv 检查**。R43 判词把"minisandbox CPU-monitor 拒绝路径"立为 R44 靶心，是**数量巧合诱导的误判**（144 拒绝/11s 的 R28c 旧形 vs 194 死/292s 的新形），据此撤案。

**死亡现场重读（真实画像）**：194 次 SIGBUS 分布 = lsd ×107、powerd ×22、analyticsd ×22、backboardd ×21、mobilegestalt.xpc ×12、SpringBoard ×10（复核吻合）。redwood 信号钩捕获 21 次，**两个凶点**：
- **A 点（analyticsd 首死，1 次）**：pc unslid `0x1a4604068`，故障数据地址 `0x217a58014`——独立事件。
- **B 点（其余 20 次，全是 backboardd，每 ~10.3 s 一轮）**：pc unslid `0x1a45c313c` / lr `0x1a45c3118` / 同一条 unslid 回调链 `0x1a45c4c20 → 0x18e545dfc → 0x1884fa3d8 → 0x186e1bff8 → 0x186e7e0b4 → 0x186e51884 → 0x186e55da0 → 0x186e94c58`，故障数据地址**次次相同** `0x2681bfd34`。同一指令、同一数据页、跨进程确定复现——该数据地址是进程无关映射，最自然的解释是 **dyld 共享缓存的数据段页**（全进程同 slide 共享）。
- 投递路径分两形：`| sent by exc handler` 后缀 ×173（launchd 的异常处理者代发 SIGBUS），无后缀 ×21（backboardd 全部——直达内核信号）。
- **与 R42/R43 的机械事实拼合**：vm_fault_internal 失败出口零经过（pager 钩全静默）→ 不是页填充失败；页 fault 若失败会被定罪 → 故障页要么填充成功（指令应重试不死）、要么**根本不走 vm_fault（PTE 在场但物理侧坏，或软件合成异常）**。内核侧 `user_exception_trace.c` 的 `usertrap:` 行（异常号 + code/subcode + **共享缓存 slide** + 寄存器文本 + fp 链）本可一次定案——但 R43 GPU cmdline 沿 R39 形 verbatim，**systrace 默认 0，全程零 usertrap**。死因机制（外部 abort on resident page / EXC_RESOURCE flavor / 其他）在现有证据下不可判。
- **上游没有现成答案**：其全部文档（air.md 1589 行、RAPHAEL*.md、README×3）零 SIGBUS 史；且其 10-27 基线迁移自述"本轮未实机验收"，桌面战绩（39 init、GLES、WPA3）属 10-23 Metal 时代旧树——README ≠ 真机证据，又一例。

### ② WiFi 固件装包（MR5 的便宜解，合同已对上）

- **上游合同**（userland/build.sh:502-508）：`MPSS_FW` 目录输入 → 硬校验七件 `modem.mdt wlanmdsp.mbn nv-modemst1 nv-modemst2 nv-fsg nv-fsc firmware-5.bin` → `cp -a "$MPSS_FW"/. flashboot/fw/`；BDF 不在七件里，由 `wifi_bdf=bd_f11cn.bin` 参数指定（上游同样拒绝缺省 0xff 通配）。
- **我方合同**（redwood_wifi.c/redwood_mpss.c）：`image_file(name)` → `redwood_image_file("fw", name, …)`——**同一个 `fw/` 目录约定**，文件名逐一对应。R43 log 的 "modem/WLAN firmware is missing from the RAM image" = `image_file("modem.mdt"/"wlanmdsp.mbn")` 空手而归。
- **结论**：MR5 不需要动内核，需要的是用户侧注入工具/说明——从本机 modem 分区只读提取七件+`bd_f11cn.bin` → 注入 flashboot.apfs 的 `fw/` → 重刷。注意：这些是设备私有文件，禁止发布（上游同此纪律，且其 RMTFS 快照只写 RAM 副本）。
- **R45 调参情报**：上游已验证控制链含 PM8150L L10C 3312 mV/HPM 五路供电、AOSS QDSS 时钟投票、`wifi_step=15 wifi_lx=2 wifi_smmu=2 wifi_keep=1 wifi_wait=10 wifi_listen=15` 参数组；已知遗留 = WiFi 开关后重扫不复（"保存已有实现，不声称已修复"）。

### ③ launchd/backboardd shim

- 上游用户态 10-27 已统一进单仓（hk_* → hakios_*，与旧历史断绝）；七个注入库（inject / inject_app / inject_fairplay / inject_media / inject_sb / inject_store / inject_tweaks）+ `rc.jobs.*` 作业系统（数十个）+ `watch_for_faults()` 故障报告器（sigaction SIGSEGV/SIGBUS/SIGABRT/SIGILL/SIGTRAP → 打 unslid 现场 → SIG_DFL 重抛——**与我方 redwood: 信号钩同款**，家族代码分叉后改名）。
- 我方用户态是 R13 谱系预构建（release 链继承，仓库无 userland 源），与上游源树不可逐文件 diff，无整搬价值；真差异要等内核侧定罪后按需引入（例如上游 inject 库的 abort 观察三件套经验）。

### 许可证事实

仓库 LICENSE 按目录双许可：内核（hakios.patch + xnu/files/）= APSL 2.0 衍生作品（源码可得义务），其余另有条款；COMPLIANCE.md 是运营中的合规清单。GitHub API 的 "NOASSERTION" 是多许可叠加的显示结果，**不等于不可用，也不等于可以整仓照搬**。定向挖矿（读源码、学方案、按我方树重写）在内核侧 APSL 义务内与现有做法同构。

### R44 重锚立案：靶心从"sandbox 拒绝"改判"systrace 询凶"

单刀（全部在 `xnu/files/osfmk/arm64/user_exception_trace.c`）：**(a)** `systrace` 默认 0→1（内核侧异常分诊入口 `exception_triage_thread` 的接线自 R41 在位，boot-arg 仍可关回）；**(b)** 异常过滤加 EXC_RESOURCE / EXC_GUARD（若真是 CPU-monitor/资源类异常，code 对直接点名 flavor）；**(c)** 按进程名节流：每名首 4 次全量、此后每 16 次一次（R42 分区律：1/s 重生循环不得吃掉 logdump 分区）。marker 同长换装（MiniIOMobileFramebuffer.cpp，56 B）。预期判读：**MR1 = usertrap: 行**（异常号 + code/subcode + slide + 现场）——B 点 20 死、A 点 1 死、以及 173 个无钩进程的死全部补上内核侧现场；slide 一到手，0x2681bfd34 与 0x1a45c313c 立即可对 dyld 缓存定名。R45 按定罪结果开真修复刀。

## R44 真机判读（2026-10-30，logdump 6010 行，内核窗 ~168 s / 日志至 00:02:48）：询凶轮零响——响声的缺席本身就是定罪

**先记账**：R44 的 CI 走了两轮。run#12（40976283142）构建一次过（17,009,280 B，+80 B = 2 个净增符号：节流对 `_user_trace_budget`/`_user_trace_budget_count`、换装 `r44_systrace_convict_mark`、TUNABLES 行号漂移 29→75），死在**门禁自己的 strings 律**——三个"新串" `RL);9j%{9` / `J1a9kAa9` / `4?C!9_S!9` 全是 `__TEXT_EXEC` 里变更指令字节的巧合可打印游程（ADRP/ADD 编码夹缝，各 x1），刀无罪。**Run-12 律**：strings 律双侧掩出代码段（`__TEXT_EXEC`/`__PRELINK_TEXT`/`__PLK_TEXT_EXEC`）——机器码不是字符串；门禁所辖全部字面量（marker/pager 判词/Darwin 戳）都在数据段，N4 负例证明数据段走私照样当场击毙。离线证明 27/27 绿（含杀死 run#12 的真字对 P6b）。修复提交 f9fb21b，run#13 重派即绿（发布内核 `79853bfa`，六律全过，gained 3 = marker + 2 Darwin 戳——与离线预言逐字一致）。

**真机判读树**：
- **MR1 = 脱靶，而脱靶本身即定罪**：6010 行**零 usertrap / 零 userabort / 零 ustacks**——记录仪一行未发。但发布内核二进制法证（静态）全绿：`_user_exception_trace` 在册；`user_exception_trace_enabled` 静态初值 = **1**（`__DATA_CONST` @ 0xfffffff0071ba6f8，cmdline 无 systrace token）；`exception_triage_thread+0x28` 处**唯一一处 BL**、无分支跳过、参数 x3 = 入口 current_thread()；`exception_triage` 全身 CFG 的主路径尾调 triage_thread（`b _exception_triage_thread` @ +0x240，x3=x19）；全部活体故障 handler（`handle_user_abort`/`handle_pc_align`/`handle_sp_align`/`handle_breakpoint`/`handle_sw_step_debug`/`handle_watchpoint`/`task_exception_notify`/EXC_RESOURCE 高水位路径）**15 个调用点全部汇入 exception_triage**。设备侧 Darwin 版本行 `Fri Oct 30 00:29:28 UTC 2026` = run#13 构建物，刷的就是发布内核。**记录仪活着、开着、在唯一汇点上，而零输出** ⇒ **这 108 条死亡根本不产生内核可见的故障**。三观察器互证：pager 钩静默（vm_fault_internal 失败出口零经过）、triage 静默、零 panic（handle_user_abort 的 triage 失败尾部是 panic——没到过）。
- **死亡波的真路径 = 信号道，不是异常道**：108 死 = 97 条 `exited due to SIGBUS | sent by exc handler[自身pid]` + 11 条裸 `exited due to SIGBUS`（全 backboardd）。反向调用图定案：`_exception_triage_thread` 全内核仅 1 个调用者（`_task_deliver_crash_notification`——尸体通知路径）；而 **`_bsd_exception` 直接调 `exception_deliver`，完全绕开 triage_thread**，其调用者 = `_issignal_locked`（← `_bsd_ast`，信号 AST 主道）/ `_do_bsdexception` / `_mac_do_machexc`。**psignal → bsd_ast → issignal_locked → bsd_exception(EXC_SOFT_SIGNAL) → exception_deliver → launchd 的 sigexc 端口 = "sent by exc handler"**——这条道天然零 usertrap。R41 起的接线前提（"每次死亡必经 exception_triage_thread"）从未被真机验证过，本轮以设备证据正式退役：**内核是旁观者，不是凶器，也不是目击者——它在信号道的上游。**
- **MR2 = 不可判**（零行输出，节流器未获触发机会）。**MR3 达成**：零 panic、零 "Lock already held"，内核全程存活（flush 至 #62 / 643 KB）。**MR4 达成**：零 "frame dropped"，GL 栈活着，三色帧维持（SpringBoard 依旧未接管——与 R43 同格）。**MR5 不变**：WiFi 固件仍缺（预期）。
- **backboardd 死亡阶段漂移（重要新事实）**：R43 的 21 次捕获 = 1 次 A 点（input 重试 11 次后死，当时误归 analyticsd——**本轮翻案：catch 上文是 backboardd 自己的 `redwood: input: the event server has not opened "Redwood buttons"` 重试行，紧随其后的 analyticsd 退出行是另一进程**）+ 20 次 B 点（IOMFB/WindowServer 阶段，unslid pc 0x1a45c313c，107 行 IOMobileFramebufferCreateDisplayList）；**R44 的 11 次捕获全部在 HID-open 即死**（unslid pc 0x1a4604068 / lr 0x1a4604064 / 同一 unslid 链 0x1a46041cc→0x1a4605fd0→0x1a4605784→0x1a45dcd7c→0x1a45dcad8→0x1a45fe0bc→0x1a45c6a84，数据页 0x1ccac8014 每次随 slide 换），**零 IOMFB 行、零 "Redwood buttons" 尝试**（R43 有 10 次）——死在 HID 系统打开的更早一步。门禁六律证明内核行为逐字节等价（记录仪文件除外），⇒ 这是**用户态失败顺序的 boot 赛跑**，非内核回归。symtab 定位：两凶点同属一镜像（base unslid 0x1a45bf000，`__TEXT` 0x7c89c；Apple 出厂对本地符号选择性涂黑 "<redacted>"，精确函数名待 R45 的内核侧现场补）。
- **驱动面健康度两轮持平**：buttons 驱动 step 5 在岗、SPMI 掉传输 5/6 次（同量级）、触摸 GT9886 正常（24 行）、adreno step 22 三色帧照画。

**R45 立案（sigpost 询凶轮——把记录仪搬到死亡波的真实 funnel 上）**：单刀 = 在 `bsd_exception()`（osfmk/kern/exception.c，三个活调用者：issignal_locked / do_bsdexception / mac_do_machexc）入口加一行节流日志（pid + 进程名 + 信号号 + 被转换的异常/code 对，沿用 R44 的按名节流律），triage_thread 的旧接线保留（无害，真 fault 时仍响）。一次 boot 即可给 97+11 条死亡逐条补上内核侧现场：**谁死、谁发的信号、什么 code**。用户态失败点（HID-open 为何秒败、"Redwood buttons" 服务为何零尝试、buttons SPMI 掉传输是否升级）是 R46 的线索，R45 的 sigpost 行负责把它们和死亡时刻对齐。门禁与工具按惯例重锚（base = R44 发布内核 79853bfa）。

## R45 立档（2026-10-30，sigpost 询凶轮构建 + 派发）

单刀落地（两文件）：`xnu/files/osfmk/arm64/user_exception_trace.c` 新增 **`sigpost_trace()`**——每次信号投递一条节流行 `sigpost: <name>[pid] exception N code 0x.. 0x..`（对浪潮即 exception 3 EXC_SOFTWARE / code 0x10003 EXC_SOFT_SIGNAL / 子码 = 信号号；SIGBUS = 10 = 0xa），复用 R44 预算（每名首 4 全量、后每 16 次）与 systrace 开关；`xnu/redwood.patch` 在 **`bsd_exception()` 入口**（exception.c，先于 `exception_deliver`，三个活 caller 全收）挂线，triage 旧线保留。marker 同长换装（`r45_sigpost_mark`，56 B）。**sigpost printf 格式串 = 本轮唯一新增数据串**，lineage 律入册（x1 in r45 / x0 in base——记录仪装进二进制的证明，防 R44 式"零响"重演时无法分辨"没装"还是"没路过"）。

工具三件重锚（base = R44 发布内核 79853bfa）：`r45_kernel_gate.py`（六律 + sigpost 律 + strings 律白名单 `sigpost:` 前缀——R42 年代 verdict-format 白名单先例）；`r45-kernel-id.py`（KNOWN += R44 = 79853bfa；新内核分支断言 R45 marker + sigpost 格式 + R44/R43/R42/R41/R40 零残留）；`r45_flash_build.py`（底板 = r44 发布：daily b0f9f6df / GPU edac8a72 / 内核条目 79853bfa）。springboard.yml 24 输入 repin：底板 URL 指向 r44-springboard、GPU 底板改判 R44（base_id_gates）、id_gates 判 R45、判读树 SP1–SP5 预注册进 README/release。

离线证明 **19/19 GREEN + 6 负例死亡**（`scripts/r45_univ_round_proof.py`）：真 r44 发布字节对判读 + 两 gates 块真实 bash 执行（40838276403 类死刑）；合成 r45 内核 = 真 r44 内核 delta-0 上换装 marker + 覆写一条 Darwin 版本串为 sigpost 格式（零填充到游程尾）——六律 GATE GREEN（含 sigpost 律双侧）；打包自证 + 判读词 x7 + "flash build's own bytes"；负例 N1 真 base 死于退役律 / N2 无 marker 死 / N3 flash 拒 base / N4 拒逐字节对 / N5 **sigpost-x0 合成体死于 sigpost 律（律有齿）** / N6 双 marker 拒收；K1 刀源断言（decl+def、预算+TUNABLE 复用、格式串、TAB 纪律、patch 三 hunk）+ K2 **全补丁对钉死 BASE c54f35ca `git apply` 回放绿且调用落位实证**（sigpost 行在 bsd_exception 体内、exception_deliver 之前）。生成器两 bug 被电池当场击毙（CLI 占位链换名反转、re-prove 行 R43_LOADER_MARK 漏改）——电池先于 CI 死亡的价值再次入账。

## R45 真机判读 + R46 立档（2026-10-30，sigbirth 询凶轮③：记录仪上移到出生点）

**R45 真机入账（6354 行）——白苹果是突破不是退步**：flip 1 = adreno step 22 三色测试帧（R43/R44 同）；**flip 2/3 = SpringBoard/CoreAnimation 开天辟地第一次真翻页**（`redwood-gl: -attachImage: IOSurface 4 ('BGRA' 1125 x 2436)` 整屏面上传 → GL 绘制 → `render_update` → `raphael-display: flip 2/3 latched`）——iOS 开机白苹果画面是 SpringBoard 自己画上去的，随后管线被浪潮杀死，面板冻在白苹果；backboardd[9] 本 boot 活到 `startup complete. 7.99545s`（R44 是 HID-open 当口秒死）。**SP1 零响但浪潮更大**：144 死 = lsd×108 + analyticsd×28 + backboardd×8（R44 是 runningboardd×83 + powerd×11 + backboardd×11 + containermanagerd×3——每 boot 换一批受害者，用户态次序竞态第三次确认）。**刀上膛验证**：发布内核 d38974bf `_bsd_exception` 入口恰一条 `BL _sigpost_trace`（全内核唯一调用点），`_user_exception_trace_enabled` 静态初值 = 1——记录仪活、开、上膛、零响 = **144 死无一穿过 bsd_exception，铁案**；R44 记录仪复核（user_exception_trace 六类异常全收、TUNABLE 默认开、预算足）——R44 静默同样是"真没故障过 triage"。**全谱反解**：38 psignal + 6 psignal_with_reason + 3 threadsignal 出生点的信号寄存器逐点解出——没有任何一点硬编码发 SIGBUS(0xa)（政策杀全是 9 号，其余 SIGXFSZ/SIGPIPE/SIGHUP/SIGALRM/SIGCHLD…）。

**R46 定案（源码级，本轮收获）**：(1) `proc_terminate()` 只发 `memorystatus_on_terminate(p)` 的 SIGKILL/SIGTERM——R45 "用户态经 proc_info 传 0xa" 假说**源头即死**（8796 无 PROC_INFO_CALL_SIGNAL）；(2) `threadsignal` 全树 3 个调用点中 **`bsd/uxkern/ux_exception.c:180` = `_handle_ux_exception → threadsignal(thread, ux_signal, code, TRUE)`**——正是 R45 验尸 binary 里 `_handle_ux_exception+0x10c` 那条；`ux_exception()` 把 **EXC_BAD_ACCESS（非 KERN_INVALID_ADDRESS，即 KERN_PROTECTION_FAILURE 类）→ SIGBUS**；(3) 该通路同时解释两轮静默：ux_handler 是**任务自己的异常处理器线程**（logdump 话术 "exc handler[own pid]" 字面成立），异常经 exception_deliver 直达 ux 端口（不碰 exception_triage），threadsignal 不走 psignal、set_exitreason=TRUE 不产生 bsd_exception(EXC_SOFT_SIGNAL) 投递。**R46 一刀（两文件）**：`user_exception_trace.c` 新增 **`sigbirth_trace(p, thread, signum, code, from)`**——每信号出生一条节流行 `sigbirth: <name>[pid] signal N code 0x.. from 0x<零滑动 LR> by <name2>[pid2]`；全新 **(名字， 信号) 键控预算表**（24 槽零初始化 → __bss 零文件增长；每键首 4 全量、后每 16 次——SIGCHLD 风暴不得吃浪潮行）；复用 systrace 开关。`redwood.patch` 四钩全落 `kern_sig.c`：**psignal**（kill(2)/资源政策/proc_terminate 自己的判决）/ **psignal_with_reason** / **psignal_uthread**（pthread_kill 唯一用户直投口）/ **threadsignal**（ux_handler 之口）；`psignal_locked/try_thread/vfork/thread_with_reason` 不挂（信号号编译期定死：SIGILL/SIGKILL/SIGTRAP/SIGVTALRM/SIGPROF/SIGXCPU，无一可载浪潮）；proc_terminate 经自身 psignal 调用自动覆盖（出生点读 `_proc_terminate`，by 字段点名请求者）。marker 同长换装（`r46_sigbirth_mark`，56 B）。R44/R45 旧线全保留（sigpost 格式 x1 双侧在册）。

工具三件重锚（base = R45 发布内核 d38974bf）：`r46_kernel_gate.py`（六律 + sigbirth 律 x1 新/x0 base + sigpost 律 x1 双侧 + strings 律白名单 `sigbirth:` 前缀）；`r46-kernel-id.py`（KNOWN += R45 = d38974bf；新内核分支断言 R46 marker + sigbirth 格式 + sigpost 格式 + R45..R40 零残留；volume/loader 谱系延展 R39..R46）；`r46_flash_build.py`（底板 = r45 发布：daily b0f9f6df / GPU aab351e3 / 内核条目 d38974bf）。springboard.yml 24 输入 repin：底板 URL 指向 r45-springboard、GPU 底板改判 R45（base_id_gates）、id_gates 判 R46、判读树 **SB1–SB5 + 零响分叉（R47 = exit reason 记录仪）** 预注册进 README/release。

## R46-fix 立档（2026-10-30，run 41023792452 验尸：一行声明的锻造事故）

**run#15 验尸（构建死亡，非门禁死亡）**：前六步全绿（磁盘 / 取树+底板三件 SHA 硬校验 / theos / xnu-deps / ccache，base_id_gates 也过——底板链完好），step[7] 内核构建死于 clang 两错：`kern_sig.c:1795: call to undeclared function 'sigbirth_trace'` + `:2577: conflicting types`。根因 = **声明位置事故**：四钩中 threadsignal 挂钩（补丁后 ~1795 行）排在文件域 extern 声明（~2577 行）**之前**——C99 隐式声明在 xnu 是硬错误，裸调被记作 `int sigbirth_trace()` 随后与正式声明冲突，kern_sig.o 处决 → make 级联 Error 2 → "内核没有产出"。门禁六律未上庭（死在产出之前）。R45 同型布局侥幸的原因：sigpost 挂 exception.c、extern 进文件头 R41 block；R46 四钩全进 kern_sig.c 才踩雷。

**修复（最小刀，内容零改动）**：按家法（`__pthread_kill` 的 `user_abort_trace` 块域 extern 先例）在 **threadsignal 体内、调用之前**加两行 extern + 空行；文件域 extern 原位保留（覆盖 psignal / psignal_with_reason / psignal_uthread 三钩）。kern_sig.c patch 段**整体重生成**（真 `git diff` 出 hunk，头与 index 线由构造精确），其余 73 段逐字节未动；新鲜 PRISTINE c54f35ca 回放 = 编辑后 kern_sig.c **逐字节相等**（92,983 B）；sigbirth_trace( x6（4 调用 + 2 声明）、abort wire x1、首声明 @1795 < 首调用 @1798。

**电池新律法**：K2 升格 **decl-before-call 法**——patched kern_sig.c 里 sigbirth 的第一条 extern 必须先于第一个调用点（run-41023792452 教训入册：未声明调用是 clang 硬错误，电池必须文本级可断言；K2 计数 5→6）。离线证明 **13/13 GREEN**（真 r45 发布字节对 + 合成 r46 delta-0 + 六律 GATE GREEN 含 sigbirth x1/x0 与 sigpost x1/x1 + flash 打包自证 + 负例 x3 + 刀断言 + K2 新法）。marker / 格式串 / 预算表 / 工具三件 / 门禁 / springboard.yml 全部原样——纯位置修复，empty-inputs dispatch 即 R46 重锻造。

## R46 真机判读 + R47 立档（2026-10-30，flush 必达轮：四记录仪补上 R42 verdict 的退场动作）

**R46 真机入账（4469 行，用户回传 r46-gpu-logdump-own.txt）——真机确实在跑 R46，浪潮满强度，记录仪行全军覆没于通道**：

- **真机 = R46 释出内核，铁证**：loader 上报 `payload entry 'kernel' size 1038b00` = 0x1038b00 = **17,009,408 B = R46 收割值逐字节**；R45/R44 的 logdump loader size 分别为 0x1038a98/0x1038a80 = 各轮收割值——五轮刷机链无一错刷。
- **波形 = R46 主嫌预测精确复现**：launchd 流满强度记录 lsd×63 连崩 + analyticsd×8（R45 是 lsd×108 + analyticsd×28——每 boot 换受害规模），**每一条死因都是 `exited due to SIGBUS | sent by exc handler[own pid]`**——死者自己的 Mach 异常处理器线程发放 = ux_handler 出生（R46 立案原文 "exc handler[own pid] is literal -- the handler IS in the dying process"）。SB3 panic 护栏 PASS（全 logdump 零 panic/零 Lock already held）；白苹果维持（`redwood: SpringBoard: activation answered here`——R45 的 flip 2/3 突破态依旧）。
- **但 logdump 无一行 sigbirth。判读定案：通道死亡，非记录仪死亡**——三条铁证：(1) **memorystatus 对照**：R46 logdump memorystatus ×0，R42 logdump ×182——memorystatus printf 四轮从未改动，R46 真机必打而不见 = logdump 的内核消息通道整体缺失；(2) **快照段解剖**：KERNEL LOG 段 690 行中 666 行是 adreno 轮询的传感器/时钟行（raphael_adreno.c 131 行注释自供 "every line said here goes there"——~8 行/秒重写环形 message buffer，77 秒 × 8.6 ≈ 666 ✓ 快照段 = 环的当前尾），记录仪行出生即被覆盖；(3) **R42 verdict 幸存的唯一原因**：redwood_fault_convict.c 打完判词即调 **`loader_log_flush()`**（注释原文 "the flush asks the loader to write the log out"）——flush 过的行进 logdump 分区全量保留（R42 logdump 的 2020 行带时间戳段 = flush 的战果，verdict ×6 + memorystatus ×182 + adreno ×1045 全在里面）。**R45/R46 的记录仪只 printf 不 flush：刀一直在挥，行全部死在了 buffer 里**。R41-R46 五轮"记录仪零响"之谜统一闭环（R41-R43 是开关默认关，R44-R46 是通道死——两种死法，一个教训：没有 flush 的 printf 在 bring-up 期等于没打）。
- **SB 判读收束**：SB1 不可开庭也不可洗白（sigbirth 行未到手，from/by 字段未知）；SB3/SB5 PASS；预注册分叉的"exit(reason) 自戕腿"**前提被 memorystatus 对照推翻**——零响 ≠ 没打，禁止盲走。

**R47 一刀（最小刀，单文件 + 标记）**：`user_exception_trace.c` 四记录仪（user_exception_trace/sigpost_trace/sigbirth_trace/user_abort_trace）**行尾各补一行 `(void) loader_log_flush();`**（R42 verdict 真机先例：78 次 flush 跑过 R42 的 boot 无恙，分区律完好；节流预算限频 = flush 频率 = 打行频率）；文件头 extern + R47 立法注释（"THE FLUSH LAW"，把通道死亡的三条铁证刻进源码）。marker 同长换装 56 B：`R47: the flush rule lands every born line in the logdump`（`r47_flush_mark`）。R44/R45/R46 旧线全保留（sigpost/sigbirth 格式 x1 双侧在册）。**零 cmdline 改动**（记录仪与 flush 默认开，无需 boot-arg）。

工具三件重锚（base = R46 发布内核 be36b679）：`r47_kernel_gate.py`（六律重锚 + sigbirth 律改双侧 x1/x1 + **新 flush 通道律：两侧 strtab 必含 loader_log_flush 符号**（R46 base 已有 pager/adreno 调用者）+ strings 律白名单收窄至 R47 marker）；`r47-kernel-id.py`（KNOWN += R46 = be36b679"the R47 gate BASE"；新内核分支断言 R47 marker + sigbirth/sigpost 格式双侧 + R46..R40 零残留；卷/loader 谱系延展 R39..R47）；`r47_flash_build.py`（底板 = r46 发布：daily b0f9f6df / GPU a2bc0222 / 内核条目 be36b679 + flush 符号断言）。springboard.yml 24 输入 repin：底板 URL 指向 r46-springboard、GPU 底板改判 R46、id_gates 判 R47、判读树 **SB1'（数据必达版：flush 后 logdump 必有 sigbirth 行；仍零行 = 记录仪真没跑 = exit(reason) 自戕腿重启）–SB5** 预注册进 README/release。

离线证明 **24/24 GREEN**（`scripts/r47_univ_round_proof.py`）：P1 YAML 九断言（SHA 四钉 + 判词 grep 对齐 + retired 集完整）+ P2 源码五断言（四 flush 落位 [156/197/310/353]、extern 先于使用、R47 立法注释、marker 56B 换装）+ P3 patch 三断言（74 段、sigbirth x6、decl-before-call 律续行）+ P4 flush 普查 x10（记录仪 x4 + pager 判词 x4 + adreno x2）+ P5 工具链对真 R46 释出 GPU 镜像的正负判定（kernel-id 判 R46 = gate BASE；flash 拒 R46 内核死于 R47 marker 断言；gate 拒逐字节对死于 byte-identical 律）。

## R47-fix 立档（2026-10-30，run 41040910832 验尸：Release 标题的 256 字铁顶）

**run 验尸（发布死亡，非构建死亡）**：前五步全绿（磁盘 / 取树+底板三件 SHA 硬校验+底板判读 / theos+SDK / xnu-deps / ccache），step③ 内核构建过关（KSHA 3f225c40… 入 GITHUB_ENV），④打镜像、⑤复证门禁全过，⑥ SHA256SUMS/README/8 资产预检全就位——死在 **⑥b 发布步：softprops/action-gh-release 422 "Validation Failed: field:name, name is too long (maximum is 256 characters)"**。根因 = R47 把整段 496 字符判读叙事塞进了 `release_name` dispatch 默认值（R41-R46 标题都短，本轮首破 256 字铁顶；叙事本已在 `release_body` 全量承载，name 里那份纯属冗余）。构建产物完整但 release 创建被 API 拒绝，tag 未落，无资产可收割。

**修复（最小刀，叙事零丢失）**：`release_name` 默认值 496→**139 字符**（"R47 · 纯血 hackios SpringBoard · flush 必达轮（R46 判读：通道死亡非记录仪死亡，四记录仪行尾补 loader_log_flush()；内核 = R46 + 一文件，cmdline 仍 R39 形 verbatim；判决树 SB1'–SB5）"），教训注释两行刻进 yml（"判读叙事全部写进 release_body（无此上限），release_name 只留一行标题"）。release_body / 判读树 / 工具三件 / 底板钉 / marker / 源码 / patch **全部原样**——纯标题修复，empty-inputs dispatch 即 R47 重发布。

**电池新律法：release title ≤ 256**：P1 新增 `P1 release title fits the GitHub 256-char cap`（len 文本级断言，run 41040910832 教训入册——R46-fix 的 decl-before-call 同款先例：CI 死过一次的东西必须进电池）。离线证明 **25/25 GREEN**（原 24 项全绿 + 新律法亲测 len=139）。

**重派发**：commit 54d9626 push main（0aa6480..54d9626）→ empty-inputs dispatch → **run 41043191263**（54d96263）in_progress。 盯守协议照旧：in_progress 回一行；SUCCESS → 收割协议（四资产 + r47-kernel-id 判读 + GATE GREEN + kernel-sha 对 run 产出）；FAILURE → 验尸协议。

## R47 真机判读 + R48 立档（2026-10-30，census 实证轮：门禁本身被捕）

**R47 真机入账（logdump 11190 行 / 1.3 MB，SSH 通道自用户 NAS `/vol1/1000/ios/logs/` 直拉）——刷新纪录的五连**：

- **真机 = R47 释出内核，铁证（刷机链第五次全对）**：loader 上报 `payload entry 'kernel' size 1038af8` = 0x1038af8 = **17,009,400 B = R47 收割值逐字节**；用户机上 `raphael-xnu-r47-gpu-flash.img` sha256 = 64e39867（= 释出 GPU 钉）；解包 kernel 条目 sha256 = 9d589bad（= 释出内核钉）。
- **R47 flush 律法真机生效**：loader-log 记录 13 次写盘（#1–#13），其中 **#4–#13 = 十次 "the kernel asked for a flush"**（R47 的新通道），每次全量 read back 一致；kernel 终局交出 log at f7c049c8, 139978 B → 159,744 B 落盘。通道问题就此了结：**活着，且十次 kernel-asked flush 证明内核侧主动用过它**。
- **挂钩在膛（对刷入字节做 BL 解剖，新律法工具 scripts/r47_bl_audit.py）**：`_psignal+0x24 → _sigbirth_trace`、`_psignal_uthread+0x28`、`_psignal_with_reason+0x2c`、`_threadsignal+0x5c`、`_bsd_exception+0x34 → _sigpost_trace`、`_exception_triage_thread+0x28 → _user_exception_trace`、`_vm_fault_internal → _redwood_fault_convict` 七处逐点验证；全部记录仪字面量在二进制（sigbirth x1 / sigpost x1 / usertrap x3 / userabort x1 / pager verdict x6 / ustacks x3）；总开关 `user_exception_trace_enabled` 二进制初值 = **1**（__DATA_CONST @ 0xfffffff0071ba6f8，cmdline 无 systrace token）。
- **然而十三个快照 1.3 MB 全文检索：sigbirth / sigpost / usertrap / userabort / redwood-pager verdict / ustacks / top 全部零行**——连 boot 期的 (launchd, SIGCHLD) 出生都零行（datamigrator 00:01:42 exit(0)，kern_exit.c:2564 的 psignal 无条件）。浪潮满强度：lsd×108 / analyticsd×28 / powerd×22 / backboardd×21 / SpringBoard×10 / xpc×12，全部 "exited due to SIGBUS | sent by exc handler[own pid]"；白苹果与 GL 三色帧维持（frame 2-4 渲染全绿后 00:02:05 浪潮爆发）；SB3 panic 零；日志终局 = 用户按 power+vol-down（手机冻死）。
- **静态推演穷尽**：钩子在膛 + 开关 = 1 + 预算 24 槽足额（(launchd, SIGCHLD) 是新键，首 4 必打）+ SIGCHLD 走的就是挂钩的 psignal + printf 通道同源（buttons/adreno 同期正常落盘）+ 记录仪若真打行，其 flush 会在 loader-log 留名且 1.5s 自旋会拖死 boot（boot 流畅跑完 5 分钟）——**五个前提全真而结论为零 = "记录仪 printf 从未执行"成为唯一幸存解释，但无法用静态分析再推进一步。R46 的"通道死亡"判读被本轮证据修正：通道活着，死的是别的。**

**R48 一刀（census 实证轮，两文件 + 一补丁段 + 标记）**：既然"钩子在膛却零响"，唯一出路是让内核自报运行时真相——① `user_exception_trace.c` 新增 **r48_\* 十个计数器**（sigbirth/usertrap/sigpost/userabort 的 seen/printed + switch/budget 拒收数），**在每个漏斗入口、一切 gate 之前自增**；② **siglive: 活体探针**——头四个信号出生无条件打印（不 flush：早 boot 环境安静，2-second notes 携带）；③ `redwood.patch` 新增 **sleh.c 段**（真 git diff 生成，pristine 回放逐字节验证）：`handle_user_abort` 入口 `redwood_fault_seen()`——物理地板，任何用户态 fault 逃不掉；④ `raphael_adreno.c` minute 通道新增 **sigcensus: 唱票**——每分钟把全部计数器打上 proven 通道并 flush。marker 同长换装 56 B：`R48: the census counts every hand at work, live, no gate`。

**判读树 CR1–CR5（预注册）**：CR1 births>0 且 printed=0 且 siglive 缺席 = 钩子跑了、行死在 printf/flush；CR2 births=0 = psignal 真没跑；CR3 faults 随浪潮上涨 = 硬件 fault 存在、triage 被绕过；CR4 faults=0 且 births=0 = SIGBUS 内核全不知情（死亡理由来自用户态）；CR5 sigbirth 行出现 = 四轮静默是假象，直接定罪。SB2/SB3/SB4/SB5 全部沿袭。

**工具三件重锚（base = R47 发布内核 9d589bad）**：`r48_kernel_gate.py`（六律：marker 56B 同长双侧、R40..R47 退役、pager x6 双侧、sigpost/sigbirth x1 双侧、**siglive/sigcensus x1 新 x0 base**、strings 律放行 census 格式）；`r48-kernel-id.py`（KNOWN += R47 = 9d589bad"the R48 gate BASE"+R47 判定分支；新内核分支断言 R48 marker + siglive/sigcensus x1 + redwood_fault_seen 符号 + R47..R40 零残留）；`r48_flash_build.py`（底板 = r47 发布：daily b0f9f6df / GPU 64e39867 / 内核条目 9d589bad）。springboard.yml 24 输入 repin：底板 URL 指向 r47-springboard、GPU 底板判 R47、id_gates 判 R48、判读树 CR1–CR5 预注册进 README/release。

**离线证明 57/57 GREEN**（scripts/r48_univ_round_proof.py）：P1 yml 24 输入 + 五 SHA 钉 + release_name 189 B ≤ 256 + 判词 grep 对齐工具真实词表 + R40..R47 退役集完整；P2 刀源断言（探针先于 gate、拒收计数、census 骑 proven 通道并 flush、56B 同长、C99 声明序）；P3 75 段 + **真 pristine c54f35ca 上 git apply 回放绿**（sleh 段逐字节）；P4 flush 普查（记录仪 x4 + convict x4 + adreno x2 + census x1 = 11，探针不 flush 入册）；P5 工具对真 R47 释出 GPU 镜像正负判定（判 R47 = gate BASE；gate 拒逐字节对死于 byte-identical 律）；P6 打包自证（合成 r48 内核进真 r47 GPU 底板 → 重解包逐字节对 → id 判 R48）。

**SSH 遥控通道同轮立档**：端点 ipv4.mysv.dpdns.org:222（DDNS 漂移曾致误判 CGNAT——DoH 干净解析 223.122.32.94 后 0.01s 通，中国移动香港段，用户判断正确）；scripts/raphael_ssh.py（run/get/put，写端强制 /vol1/1000/ios）；本轮 logdump 四件与刷入镜像全部经此通道取证。新律法候选：**远程取证轮的 logdump 应第一时间拉回本地再判读**（本 round 的快照结构解剖与 BL 审计全部依赖本地字节）。
