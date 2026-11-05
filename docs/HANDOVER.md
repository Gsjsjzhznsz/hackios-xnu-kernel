# hackios 完整交接书

**版本锚点**：main = `3d58e2e`（2026-11-05，D1 取证轮 CI run#42 在飞时点）
**读者**：下一个 AI 会话（或人类接手者）。读完本文件即可接手，不需要任何聊天历史。
**本文件已提交进正典仓库**——沙盒会回收（已有三次前科），任何新沙盒 `git clone` 即得最新版；动手前先 `git fetch` 对齐。

---

## 1. 项目一句话

把 Apple 的 **xnu-8796.121.2** 内核 + 一个自研 mini-iOS 用户态（MiniIO* 族：backboardd/SpringBoard/IOMobileFramebuffer 形状）跑到小米 **raphael（K20 Pro / Mi 9T Pro，SM8150，Adreno 618）** 真机上：自研 XNULOADER 引导 → Android boot 镜像内嵌 XNUP payload → fastboot 点火 → 真机判读。

**分工模型**：AI 会话负责源码手术 / CI 流水线 / 日志判读 / 账本归档；**用户负责物理世界**：点火（fastboot）、盯屏与肉眼证词（判决树的分支依据）、路由器等物理操作、点火窗口的开合。

**方法论**：一切按"轮"推进。一轮 = 预注册刀序 → 源码单变量手术 → 离线证明 → CI 构建门禁 → 真机点火取卷 → 判读定谳 → 账本落档。判决树预注册（HD* / HR* 系），证词开火哪支走哪支。

---

## 2. 当前进度鸟瞰（截至本文件提交）

| 线路 | 状态 |
|---|---|
| 显示硬件层 | ✅ 全活（three scanout / INTF1 TE 呼吸 / 面板 120fps / flip 链） |
| BootUI 白苹果 | ✅ 上屏（backboardd 直连 swap 时代 39-96s 可见） |
| 用户态 | ✅ **首次走完 BootUI→锁屏全程**（D0b：frame 37、AppleLogo dismissed、Spinny、11MB 壁纸纹理装填）；零 SIGBUS（R36-R48 plague 绝迹）、零 panic、7 进程全正常退出 |
| GPU | ❌ **全战役唯一病根**：SpringBoard 渲染风暴下 SMMU 拒绝 3D 核 @ 0x3500，submission 卡死，锁屏永不点亮（详见 §7） |
| 背光 | ⚠️ 内核无物理背光通道（AMOLED DCS 0x51 未驱动，MiniIOMobileFramebuffer.cpp:147 铁证）；CoreBrightness factor 0/1.0 = 软件黑帧执行者（redwood_fb_set_dim） |
| WiFi | ❌ 挂起中：SAE 加密链全通，关联被 AP 拒 status 53（AP 侧收录问题；零码试验 = 路由器重启后原包重火，等用户窗口，详见 §8） |
| 本轮 D1 | 🔄 GPU 停滞取证刀：run#41 死于 define-after-use（已修+新律），**run#42 在飞**（§7） |
| 下轮 D2 | 单变量修复，按 HD1 判决树分诊 |

---

## 3. 仓库、CI 与工具链

### 3.1 仓库
- **origin** = `https://github.com/Gsjsjzhznsz/hackios-xnu-kernel`（main = 正典账本；`docs/WORKLOG.md` = 全史判读账本，本轮 everything 落账于此）
- **helium** = `https://github.com/HeliumStudio-Dev/hackios-xnu-kernel.git`（参考仓库 remote；其桌面可见、MiniIOSurface 同源）
- 内核树 = `xnu/`（overlay 机制：`xnu/apply.sh` 把 `redwood.patch` + `xnu/files/` 覆盖到 pristine xnu-8796.121.2 上构建；新增内核源文件必须同时进 `osfmk/conf/files` 注册——R42 两半律）
- `.gitignore` 是**白名单制**（`/*` 全忽略 + `!/path` 豁免）：`scripts/` 下新文件要 `git add -f`
- 布局：`xnu/files/`（全部内核改动）· `tools/`（每轮工具）· `scripts/`（判读器/证明/生成器/会话脚本）· `docs/`（WORKLOG + 本文件）· `.github/workflows/springboard.yml`（**唯一工作流**，所有轮共用，轮差异全在 dispatch 输入默认值里）

### 3.2 每轮工具四件套（tools/，按轮换代）
| 工具 | 用途 |
|---|---|
| `<r>_flash_build.py` | CI 内：r61 谱系底板 + 新内核 → 双镜像（daily 控制组 + gpu 目标镜像，cmdline R39 原样） |
| `<r>_kernel_gate.py` | 新内核 vs 底板内核的六法门禁（size/marker/lineage/segment/strings/symtab） |
| `<r>-kernel-id.py` | 镜像判读（sha KNOWN 表 + 标记 ladder；**判词原话会被 id_gates grep**——改判词必须同步改 yml） |
| `<r>_flash_pack.py` | 本地会话包：CI gpu 镜像上**只换 cmdline** → `<r>.xnup` |

### 3.3 CI（springboard.yml，workflow_dispatch 制）
8 步：①依赖 → ②取树(clone main HEAD)+底板三件(SHA 硬校验)+条目提取+底板判读 → ②c-e 三缓存 → ③内核构建(theos v19L2 clang)+门禁一/二(pre_gate_py)+门禁三(kernel_gate_cmd) → 诊断产物 → ④双镜像手术(flash_build_cmd) → ⑤判读复证(kernel_id_cmd+id_gates) → ⑥SHA/README/Release(softprops，release/ 前缀 glob 翻译律) → ⑥c 直链汇总。
**dispatch**：`curl -X POST -H "Authorization: token <TOKEN>" …/actions/workflows/springboard.yml/dispatches -d '{"ref":"main"}'`（**默认输入 = 当前轮钉**，不用传输入）。
产物命名 `raphael-xnu-<r>-gpu-flash.img` 等；release tag `<r>-springboard`。

### 3.4 门禁纪律（CI 上真实运行的断言）
- `pre_gate_py`：谱系 + 换代件标记逐字在字节里（R33 谱系 4 项、R38 显示/WiFi 5 项、sigbirth/sigpost/siglive/sigcensus 各 1、redwood-fault x2、pager 判词 x6、D1 标记 x1、R61 及更早标记零残留）
- `kernel_gate_cmd`：六法（size <64KiB；**标记同长换替**；lineage；segment 律（__TEXT 增长页量子化 <32KiB、其余段随移）；strings 律（**增益掩码 = D1 标记 + `adreno: gpu: dump:` 前缀 + run-like + Darwin；损失 = R61 标记**）；symtab 不缩）
- `id_gates`：grep 判读工具的**真判词**（"the kernel entry is D1"）；底板门禁 grep "the kernel entry is R61"（孪生双改律）

---

## 4. 每轮生命周期（家法流程，照做即可）

1. **判读上轮**：判读器（`scripts/<r>_judge.py` / `d0b_judge.py` 式）吃 logdump，产出定谳
2. **预注册刀序 + 判决树**：进 WORKLOG；一轮只修一个变量（单变量律）
3. **源码手术**：程序化脚本（`d1_surgery_apply.py` 式，**不要用编辑器直接改**——见 §9 律 5）
4. **标记同长换替**：56B 族标记，宿主 = `xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp` 的 `__attribute__((used)) static const char <r>_mark[]`
5. **工具代生成**：`gen_<r>_tools*.py` 断言式单命中克隆上一代
6. **yml 重钉**：`gen_<r>_yml.py`（26 处：round/tag/release_name/底板 URL+SHA/entry shas/底板门禁/pre_gate/三 cmd/id_gates/README/body/checksum/release_files）
7. **离线证明**：`d1_surgery_proof.py` 式（P 法 + N 负控制；掩码从 diff 机械推导，不猜）
8. **WORKLOG 落账 + commit + push**（token 见 §10）
9. **dispatch CI** → 守望（fail 先拉 jobs API 日志定位）
10. **下载** `raphael-xnu-<r>-gpu-flash.img` + `<r>-kernel-gate.txt`（记 KSHA）
11. **本地打包**：`python3 tools/<r>_flash_pack.py --base <gpu.img> --base-sha <下载镜像 sha> --kernel-sha <KSHA> --out-gpu … --out-xnup <r>.xnup`
12. **点火**：`scripts/r59_session.sh`（旋钮 `R59_XNUP=<r>.xnup` `R59_DWELL=<秒>`；**预点火扫场律**）
13. **取卷**：`oem stop`（OKAY 后候 5s）→ `oem logdump` → `get_staged`；loader 沉默则 TWRP dd 64MB 分区兜底（历轮 6 次兑现）
14. **判读 + 归档**：NAS `/vol1/1000/项目/hackios/`

---

## 5. 启动链与用户侧协议

- **XNUP payload**：entries = `loader.efi / kernel / ramdisk / cmdline / usb`（16B 名 + u32 len + 数据，16B 对齐；gzip 后进 boot.img kernel 区）
- **点火链**：`fastboot flash boot <r>-gpu-flash.img` → `fastboot reboot` → **启动卷律（必守）**：`oem ramdisk` 臂 → `fastboot stage raphael-<r>-flashboot.apfs` → `stage <r>.xnup` → `oem run`（漏 stage = launchd ENOENT panic）
- **停车形状**：`18d1:d00d` = ABL fastboot 稳态（正常）；`18d1:d001` = TWRP（分区兜底的入口）
- **预点火扫场律**：任何点火前 `ps` 查活会话 + `/proc/<pid>/environ` 里 `R59_XNUP` 自证（双火事故 ddebf07 的教训）
- **窗口律**：`oem screenshot` 面板实拍必须在 `oem stop` OKAY 后的 loader 存活窗内（phase 6.5；d0b 轮两连失窗的教训——**下一版 r59_session.sh 要补**）
- **cmdline 语义**：DAILY 组 = `touchscreen=1 buttons=1 cpus=8 ufs=2 bootcpu=7 root_apfs=ufs_userdata`；GPU 组 = `gpu=1`（adreno/metal 五枚已在 D0 轮退役）；`fb_scan=1`；wifi 组 D0b 起显式 `wifi=0`（单变量显示轮形状，93B 含 NUL）

---

## 6. 内核链 sha 表（对官方字节的锚）

| 件 | sha256 前 8 | 说明 |
|---|---|---|
| loader.efi | `4bf828e4` | r38..r61 不变（含 autorefresh 指纹 `display: raphael command-mode autorefresh enabled`） |
| ramdisk（小卷） | `5b9bbbcb` | r39..r61 不变 |
| daily 底板 | `b0f9f6df` | r40 起、历轮逐字节控制组 |
| flashboot | `14485e7c` | 与 r54 逐字节同 |
| **r61 gpu 镜像** | `39c6e70e` | **D1 门禁基底**（内核 entry = R61 构建 `8ef12fef`，17,027,112 B） |
| D0 材料 | `download/d0/`：d0.xnup `04d3ab67` / d0b.xnup `91c62c30` / r59-logdump-d0b-staged.bin `a26da4a5`（NAS 有副本） | |

R61 release 直链模板：`https://github.com/Gsjsjzhznsz/hackios-xnu-kernel/releases/download/r61-springboard/<asset>`。

---

## 7. D1 全案（本轮完整上下文）

### 7.1 死亡链（D0b 卷逐毫秒）
```
105.2s   SpringBoard 渲染风暴开闸（34 submissions / 161 命令缓冲 / 10151ms 窗全在此）
106.261s swap 30 撞 500ms 门（"the GPU is not done with surface 4 after 500 ms"）
106.261s adreno 快照：submission 32 is the last, core done 29; ring 1299==1299（CP 空转停）
106.273s THE SMMU REFUSED the 3D core: status 0x80000402, address 0x3500, syndrome 0x20002
         （space_faulted 完整命名路径 = FSR_FATAL 等待放弃；我方页表 walk L1[0]=0x105776003 → L2[0]=0）
106.8-116.9s swap 31-36 全部 500ms 丢帧，没有一个 SpringBoard 帧上屏
面板永远停在 frame 5 的熄屏黑帧 = 用户看到的黑屏
```

### 7.2 已钉死的事实
- **0x3500 三代同现**：R39 关机快照 / R41 复位路径 / D0b 运行期 —— 命令流模板级 bug，非随机损坏
- **池复用证据**：停死 IB1 `0x20488000` = submission 4 的第一命令缓冲（45.726s 提交，156 词，84 词已执行处停）
- **最大嫌疑 = reloc 漏 patch**（池复用缓冲上 reloc 残留/漏重打）。**注意**：提交侧 `nr_relocs != 0` 的命令缓冲直接被拒（RedwoodGPU.cpp submit 门），patch 是**用户态**（Mesa）打的——漏 patch 是用户态形状，内核侧取证能证明"流里有 0x3500"与"0x3500 不在任何自名缓冲区间"
- **IOSurfaceSharedEvent e00002c7 已降级为杂音**（swap 其实在发生）
- R50 起 25 个 "frame dropped" 曾被判"背景噪声"——**误诊**，实为 SMMU 拒绝驱动的 CP 停滞

### 7.3 D1 取证刀（已入正典：fdaaad5 手术 + 4a4dfa1 修复 + 3d58e2e 账）
- **报告钩**（`raphael_adreno.c` `redwood_gpu_report()`）：IB1/IB2 寄存器一次读入局部量，打印与交接同一瞬；dump 在 `loader_log_flush()` 之前 → 一卷带走全程
- **提交侧账本**（`RedwoodGPU.cpp`）：48 环 × 每 submission 8 IB × 16 BO 区间；写时 gGPULock 下（wrap 用 `++at == RING` 有界回卷，不对 wrapped uint32 取模）；读时 `IOLockTryLock`（上游 xnu-8796.121.2 IOLocks.h 已验证；拿不到锁就无锁读并自报）——报告可能带锁（gpuWait no-answer / ring-full 路径）也可能不带（frame-dropped 路径）
- **dump 本体**：定位卡死 submission（CP_IB1_BASE 对账）→ 96 词 ±48 出卷 → **0x3500 猎杀**（精确值 + 近 NULL 64 位对形状：lo<0x10000 && lo!=0 && hi==0）→ 逐命中二值判词（该 submission 自名缓冲区间内 / 无名）→ 池复用注记（同一地址曾是哪个更早 submission 的命令缓冲）→ 单 fire（static said）+ 上限（猎 16384 词 / 6 命中）
- **标记**：`D1: the stalled command buffers read aloud, word by word`（56B，换替 R61）
- **d1 工具代**：`tools/d1_flash_build.py` / `d1_kernel_gate.py` / `d1-kernel-id.py` / `d1_flash_pack.py`（CLI：`--base --base-sha --kernel-sha --out-gpu --out-xnup`）
- **run#41 尸检**：`GPU_STALL_*` #define 在使用它们的 struct 之后 = define-after-use 编译错（CI 41866501957 步③ RedwoodGPU.cpp:253:29）；修复 = 字节级搬移（`scripts/d1_run41_fix.py`，6+/6-，raw==-w）；**P4b 律**入册（数组界常量的"值"与"序"是两条独立的法）
- **run#42 = https://github.com/Gsjsjzhznsz/hackios-xnu-kernel/actions/runs/42010259868（4a4dfa1，2026-11-05 dispatch）**

### 7.4 HD1 判决树（预注册，判读器开火用）
- **HD1-1** = 0x3500（或近 NULL 形状）**在卡死命令流内** ⇒ 命令流生成 bug（指针未转 IOVA / reloc 漏 patch）⇒ **D2 单变量修复**
- **HD1-2** = 流内无 0x3500 而 IB2/链指针坏 ⇒ 链损坏 ⇒ D2 按单修
- **HD1-3** = 命令流完好无损 ⇒ GPU 侧深挖（shader 上传 / 页表 / VFD）⇒ D2 分诊
- **HD1-4** = panic/异常 ⇒ 按码分诊
- 回滚 = r61 GPU 镜像（39c6e70e）

---

## 8. WiFi 线（挂起中，R62 刀序已预注册）

- 现状：SAE commit/confirm 双 0（加密链全通，PMK/PMKID 与 AP 必然一致）；**关联拒 status 53** + 67B 能力 IE（高通 OUI `8c:fd:f0` = QCN 族，纯能力通告无拒因语义）+ 应答 AID=0（互证 hostapd `get_aid` 失败路径 "No room for more AIDs"）
- 我方请求无罪（源码级定谳）：RSN 携 AKM 8 + PMF 0xc0 + PMKID + RSNXE，fork 四律全在膛
- **R62 刀序**：① vendor IE 语法解读（**已落袋**：qca-wifi 侧无公开语义，闭卷）→ ② **路由器重启（或查客户端表）后原包重火**（零码试验，r61.xnup 轮换无关——**等用户窗口**）→ ③ 内核小刀（ext-cap 镜像 / REASSOC 形状 / listen-interval，一轮一刀）
- 判决树 HR18 预注册在案：AP 重启后 join 胜 ⇒ 计划 R37 行后半兑现；仍 53 ⇒ 按单修；status 变 ⇒ 分诊
- 回滚律：cmdline-append 不含 join 重跑 R58 稳态

---

## 9. 律法清单（每条都有出处，违者已付出代价）

1. **fetch before convicting**：任何定罪/动刀前先 `git fetch` 对齐正典（沙盒会回滚、别的通道会推进；本会话第三次兑现——盲目重建手术就撞上 run#42）
2. **未提交 == 未发生**：手术完成即 commit+push（三次容器回收吃掉未提交工作的教训）
3. **单变量律**：一轮只动一个变量；cmdline 轮与源码轮分开
4. **标记同长换替律**：56B 族；"标记在膛 ≠ 标记出卷"（帧缓冲路径回声在本 boot 形状不触发，打印零触发是常态）
5. **编辑器 TAB-churn 律**：编辑器级修改会把 tab 缩进文件整文件归一化（2134 假 diff vs 294 真改动）——tab 缩进文件用**程序化手术脚本**，验收 = `git diff` == `git diff -w`
6. **define-before-use 律（P4b）**：数组界常量的值与序是两条独立的法；离线证明管语义、编译器管声明序，两者都过才算数
7. **门禁 grep 工具真判词**（run 40838276403）；**孪生 grep 双改**（41460053960/bef1c05：同义 grep 出现两处必须都改）
8. **\n 掩码律**：门禁字符串掩码按**提取器输出域**写（可打印 run 不含 \n），不按源文件字面抄（run 41472979841）
9. **pipefail 律**：一切门禁管道 `bash -eo pipefail`（tee 吞退出码假绿）
10. **子 bash 模板占位符必须 export**（run 40853122934：K 空串）
11. **Release glob 翻译律**：`uses:` 步的文件列表相对 base 不同时前置翻译步
12. **conf 注册两半律**：新增内核源文件 + conf/files 注册是一个刀的两半（R42）
13. **struct 词汇律**：动 struct 内部的轮要用上游头文件词汇做机械证明（R42）
14. **上游接口验证律**：任何内核 API 使用先验 xnu-8796.121.2 上游（IOLockTryLock / vm_map_try_lock 先例；WebFetch apple-oss-distributions raw）
15. **token 即用即除**：不入档、不进文件、不进日志
16. **预点火扫场律 + 窗口律**（§5）
17. **判读器三课**：打印限流 ≠ 停摆（`frames++ < 3` 假象，R39 验尸课）；预览残迹律（日志驱动 2s 冲刷注记的问数尾巴）；代际隔离律（新异常先问是哪代引入的）

---

## 10. 环境与恢复

- **沙盒回滚恢复法**：`git fetch origin && git reset --hard origin/main`（本仓库三次痊愈；持久区快照可能异代，以正典为准）
- **token**：用户发的 `ghp_…` 即用即除。当前账面：上次发的 token（2026-11-05 会话）**至今仍活**（3d58e2e 推送用它）；401/403 时向用户要新的
- **NAS**：`/vol1/1000/项目/hackios/`（判读卷 + 会话日志归档）；UGOS `sudo -S`（密码走 stdin）；root 不能覆盖 admin-owned `/tmp` 已存在文件——keeper 日志用新路径
- **keeper**：`scripts/r59_keeper.sh`；NAS 版与仓库版有差异（fb_wait 有界 60×10s vs 8s×∞）——下次通道窗口先 diff+refresh
- **判读输入**：staged 4MB（get_staged）优先，TWRP 64MB 分区版兜底；判读器 `d0b_judge.py` 是最新形状（D1 版 = 抓 `adreno: gpu: dump:` 段 + 0x3500 命中分类 + HD1 分支判定）

---

## 11. 下一会话的第一小时

1. `git fetch && git reset --hard origin/main`；读本文件 + `docs/WORKLOG.md` 尾部 D1 段
2. **查 run#42**（`curl -H "Authorization: token …" …/actions/runs/42010259868`）：
   - **绿** → release `d1-springboard` 在架 → 下载 `raphael-xnu-d1-gpu-flash.img` + `d1-kernel-gate.txt`（记 KSHA）→ `python3 tools/d1_flash_pack.py --base raphael-xnu-d1-gpu-flash.img --base-sha <镜像 sha> --kernel-sha <KSHA> --out-gpu raphael-xnu-d1-forensic.img --out-xnup d1.xnup` → 进点火
   - **红** → jobs API 拉日志按死点名修（先跑 `python3 scripts/d1_surgery_proof.py` 排除离线可检的）→ 修 → push → re-dispatch
3. **点火协议**：预点火扫场 → `R59_XNUP=d1.xnup R59_DWELL=300` → **告知用户：T0+30s 起盯屏 2 分钟**——这次 GPU 停滞时命令流全裸出卷；若顺带修复生效，用户将**第一次看到 SpringBoard 锁屏画在真机上**
4. **判读**：`stop/logdump/get_staged` 三件 + TWRP 兜底 → D1 版判读器 → HD1 分支定谳 → NAS 归档 → **D2 预注册进 WORKLOG**
5. 别忘了：`r59_session.sh` 下一版补 phase 6.5（oem screenshot 窗口）；R62② 刀（路由器重启）等用户窗口，主动问一句
