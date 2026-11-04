#!/usr/bin/env python3
"""Re-pin the universal workflow's defaults from the R61 round to the D1 round.

The lesson list this obeys (all on record):
  - every substitution asserts EXACTLY ONE hit (the silent near-miss law);
  - the base_id_gates grep the id tool's REAL verdict words (run 40838276403);
  - no twin-grep left behind (run 41460053960 / bef1c05 lessons: BOTH copies);
  - release_name one line, the narrative rides release_body (256-char cap);
  - the base stays the r61 release (gpu 39c6e70e, kernel entry 8ef12fef,
    daily b0f9f6df, flashboot 14485e7c -- byte-identical to the r60/r54 ones).
"""
import pathlib

YML = pathlib.Path('/home/z/my-project/repo_hackios/.github/workflows/springboard.yml')
D1_GPU = '39c6e70e430e424b93f6cfe40d2c7d264c3bf2dde0813087fdd48aa559f99ea1'
D1_KERNEL = '8ef12fefbb2edc1cafdb0bb38bca4d0bb6adffee1771fdd333efc9c77b7b5ae7'


def sub1(text, old, new, what):
    n = text.count(old)
    assert n == 1, '%s: %d hits (want exactly 1)' % (what, n)
    return text.replace(old, new)


src = YML.read_text()

src = sub1(src, '        default: "r61"\n', '        default: "d1"\n', 'round default')
src = sub1(src, '        default: "r61-springboard"', '        default: "d1-springboard"', 'release_tag default')
src = sub1(src, '        default: "R61 · 纯血 hackios SpringBoard · 关联应答出卷轮（证据刀：67 字节 elements 十六进制出卷 + 诚实问数 + 逐问自报；判决树 HR17）"',
           '        default: "D1 · 纯血 hackios SpringBoard · GPU 停滞解剖取证轮（取证刀：提交侧账本点名 + 停滞 96 词出卷 + 0x3500 猎杀；判决树 HD1）"', 'release_name')
src = sub1(src, 'releases/download/r60-springboard/raphael-xnu-r60-flash.img"',
           'releases/download/r61-springboard/raphael-xnu-r61-flash.img"', 'base_daily_url')
src = sub1(src, 'releases/download/r60-springboard/raphael-xnu-r60-gpu-flash.img"',
           'releases/download/r61-springboard/raphael-xnu-r61-gpu-flash.img"', 'base_gpu_url')
src = sub1(src, '        default: "0a0683f1e7771dc99c23446afad42c255b5001137210685a888fd3ab69f296fe"',
           '        default: "%s"' % D1_GPU, 'base_gpu_sha')
src = sub1(src, 'releases/download/r60-springboard/raphael-r60-flashboot.apfs"',
           'releases/download/r61-springboard/raphael-r61-flashboot.apfs"', 'base_flashboot_url')
src = sub1(src, '          kernel=e0570b2adfa46400f38834aab1fa95dc4f838dd18cd700368817535bf8c106a7',
           '          kernel=%s' % D1_KERNEL, 'base_entry_shas kernel')

src = sub1(src, '          grep -c "the kernel entry is R60" "$BASE_IDFILE" | grep -qx 1 || { echo "::error::GPU 底板的内核必须判为 R60（R61 的门禁基准）"; exit 1; }',
           '          grep -c "the kernel entry is R61" "$BASE_IDFILE" | grep -qx 1 || { echo "::error::GPU 底板的内核必须判为 R61（D1 的门禁基准）"; exit 1; }', 'base gate R61 verdict')
src = sub1(src, '          grep -q "the kernel entry is R60 (the association-retry round" "$BASE_IDFILE" || { echo "::error::GPU 底板判词不是 R60 关联重试轮（r61-kernel-id 的真实判词）"; exit 1; }',
           '          grep -q "the kernel entry is R61 (the evidence round" "$BASE_IDFILE" || { echo "::error::GPU 底板判词不是 R61 关联应答轮（r61-kernel-id 的真实判词）"; exit 1; }', 'base gate R61 prose')

src = sub1(src, "          assert n('R60: the association asks the access point, try it again') == 0, 'the retired R60 marker still rides'",
           "          assert n('R60: the association asks the access point, try it again') == 0, 'the retired R60 marker still rides'\n"
           "          assert n('R61: the association answer read aloud in hex, all bytes') == 0, 'the retired R61 marker still rides'", 'pre_gate R61 retired line')
src = sub1(src, "              ('R61: the association answer read aloud in hex, all bytes', 'THE R61 association marker'),",
           "              ('D1: the stalled command buffers read aloud, word by word', 'THE D1 GPU stall forensics marker'),", 'pre_gate D1 marker entry')
src = sub1(src, 'R61 标记 1 项（R60 退役）', 'D1 标记 1 项（R61 退役）', 'pre_gate print D1')
src = sub1(src, "+ R60/R54/R52/R51/R50/R49/R48/R47/R46/R45/R44/R43/R42/R41/R40 标记零残留')",
           "+ R61/R60/R54/R52/R51/R50/R49/R48/R47/R46/R45/R44/R43/R42/R41/R40 标记零残留')", 'pre_gate print retired list')

src = sub1(src, 'python3 tools/r61_kernel_gate.py --base-kernel \\"$BASE_KERNEL\\" --r60-kernel \\"$K\\"',
           'python3 tools/d1_kernel_gate.py --base-kernel \\"$BASE_KERNEL\\" --r61-kernel \\"$K\\"', 'kernel_gate_cmd')
src = sub1(src, 'python3 tools/r61_flash_build.py --daily', 'python3 tools/d1_flash_build.py --daily', 'flash_build_cmd')
src = sub1(src, 'python3 tools/r61-kernel-id.py \\"$REL/raphael-xnu-${R}-flash.img\\"',
           'python3 tools/d1-kernel-id.py \\"$REL/raphael-xnu-${R}-flash.img\\"', 'kernel_id_cmd')

src = sub1(src, '          grep -c "the kernel entry is R61" "$IDFILE" | grep -qx 1 || { echo "::error::GPU 镜像内核判词不是 R61（本轮证据轮构建，r61-kernel-id 的真实判词词形）"; exit 1; }',
           '          grep -c "the kernel entry is D1" "$IDFILE" | grep -qx 1 || { echo "::error::GPU 镜像内核判词不是 D1（本轮 GPU 停滞取证轮构建，d1-kernel-id 的真实判词词形）"; exit 1; }', 'id_gates D1 verdict')

src = sub1(src, '''          raphael (K20 Pro) R61 交付包：**关联应答出卷轮（证据刀）**。

          R60 真机判读入账（HR16-2）：重试刀机械兑现（五问环 + 200ms 退避），AP 形状 =
          一次 53 拒（携 67 字节 elements、AID 11475 非常规）+ 四问沉默 —— PMKSA 落定窗
          假说削弱：沉默型拒绝指向请求本身不合法（RSN 构造律）强于"稍后再试"窗。SAE
          交换全通（hostap 本尊、commit/confirm 双 0、AUTH accepts）⇒ PMK/PMKID 与 AP
          必然一致，加密无罪。内核本体全绿：epoch 存活 2,094s 史上最高、零 panic、
          decode 58/0、adreno 843 行、stop 0.001s / logdump 0.012s / get_staged 净收、
          loader 保留总线（历轮最健康停车形状）。

          R61 一刀（证据轮）：关联应答的 elements 十六进制出卷（每行 16 字节）——67 字节
          里可能写着 comeback time、AP 自己的 RSN 律、或拒绝理由；诚实计数（真实问数
          入卷，R60 的 "three requests" 陈旧文案退役）；逐问自报（attempt N of 5）。
          标记同长换替（R61: the association answer read aloud in hex, all bytes）。
          cmdline 零改动。

          【刷机与取证】
          1. fastboot flash boot raphael-xnu-r61-gpu-flash.img; fastboot reboot
          2. 启动卷律（必守）：oem ramdisk 臂 → fastboot stage
             raphael-r61-flashboot.apfs → stage XNUP → oem run（漏
             stage = launchd ENOENT panic）
          3. 凭据：fw-nv 版启动卷内 network 文件两行（SSID 1-32 B / PSK 8-63 B）
          4. oem stop 后候 5s 再 logdump；分区写失败 = TWRP dd 兜底
          【判决树 HR17（预注册）】
          HR17-1 = 67B elements 点名可修律（comeback time / RSN 差异 / 理由码）⇒
                R62 按单修；
          HR17-2 = 元素出卷后 AP 应答形状变化 ⇒ 按新形状分诊；
          HR17-3 = 关联被收（THE NETWORK IS JOINED + 帧计数窗 > 0）⇒ 计划 R37 行
                后半终兑现；
          HR17-4 = panic/异常按码分诊。回滚 = r60 GPU 镜像。
          【镜像说明】
          - raphael-xnu-r61-flash.img = 日常控制组
          - raphael-xnu-r61-gpu-flash.img = 本轮目标镜像（仅 kernel 条目换新）
          - 启动卷 stage 一次：raphael-r61-flashboot.apfs 或固件+NV 版
          ⚠️ 含 Apple 版权内容，仅供个人研究；下载完成后请尽快删除本 Release。''',
           '''          raphael (K20 Pro) D1 交付包：**GPU 停滞解剖取证轮（取证刀）**。

          D0b 真机判读入账：用户态首次走完 BootUI→锁屏全程（redwood-gl frame 到 37、
          AppleLogo dismissed、Spinny、11MB 壁纸纹理装填），SIGBUS plague 绝迹、零
          panic、7 进程全正常退出——整个战役只剩 GPU 一处病根。真因链逐毫秒入卷：
          SpringBoard 渲染风暴（105.2-115.4s，34 submissions / 161 命令缓冲）中 swap
          30 撞 500ms 门，submission 30 卡死（core done 29 / ring 1299==1299 空转停），
          106.27s SMMU 拒绝 3D 核 @ 0x3500（status 0x80000402 / syndrome 0x20002 /
          我方页表 L2[0]=0）——0x3500 三代同现（R39 关机 / R41 复位 / D0b 运行期），
          命令流模板级 bug，最大嫌疑 = 池复用缓冲上的 reloc 漏 patch。

          D1 一刀（取证轮，不动行为）：swap 门首响时 —— ① 提交侧账本点名卡死的
          submission 与命令缓冲（含池复用注记：同一地址曾是更早 submission 的命令缓冲）；
          ② 停滞位置前后 96 词从内核自身映射读出卷；③ 0x3500 与近 NULL 指针形状猎杀，
          逐命中对照该 submission 自名的缓冲区间（域内 / 无名二值判词）。
          标记同长换替（D1: the stalled command buffers read aloud, word by word）。
          cmdline 零改动（会话轮换的 d1.xnup 用 D0b 形状 cmdline，本地打包）。

          【刷机与取证】
          1. fastboot flash boot raphael-xnu-d1-gpu-flash.img; fastboot reboot
          2. 启动卷律（必守）：oem ramdisk 臂 → fastboot stage
             raphael-d1-flashboot.apfs → stage XNUP → oem run（漏
             stage = launchd ENOENT panic）
          3. 凭据：fw-nv 版启动卷内 network 文件两行（SSID 1-32 B / PSK 8-63 B）
          4. oem stop 后候 5s 再 logdump；分区写失败 = TWRP dd 兜底
          【判决树 HD1（预注册）】
          HD1-1 = 0x3500（或近 NULL 形状）在卡死命令流内 = 命令流生成 bug（指针未转
                IOVA / reloc 漏 patch）⇒ D2 单变量修复；
          HD1-2 = 流内无 0x3500 而 IB2/链指针坏 = 链损坏 ⇒ D2 按单修；
          HD1-3 = 命令流完好无损 = GPU 侧深挖（shader 上传 / 页表 / VFD）⇒ D2 分诊；
          HD1-4 = panic/异常按码分诊。回滚 = r61 GPU 镜像。
          【镜像说明】
          - raphael-xnu-d1-flash.img = 日常控制组
          - raphael-xnu-d1-gpu-flash.img = 本轮目标镜像（仅 kernel 条目换新）
          - 启动卷 stage 一次：raphael-d1-flashboot.apfs 或固件+NV 版
          ⚠️ 含 Apple 版权内容，仅供个人研究；下载完成后请尽快删除本 Release。''', 'release_readme')

src = sub1(src, '''          raphael (K20 Pro) R61 交付包：**关联应答出卷轮（证据刀）**。

          R60 定案：五问全拒/沉默（53 + 67B elements + AID 11475），墙在关联请求
          构造侧或 AP 的应答语义里 —— 67 字节无人读过。SAE 加密链全清白（hostap
          本尊 + AP 已验 confirm）。epoch 存活 2,094s 史上最高、零 panic。

          R61 一刀：应答 elements 十六进制出卷（16 字节/行）+ 真实问数 + 逐问
          自报；标记同长换替。cmdline 零改动，R60 重试律在膛。

          本轮判决问题（HR17 预注册）：elements 点名可修律 ⇒ R62 按单修；关联
          被收 ⇒ 计划 R37 行后半终兑现；panic ⇒ 按码分诊；回滚 = r60 GPU 镜像。

          内核 = R60 稳态 + 出卷刀（lx_80211.c 应答 hex 打印 + 诚实计数）；
          loader/cmdline/卷 = r60 release 原字节（= r54 谱系）；日常镜像 =
          r60 逐字节控制组。WiFi 固件+NV 走 flashboot 用户侧重建（R56 律沿袭）。''',
           '''          raphael (K20 Pro) D1 交付包：**GPU 停滞解剖取证轮（取证刀）**。

          D0b 定案：用户态史上最健康（走完 BootUI→锁屏、零 SIGBUS、零 panic），
          病根独居 GPU —— SMMU 运行期拒绝 @0x3500，三代同现（R39/R41/D0b），
          命令流模板级 bug；停死 IB1 0x20488000 = submission 4 用过的池复用缓冲，
          0x3500 最大嫌疑 = reloc 漏 patch 残留。

          D1 一刀：swap 门首响即出 —— 提交侧账本点名卡死 submission + 池复用注
          记；停滞位置前后 96 词出卷；0x3500/近 NULL 指针猎杀逐命中对照自名缓冲
          区间。标记同长换替。cmdline 零改动。

          本轮判决问题（HD1 预注册）：0x3500 在流内 ⇒ D2 单变量修；不在流内 ⇒ 链
          损坏分诊；流完好 ⇒ GPU 侧深挖；panic ⇒ 按码分诊；回滚 = r61 GPU 镜像。

          内核 = R61 稳态 + 取证刀（raphael_adreno.c 报告钩 + RedwoodGPU.cpp 账本
          与 dump）；loader/cmdline/卷 = r61 release 原字节（= r54 谱系）；日常镜
          像 = r61 逐字节控制组。WiFi 固件+NV 走 flashboot 用户侧重建（R56 律沿袭）。''', 'release_body')

src = sub1(src, '        default: "raphael-xnu-r61-flash.img raphael-xnu-r61-gpu-flash.img raphael-r61-flashboot.apfs r61-md1-root.apfs"',
           '        default: "raphael-xnu-d1-flash.img raphael-xnu-d1-gpu-flash.img raphael-d1-flashboot.apfs d1-md1-root.apfs"', 'checksum_files')
src = sub1(src, '''          raphael-xnu-r61-flash.img
          raphael-xnu-r61-gpu-flash.img
          raphael-r61-flashboot.apfs
          r61-md1-root.apfs
          r61-kernel-gate.txt''',
           '''          raphael-xnu-d1-flash.img
          raphael-xnu-d1-gpu-flash.img
          raphael-d1-flashboot.apfs
          d1-md1-root.apfs
          d1-kernel-gate.txt''', 'release_files')

YML.write_text(src)
print('springboard.yml re-pinned for D1 (%d lines)' % len(src.splitlines()))
leftovers = [l for l in src.splitlines() if 'r61' in l.lower()]
print('lines still mentioning r61 (%d, all legitimate: base urls/tool/verdicts/rollback prose):' % len(leftovers))
for l in leftovers:
    print('   ', l.strip()[:110])
