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
