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
