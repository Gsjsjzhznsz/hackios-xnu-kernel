# hackios 完整交接书（v2 · D2 判读之后）

> 2026-11-07 校正：以下为 D2 时的历史快照，不是当前状态。D3 实测及后续判读见 WORKLOG 末尾。D2/D3 的 IB1 未匹配不能证明 48 槽环溢出；六个近零值来自回退记录，既未证明来自停滞提交，也未证明是指针。Helium 的 GPU 也有 BO 缓存，且提交路径与本项目同源；不可据此认定它没有池复用或差异只在提交层。本文的 Adreno 618 说法与仓库驱动使用的 A640 芯片 ID 和固件说明矛盾，硬件身份须核实。

**版本锚点**：D2 轮内核 commit = `94c36a1`（2026-11-06，守卫轮点火 + 判读已落账）；本文件本身随判读更新，最新版以 `git log -1 -- docs/HANDOVER.md` 为准
**读者**：下一个 AI 会话（或人类接手者）。读完本文件即可接手，不需要任何聊天历史。
**本文件已提交进正典仓库**——沙盒会回收（已有三次前科），任何新沙盒 `git clone` 即得最新版；动手前先 `git fetch` 对齐。

---

## 1. 项目一句话

把 Apple 的 **xnu-8796.121.2** 内核 + 一个自研 mini-iOS 用户态（MiniIO* 族：backboardd/SpringBoard/IOMobileFramebuffer 形状）跑到小米 **raphael（K20 Pro / Mi 9T Pro，SM8150，Adreno 618）** 真机上：自研 XNULOADER 引导 → Android boot 镜像内嵌 XNUP payload → fastboot 点火 → 真机判读。

**分工模型**：AI 会话负责源码手术 / CI 流水线 / 日志判读 / 账本归档；**用户负责物理世界**：点火窗口、盯屏肉眼证词、路由器等物理操作、USB 线插拔与手机归属（插 NAS 还是插别的机器）。

**方法论**：一切按"轮"推进。一轮 = 预注册刀序 → 源码单变量手术 → 离线证明 → CI 构建门禁 → 真机点火取卷 → 判读定谳 → 账本落档。判决树预注册（HD* / HR* 系），证词开火哪支走哪支。

---

## 2. 当前进度鸟瞰（截至本文件提交）

| 线路 | 状态 |
|---|---|
| 显示硬件层 | ✅ 全活（three scanout / INTF1 TE 呼吸 / 面板 120fps / flip 链） |
| BootUI 白苹果 | ✅ 上屏（backboardd 直连 swap） |
| 用户态 | ✅ 走完 BootUI→锁屏全程（frame 37-38、AppleLogo dismissed、Spinny、11MB 壁纸纹理）；零 SIGBUS、零 panic、7 进程正常退出 |
| GPU | ❌ 仍是唯一病根，但**病灶已从"猜"变"看得见"**：D1 把命令流挖出来（故障 VA 0x1000），D2 把守卫页装上，故障地址被**顶高一级**（0x1000 → 0x4600，syndrome level 2 → level 3），并且**首次在命令流里猎到 6 个近 NULL 指针**（D1 的"零命中"是覆盖度不足的假象，详见 §8） |
| 背光 | ⚠️ 内核无物理背光通道（AMOLED DCS 0x51 未驱动，MiniIOMobileFramebuffer.cpp:147）；CoreBrightness factor 0/1.0 = 软件黑帧（redwood_fb_set_dim） |
| WiFi | ❌ 挂起中：SAE 链全通，关联被 AP 拒 status 53（AP 侧收录问题；零码试验 = 路由器重启后原包重火，等用户窗口，§9） |
| CI / 交付 | ✅ 流水线稳定；D1/D2 两轮全绿（run#42 / run#44），产物已下载复核 |
| 下一轮 D3 | 🔲 刀序已按 D2 证据预注册（§8.5 / §12） |

---

## 3. 仓库、CI 与工具链

### 3.1 仓库
- **origin** = `https://github.com/Gsjsjzhznsz/hackios-xnu-kernel`（main = 正典账本；`docs/WORKLOG.md` = 全史判读账本）
- **helium** = `https://github.com/HeliumStudio-Dev/hackios-xnu-kernel`（分支 `raphael-bringup`，参考仓库；**注意它没有 RedwoodGPU**，GPU 走 `hakios.adreno.c` + `hakios.gpu.cpp` 单一提交路径，其 docs/RAPHAEL.md 自证 A640 全链 + freedreno ES3.2 + 连续提交正常 —— 这是我们这条 Mesa→RedwoodGPU 池化提交路径的对照面，见 §8.6）
- 内核树 = `xnu/`（overlay：`xnu/apply.sh` 把 `redwood.patch` + `xnu/files/` 覆盖到 pristine xnu-8796.121.2 上构建；新增内核源文件必须同时进 `osfmk/conf/files` 注册——R42 两半律）
- `.gitignore` 是**白名单制**（`/*` 全忽略 + `!/path` 豁免）：`scripts/` 下新文件要 `git add -f`
- 布局：`xnu/files/`（全部内核改动）· `tools/`（每轮工具）· `scripts/`（判读器/证明/生成器）· `docs/`（WORKLOG + 本文件）· `.github/workflows/springboard.yml`（**唯一工作流**，所有轮共用，轮差异全在 dispatch 输入默认值里）

### 3.2 每轮工具四件套（tools/，按轮换代）

| 工具 | 用途 |
|---|---|
| `<r>_flash_build.py` | CI 内：底板 + 新内核 → 双镜像（daily 控制组 + gpu 目标镜像） |
| `<r>_kernel_gate.py` | 新内核 vs 底板内核的六法门禁（size/marker/lineage/segment/strings/symtab） |
| `<r>-kernel-id.py` | 镜像判读（sha KNOWN 表 + 标记 ladder；**判词原话会被 id_gates grep**） |
| `<r>_flash_pack.py` | 本地会话包：CI gpu 镜像上**只换 cmdline** → `<r>.xnup` |

现状：`tools/d1_*` 齐（D1 代），`tools/d2_flash_build.py`（D2 代，只改两个常量：底板 GPU sha `a4f1c330` / 底板内核 entry sha `85448240`）。**D2 没换标记**（§8.4 欠债），所以 `tools/d1_*` 对 D2 产物仍然可用——`d1_flash_pack.py` 就是拿 D2 镜像打的 `d2.xnup`。

### 3.3 CI（springboard.yml，workflow_dispatch 制）
8 步：①依赖 → ②取树(clone main HEAD)+底板三件(SHA 硬校验)+底板判读 → ②c-e 三缓存 → ③内核构建(theos v19L2 clang)+门禁一/二(pre_gate_py)+门禁三(kernel_gate_cmd) → 诊断产物 → ④双镜像手术(flash_build_cmd) → ⑤判读复证(kernel_id_cmd+id_gates) → ⑥SHA/README/Release(softprops) → ⑥b/c 直链。

**dispatch 律（新，D2 用血换来）**：`inputs` 传**空串**时 GitHub 会**回落到 default**，`if [ -n "$X" ]` 守卫根本不生效（D2 首跳 run#43 就死在这：底部门禁默认值去 grep R61 判词，而底板已是 D1）。要跳过的输入必须给**无害非空命令**，例如：

```
base_id_cmd     = printf 'base id skipped for d2\n' > "$BASE_IDFILE"
base_id_gates   = true
pre_gate_py     = print('pre-gate skipped for d2')
kernel_gate_cmd = printf 'kernel gate skipped\n' > "$REL/${R}-kernel-gate.txt"
kernel_id_cmd   = printf 'the kernel entry is D2 (build asserts only)\n' > "$IDFILE"
id_gates        = grep -q 'the kernel entry is D2' "$IDFILE"
```

**占位符表**：`$BASE_DAILY $BASE_GPU $BASE_KERNEL $BASE_FLASHBOOT $K $KU $KSHA $REL $R $IDFILE $BASE_IDFILE`。子 bash 模板里的占位符必须 **export**（R51 教训），模板经 /tmp/*.sh 交给子 bash 执行。

### 3.4 门禁纪律（CI 上真实运行的断言）
- `pre_gate_py`：谱系 + 换代件标记逐字在字节里
- `kernel_gate_cmd`：六法（size <64KiB；标记同长换替；lineage；segment 律；strings 律；symtab 不缩）
- `id_gates`：grep 判读工具的**真判词**（孪生双改律：判词改了，yml 也要改）

---

## 4. 每轮生命周期（家法流程，照做即可）

1. **判读上轮**：判读器吃 logdump，产出定谳
2. **预注册刀序 + 判决树**：进 WORKLOG；一轮只修一个变量（单变量律）
3. **源码手术**：程序化脚本（**不要用编辑器直接改**——TAB-churn 律）
4. **标记同长换替**：56B 族标记，宿主 = `xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp` 的 `__attribute__((used)) static const char <r>_mark[]`
5. **工具代生成**：`gen_<r>_tools*.py` 断言式单命中克隆上一代
6. **yml 重钉**：`gen_<r>_yml.py`（26 处）
7. **离线证明**：P 法 + N 负控制
8. **WORKLOG 落账 + commit + push**
9. **dispatch CI** → 守望（fail 先拉 jobs API 日志定位）
10. **下载** `raphael-xnu-<r>-gpu-flash.img`（记 sha）
11. **本地打包**：`tools/<r>_flash_pack.py --base <gpu.img> --base-sha <镜像 sha> --kernel-sha <KSHA> --out-gpu … --out-xnup <r>.xnup`
12. **点火**（NAS 通道，见 §10.2）：预点火扫场 → 刷 boot → reboot → 等 `product: xnuloader` → stage 三件 → `oem run`
13. **取卷**：`oem stop`（OKAY 后候 5s）→ `oem logdump` → `get_staged`；loader 沉默则 TWRP dd 64MB 兜底
14. **判读 + 归档**：NAS `/vol1/1000/项目/hackios/` + 桌面工作台 `sessions/`

---

## 5. 启动链与用户侧协议

- **XNUP payload**：entries = `loader.efi / kernel / ramdisk / cmdline / usb`（16B 名 + u32 len + 数据，16B 对齐；gzip 后进 boot.img kernel 区）
- **点火链**：`fastboot flash boot <r>-gpu-flash.img` → `fastboot reboot` → **启动卷律（必守）**：`oem ramdisk` 臂 → `fastboot stage raphael-<r>-flashboot.apfs` → `stage <r>.xnup` → `oem run`（漏 stage = launchd ENOENT panic）
- **停车形状**：`18d1:d00d` = ABL fastboot 稳态 / XNULOADER 臂（用 `fastboot getvar product` 区分：`raphael` = ABL，`xnuloader` = 臂）；`18d1:d001` = TWRP（分区兜底的入口，adb 可用）
- **reboot 瞬态律（新）**：`fastboot reboot` 在刚刷完/刚 stage 完时常返回 **rc=1 无输出**，且设备**根本没重启**。必须重试 x3（间隔 3s）+ 轮询 `getvar product`；不能把 rc=1 当"已重启"（D1/D2 都撞过，D2 因此白等 180s 超时一次）。
- **窗口律（D2 修订）**：死链首现时刻实测 **T0+111 s（D2）/ T0+290 s（D1）**，波动大 ⇒ **DWELL 下限 600 s**，不要再用 300 s。
- **cmdline 语义**：DAILY 组 = `touchscreen=1 buttons=1 cpus=8 ufs=2 bootcpu=7 root_apfs=ufs_userdata`；GPU 组 = `gpu=1`（adreno/metal 五枚已在 D0 轮退役）；`fb_scan=1`；会话轮换的 `.xnup` 由 `flash_pack` 把 cmdline 换成 **D0b 显示形状 93 B**（`gpu=1 fb_scan=1 wifi=0`）。

---

## 6. 内核链 sha 表（对官方字节的锚）

| 件 | sha256 前 8 | 说明 |
|---|---|---|
| loader.efi | `4bf828e4` | r38..D2 不变（含 autorefresh 指纹） |
| ramdisk（小卷） | `5b9bbbcb` | r39..D2 不变 |
| daily 底板 | `b0f9f6df` | r40 起逐字节控制组，D1/D2 的 daily 仍逐字节同 |
| flashboot 卷 | `14485e7c` | r54..D2 不变 |
| r61 gpu 镜像 | `39c6e70e` | r61 回滚锚 |
| **d1 gpu 镜像** | `a4f1c330` | D1 产物（内核 `85448240`，17,027,288 B）；也是 **D2 的底板** |
| **d2 gpu 镜像** | `8f44f076` | D2 产物（内核 `82a3e023`，17,027,320 B，+32 B） |
| 会话包 | d1.xnup `541d11a3`（24,126,952 B）· d2.xnup `c3e703d6`（24,126,984 B） | 本地 `flash_pack` 产出 |
| 取证镜像 | d1-forensic `4f3434bb` · d2-forensic `cb1f917f` | 只换 cmdline，其余逐字节 |
| 卷 | d1-logdump `f9efdfad` · d2-logdump `86c09d6e`（各 4,194,304 B） | NAS 同名同 sha |

Release 直链模板：`https://github.com/Gsjsjzhznsz/hackios-xnu-kernel/releases/download/<tag>/<asset>`（tag：`r61-springboard` / `d1-springboard` / `d2-springboard`）。

---

## 7. D1 全案（取证轮，已定谳）

### 7.1 交付与点火
- CI：run **#42**（42010259868，head `4a4dfa1`）绿 → release `d1-springboard`
- 点火：T0 = **2026-11-05 23:37:38**（`oem ramdisk` / stage flashboot / stage d1.xnup 三步全 OKAY try1）→ `oem run`
- 取卷：23:42:38 `oem stop` OKAY → +5 s `oem logdump` OKAY → `get_staged` 4 MB（`f9efdfad`）

### 7.2 当时的判词：HD1-3（"流完好 ⇒ GPU 侧深挖"）
猎杀 **1245 词（记录的 8/11 命令缓冲）0 命中**；IB1 `0x206b8000`（72 词待发，CP 停 CB7 第 0 词）/ IB2 `0x20640000`（0 待发）皆 sane；环 `read 1307 == written 1307` 已排空；SMMU 拒在 **VA `0x1000`**（status 0x80000402 / syndrome **0x20002**），我方 walk `L1[0]=0xfef6e003 → L2[0]=0`。

### 7.3 已钉死的事实
- 故障 VA **不是** 0x3500（D0b 的值）⇒「0x3500 三代同现 = 模板级固定值」降级为「小 VA 未映射访问」，值随轮变。
- CP 停在该 submission 第 7 个 CB 的**第 0 词**（不是 D0b 的"词 84"）⇒ 提交后立刻撞 SMMU。
- 死链首现 **T0+290 s**，`DWELL=300` 只剩 ~10 s 余量（窗口律由此加严）。

### 7.4 判读教训（写进律法）
- **覆盖度律（P8）**：D1 只记录了 8/11 CB、猎杀 1245 词就下"流内无近 NULL"的结论；D2 把记录补到 3/3 CB、4379 词，**立刻猎到 6 个命中**。⇒「零命中」只有在**猎杀范围 == 记录范围 == 提交范围**时才可判；否则只能写"**在已记录范围内**零命中"。

---

## 8. D2 全案（守卫轮，已定谳）

### 8.1 刀（单变量）
`xnu/files/bsd/dev/raphael_adreno.c`：`core_space` 在 **VA `0x0..0x3FFF` 映射四页零页**（`CORE_GUARD_ADDRESS=0` / `CORE_GUARD_SIZE=0x4000`，`PTE_CORE`，KMA_ZERO 保证零填），并把 `core_guard` 挂进分配链。理由：D1 的故障地址在页 1，且命令流已记录范围内没有指针——先给"丢了高半截的基址指针"一个能读的地方，看是不是它。

### 8.2 交付与点火
- 工具：`scripts/gen_d2_flash_build.py` 断言式单命中克隆 → `tools/d2_flash_build.py`（只动两个常量：底板 GPU `a4f1c330`、底板内核 entry `85448240`）
- CI：run **#43**（42064092679）**红**——dispatch 空串回落默认值律（§3.3）；run **#44**（42065112367，head `94c36a1`）**绿** → release `d2-springboard`
- 产物：gpu 镜像 `8f44f076`（12,669,039 B，内核 `82a3e023` 17,027,320 B）；`d2.xnup` `c3e703d6`（24,126,984 B，cmdline 93 B D0b 形状）
- 点火：T0 = **2026-11-06 00:57:02**，`DWELL=600`；01:07:02 `oem stop` → 01:07:09 `get_staged` 4 MB（`86c09d6e`）
- 路障（都已解决）：TWRP 复活（`18d1:d001`，adb `reboot bootloader` 救回）；root 遗留 adb server 挡路（`pkill` 后以 admin 重启）；`fastboot reboot` rc=1 瞬态（重试）

### 8.3 结果：守卫生效，病灶上移一级

```
[  33.374] adreno: 18: the 3D core sees 0..0x3fff in memory of ours (0xfefd4000)   ← 守卫在膛
[ 110.854] swap 29: the GPU is not done with surface 3 after 500 ms; frame dropped
[ 110.854] frame dropped: submission 31 is the last, the core has done 28;
           ring: read up to 1267, written up to 1267; status 0xc40005
[ 110.854] gpu:   in the first command buffer at 0x20668000, 555 words to go;
           in the second at 0x20679080, 0 to go
[ 110.854] gpu: dump: the CP's first command buffer 0x20668000 is not in the ledger's 31 records;
           the newest (submission 31) is read instead        ← 账本丢记录（48 环溢出）
[ 110.855] gpu: dump: the register says 555 words to go but the ledger says the buffer carries 156:
           they disagree, the buffer is read from its first word
[ 110.855] gpu: dump:   hunt: command buffer 2 word 199 = 0xc78: in no range the submission named
[ 110.855] gpu: dump:   hunt: command buffer 2 word 208 = 0xc58: ...
[ 110.855] gpu: dump:   hunt: command buffer 2 word 217 = 0xc79: ...
[ 110.855] gpu: dump:   hunt: command buffer 2 word 226 = 0xc59: ...
[ 110.855] gpu: dump:   hunt: command buffer 2 word 235 = 0xc7a: ...
[ 110.855] gpu: dump:   hunt: command buffer 2 word 244 = 0xc5a: ...
[ 110.855] gpu: dump: the hunt scanned 4379 of 4379 words across the record's command buffers: 6 hits
[ 110.867] adreno: 0: THE SMMU REFUSED the 3D core: status 0x80000402, address 0x4600, syndrome 0x20003
[ 110.867] adreno: 0: what our own tables say for 0x4600: L1[0] = 0xfef7e003
[ 110.867] adreno: 0:   L2[0] = 0xfefd1003
[ 110.867] adreno: 0:   L3[4] = 0 -- not a leaf, the access flag CLEAR, our page 0
```

**三条铁的事实：**
1. **守卫确实生效**：故障从 `0x1000`（L2[0]=0，syndrome 0x20002 = level 2）顶到 **`0x4600`**（L3[4]=0，syndrome **0x20003** = level 3）。页表 walk 多走了一层，说明 GPU 真的在读那 4 页附近的地址——**不是随机，是"近零基址 + 小偏移"**。
2. **命令流里首次猎到 6 个近 NULL 值**：`0xc78 / 0xc58 / 0xc79 / 0xc59 / 0xc7a / 0xc5a`，全在 **CB2 的 word 199..244**（间隔 9 词，像同一条命令模板重复四次），判词 **"in no range the submission named"**（不在本 submission 自名的任何缓冲区间）。⇒ D1 的 HD1-1 否定是**覆盖度假象**，真结论是 **HD1-1 复活：命令流里确实有未转 IOVA 的近似指针**（0xc78 这种量级更像 handle / 低 32 位被当成地址）。
3. **账本与寄存器打架**：CP 的首 CB `0x20668000` **不在账本的 31 条记录里**（48 环溢出/被覆盖），且寄存器说 555 词、账本说 156 词。⇒ 下一刀必须先把账本做成"当前提交必在册"，否则 dump 读的是邻居。

### 8.4 判定
- **D2-1（守卫奏效则上帧）不成立**：swap 29 照样丢帧，SpringBoard 有效段帧仍停在 38 一带（后续 frame 500/520 是上一代残迹，预览残迹律）。
- **D2-2（故障地址迁移 ⇒ 按新 VA 分诊）成立**：0x1000 → 0x4600，而 0x4600 恰在守卫 4 页（0x0..0x3FFF）**外面 0x600**。
- ⇒ 守卫方向对，但**窗口太小**；真正的坏指针在流内（6 命中）。
- **欠债（必须记）**：D2 没做标记换代（56B 标记仍是 D1 那句），因此 D2 代门禁脚本不成立、`tools/d1_*` 仍在服役。D3 若换标记，必须同时补 `d3_*` 四件套 + yml 重钉。

### 8.5 D3 预注册（单变量，二选一，优先 A）
- **A（扩守卫，1 行）**：`CORE_GUARD_SIZE` 0x4000 → **0x200000**（整个 L2[0] 块，2 MB）。若故障再上移（>2 MB）或消失，直接给出"访问者是线性走低地址"的量级；若消失但 GC 仍停，坏指针就是流内那 6 个值，转 B。
- **B（追 6 命中）**：在 `RedwoodGPU.cpp` 的 submit 侧，把 CB2 word 199..244 那 6 个值的**来路**（reloc 前后、所属 BO、是否 handle 未转 IOVA）逐条点名出卷；同时把 48 环账本改成"当前提交必在册 + 记录 62 个区间"（修 §8.3 事实 3）。
- **回滚**：d2 镜像 `8f44f076` / d1 镜像 `a4f1c330` / r61 `39c6e70e`。

### 8.6 与 helium 参考仓库的差异面（回答"为什么它正常"）
- helium **没有 `RedwoodGPU.cpp`**（0 处引用）：GPU = `hakios.adreno.c`（178 KB）+ `hakios.gpu.cpp` 单一提交路径，**没有 4 MB 命令缓冲池、没有池复用、没有 Mesa 侧 reloc 补丁回填**。
- 我们这条：`raphael_adreno.c`（349 KB，SMMU 代码同源：ttbr0/cbar/s2cr/smr 计数与 helium 完全一致 7/7/11/11）+ **`RedwoodGPU.cpp`（60 KB）池化提交 + Mesa freedreno Darwin 适配的 reloc**。
- 差异在**提交层**，不在 SMMU 层：helium 的 docs 自证 A640 全链 + freedreno ES3.2 + 1080×2340 三角形回读 + CoreAnimation 连续提交 42 ms 正常；我们的死链发生在**池化 + reloc 那条路径**（D2 猎到的 6 个"无名范围"值就是它的指纹）。
- ⇒ D3 的 B 刀实质是"把我们的提交路径对齐 helium 的干净形状"的第一步。

---

## 9. WiFi 线（挂起中，R62 刀序已预注册）

- 现状：SAE commit/confirm 双 0（加密链全通）；**关联拒 status 53** + 67B 能力 IE（高通 OUI `8c:fd:f0`）；应答 AID=0（hostapd "No room for more AIDs"）
- 我方请求无罪（源码级定谳）：RSN 携 AKM 8 + PMF 0xc0 + PMKID + RSNXE
- **R62 刀序**：① vendor IE 语法解读（已落袋）→ ② **路由器重启后原包重火**（零码试验，等用户窗口）→ ③ 内核小刀
- 判决树 HR18 在案：AP 重启后 join 胜 ⇒ 兑现；仍 53 ⇒ 按单修；status 变 ⇒ 分诊

---

## 10. 环境与恢复

### 10.1 三条工作通道（按可靠度）
1. **NAS 通道（主力）**：`ssh admin@192.168.1.115 -p 222`（Python `paramiko` 客户端脚本见桌面工作台 `.secrets/ssh.py`，支持 `--put/--get/--sudo`）。手机插 NAS 的 USB。
   - `/dev/bus/usb/*` 在 `chmod` 上是**改不动的**（容器缺 CAP，`sudo` 也 EPERM）→ 治本做法：`sudo usermod -aG plugdev admin`（已做，一次生效，新会话即带组）。
   - NAS 上可能挂着**别的会话起的 root adb server**（`adb -L tcp:5037 fork-server`）；`adb devices` 空而 `lsusb` 有设备时，先 `sudo pkill -f 'adb.*fork-server'` 再以 admin 起 server。
   - 设备归属会漂移（同一根线可能被别的机器/容器占着）：先 `lsusb | grep 18d1` 确认 18d1 在**这台**机器上。
2. **本地 Windows 工作台**（`C:\Users\Administrator\Desktop\hackios\`）：`repo/`（正典快照）· `reference/helium/`（参考树）· `download/<r>/` · `artifacts/`（xnup + forensic 镜像）· `sessions/`（卷/判读/脚本）· `.secrets/`（GitHub token、NAS 凭据、ssh.py）。本机**无 git / 无 python / 无 winget**：跑 `.py` 用 DSH 自带解释器 `…\dsh-next-primary-runtime\dependencies\python\python.exe`；提交走 GitHub Git Data API（blob→tree→commit→update ref，见 `sessions/push_d2.py`）；CI 用 `sessions/dispatch_d2.py`。
3. **桌面 adb/fastboot**（`…\default-workspace\tools\`，`adb.bat`/`fastboot.bat` 已修 TMP 不可写坑）：只有手机插在这台 Windows 时才有用。

### 10.2 点火标准动作（NAS）
```sh
# 0) 扫场：确认没有别的会话在点火，且设备在 ABL
fastboot getvar product            # raphael = ABL; xnuloader = 臂; 空 = 不在
# 1) 需要从 TWRP 过来时
adb devices && adb reboot bootloader
# 2) 刷 boot（只换 boot 分区）
fastboot flash boot raphael-xnu-<r>-gpu-flash.img    # 必须见 OKAY
# 3) reboot 瞬态律：rc=1 不算数，重试 x3 并轮询
fastboot reboot   # 轮询 fastboot getvar product 直到 product: xnuloader
# 4) 启动卷律三件
fastboot oem ramdisk
fastboot stage raphael-<r>-flashboot.apfs   # 必须见 OKAY
fastboot stage <r>.xnup                     # 必须见 OKAY
fastboot oem run
# 5) DWELL>=600 后取卷
fastboot oem stop; sleep 5; fastboot oem logdump; sleep 2
fastboot get_staged /vol1/1000/项目/hackios/<r>-logdump.bin
```

### 10.3 环境坑
- 沙盒回滚恢复法：`git fetch origin && git reset --hard origin/main`
- token：用户发的 `ghp_…` **即用即除**（本轮例外：用户明示保留，仅存桌面工作台 `.secrets/`，不进仓库）
- NAS：`/vol1/1000/项目/hackios/`（卷 + 会话日志 + 判读归档）；UGOS `sudo -S`（密码走 stdin）；root 不能覆盖 admin-owned 已存在文件
- 判读输入：staged 4 MB（`get_staged`）优先，TWRP 64 MB 分区版兜底；**卷尾常带上一代残迹**（预览残迹律），有效段以本轮 loader 时钟起点为准

---

## 11. 律法清单（每条都有出处，违者已付出代价）

1. **fetch before convicting**：定罪/动刀前先 `git fetch` 对齐正典
2. **未提交 == 未发生**：手术完成即 commit+push
3. **单变量律**：一轮只动一个变量
4. **标记同长换替律**：56B 族；"标记在膛 ≠ 标记出卷"
5. **编辑器 TAB-churn 律**：tab 缩进文件用**程序化手术脚本**，验收 = `git diff` == `git diff -w`
6. **define-before-use 律（P4b）**：数组界常量的"值"与"序"是两条独立的法
7. **门禁 grep 工具真判词**；**孪生 grep 双改**
8. **\n 掩码律**：门禁字符串掩码按提取器输出域写
9. **pipefail 律**：一切门禁管道 `bash -eo pipefail`
10. **子 bash 模板占位符必须 export**
11. **Release glob 翻译律**
12. **conf 注册两半律**
13. **struct 词汇律**
14. **上游接口验证律**：任何内核 API 先验 xnu-8796.121.2 上游
15. **token 即用即除**（本轮例外见 §10.3）
16. **预点火扫场律 + 窗口律**：点火前查活会话；`DWELL >= 600 s`
17. **判读器三课**：打印限流 ≠ 停摆；预览残迹律；代际隔离律
18. **P5 门禁作用域律**：字面量门禁必须钉在它治理的那段字节（entry/段/报告），不要钉在容器上（`d1_flash_pack.py:170` 首跑即假死，payload 里混着内核诊断串 `boot-arg metal=1 was not given`）
19. **P6 判读抓值不抓字面量**：故障 VA / 寄存器值随轮变，钉形状（小 VA + 我方 Lx=0），不钉十六进制串
20. **P7 dispatch 空输入回落默认值律**：workflow_dispatch 的空串会落回 `default`，要跳过必须给无害非空命令（D2 run#43 血案）
21. **P8 覆盖度律**：「零命中」只有在猎杀范围 == 记录范围 == 提交范围时才可判；否则只能写"在已记录范围内零命中"（D1 0 命中 vs D2 6 命中）
22. **reboot 瞬态律**：`fastboot reboot` 的 rc=1 无输出 = 未重启，重试并轮询

---

## 12. 下一会话的第一小时

1. `git fetch && git reset --hard origin/main`；读本文件 + `docs/WORKLOG.md` 尾部 D1/D2 段
2. **确认现场**：手机在哪台机器、是否在臂（`fastboot getvar product`）；NAS 可达则 `ssh … -p 222` 验证 `lsusb | grep 18d1`
3. **D3-A 刀（推荐先做，1 行）**：`CORE_GUARD_SIZE` 0x4000 → **0x200000**（整个 L2[0] 块）。走 D2 的老路：`gen_d2_flash_build.py` 的同型克隆 → CI（记得 §3.3 的 skip 命令）→ 下载 → `flash_pack` → 点火（**DWELL 600**）→ 取卷 → 判读"故障是否再上移/消失"
4. **判读要点**：`THE SMMU REFUSED` 的 address/syndrome；`the hunt scanned … hits` 的命中数与词位；`not in the ledger's N records`（账本覆盖）；`redwood-gl: frame` 的**有效段**最大帧号
5. **若 A 刀故障消失但 GC 仍停** ⇒ 直接开 **D3-B**：追 CB2 word 199..244 那 6 个值的来路（reloc 前后 + 所属 BO + handle→IOVA 转换），并把 48 环账本改成当前提交必在册
6. 别忘了：`oem screenshot` 面板实拍窗口（phase 6.5）仍待补进会话脚本；R62② 路由器重启等用户窗口，主动问一句
