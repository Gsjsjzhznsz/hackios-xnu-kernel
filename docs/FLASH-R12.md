# FLASH-R12 — GPU 判读修复轮：step 5 三处移植错误定案

R11 真机战报：**触摸、按键、清屏全胜**（`selected GT9886 normandy`、
`1257 reads of the bus, 7 us each, 0 failed`）；GPU 实验镜像卡在 step 5 的
"频率计数器测量"（`the counter did not finish`），系统回退软渲染。本轮把
r11-gpu-log.txt 逐行对着三个权威源（vendor gcc-sm8150.c、mainline
gpucc-sm8150.c、mainline sm8150.dtsi）核完，R11 移植的三处错误全部定案并修复
（`tools/host/r12_fix_adreno.py`，每一处注明出处）。**内核是唯一变化**；
触摸/按键/清屏字节不动，iOS 卷与 R5-R11 相同，双镜像 cmdline 与 R11 相同
（对照实验：只换内核）。

## 病理：计数器为什么"永远数不完"

那个测量块（GCC 0x162000 一族）的工作方式是：**拿被测时钟驱动计数器**，
拿 19.2MHz 晶体 /4（4.8MHz）当时间窗。RUN 置起后，DONE 位要等**被测时钟
自己的边沿**才置起——被测链上任何一环静止（时钟停 / debug 缓冲关 / mux
指向空档），DONE 永远不来。这不是等待时间不够（poll 上限 200ms，计数窗
才 1ms），也不是写入顺序错（CLEAR→ticks→RUN 与 vendor 完全一致）——
是**输入链断**。R11 的链断在移植错误上：

## 三处修复（每处都有权威出处）

### ① 300MHz 投票写错了寄存器 —— 0x152000 → **0x152004**

R11 日志原文：`gcc pll votes (0x152000) = 0x1`，写 `0x10001`，读回还是
`0x1`——bit16 写入被丢。vendor gcc-sm8150.c（`work/ref-raphael-gcc.c`，
本机内核源）：

```
0x15200 = GPLL vote 寄存器（bit0=gpll0——日志读到的 0x1 就是它，
            bit6=gpll6，bit7=gpll7）
0x15204 = GPU 的两路 tap：bit15 = gcc_gpu_gpll0_clk_src（600MHz）
                            bit16 = gcc_gpu_gpll0_div_clk_src（300MHz）
```

R11 把 bit16 写进了 0x15200（GPLL vote 的保留位）。真地址 0x15204 bit16
本轮起生效——这是 gmu_clk_src 的 parent（300MHz tap）的送进门。

### ② "hub" 家族是 SC7280 的 —— SM8150 GPUCC 里根本没有

mainline **gpucc-sm8150.c** 全表：`gmu_clk_src@0x1120`、`ahb@0x1078`、
`cx_gmu@0x1098`、`cxo@0x109c`、`cxo_aon@0x1004`、`crc_ahb@0x107c`、
`cx_apb@0x1088`、`cx_snoc_dvm@0x108c`、`gx_gmu@0x1064`——**没有**
hub_clk_src / hub_aon / hub_cx_int / cb（那些是 gpucc-sc7280.c 的）。
R11 把五个 SC7280 偏移（0x1170/0x117c/0x1178/0x1180/0x1204）当 SM8150
用——全是未实现洞：**读 0、写丢、不 fault**，与日志逐字吻合
（`hub_clk_src 写 0x603 读回 0`；`hub_aon/hub_cx_int/cb 写 1 读回 0`）。

SM8150 的 SMMU 时钟是 **sm8150.dtsi** 里 adreno_smmu 节点写的：
`gpucc ahb + gcc memnoc_gfx + gcc snoc_dvm_gfx`——R11 日志显示这三个
**本来就在跑**（`0x221`、`0x1`、`0x1`）。修复：四条幽灵写入删除，
补 `cxo_aon@0x1004`（AON 侧晶体缓冲，R11 读到 0=关着）。

### ③ 计数器从"门禁"降级为"证人" —— 用户问题的正面回答

**能不能跳过测量、固定频率往下走？能，而且这是正确做法**：

- Linux 从不让 bring-up 等测量（measure block 只在 debugfs 里用，且是
  SC7280 一代 debugcc 的产物）；
- 频率本来就是**算术**：RCG 半分频写的 3/2，×300MHz tap = **200MHz 整**
  （gpucc-sm8150.c 频率表原文 `F(200000000, P_GPLL0_OUT_MAIN_DIV, 1.5)`），
  不需要计数器告知；
- 硬件握手另有更权威的证人：RCG 的 UPDATE 位写 1 后硬件清 0（R11 日志里
  gmu_clk_src 的 UPDATE **完成了**——时钟切换真落地）；GDSCR 的 PWR_ON
  （step 4 的 CX 域，0xf8222000）。这两样 R11 全都通过了。

本轮起：测量失败只打一行
`the counter did not finish ... going on with the configured rates`，
按配置值继续；`cx_ready()`（后面 15 步的公共门）的总线证据改用 AHB 控制
位（`0x1078`：bit0=enable 置位、bit31=OFF 清零——R11 日志读到的 0x1）。
测量链上的 SC7280 mux 值（0x146）与 debug 偏移（0x10fc/0x1100/0x1568）
保留在代码里但不再是门——这次若反而测出来，白得真频率数据。

## 刷写（与 R11 完全相同）

```
fastboot boot  raphael-xnu-r12-flash.img          # 主镜像：触摸+按键
fastboot oem  ramdisk
fastboot stage raphael-r12-flashboot.apfs
fastboot oem  run                                 # 黑屏 → 桌面 → +5s 触摸上电

# GPU 实验镜像（第二刷，同卷）：
fastboot boot  raphael-xnu-r12-gpu-flash.img      # 同上 + gpu=1
fastboot oem  ramdisk
fastboot stage raphael-r12-flashboot.apfs
fastboot oem  run
```

## 判读树（GPU 实验镜像，klog 里 `adreno: N`）

| klog 特征 | 含义 / 下一步 |
|---|---|
| `step 5: ... going on with the configured rates` | **R12 生效签名**（新日志串）——测量降级已编译进内核 |
| `gcc pll votes (0x152004) = 0x1????` 且 bit16 保住 | 300MHz 投票真地址落地（对照 R11 的 0x15200 丢位） |
| `cxo_aon (0x2c91004)` 从 0 变 0x1 | 新增的 AON 晶体缓冲开起来了 |
| step 6+ 每步一行推进 | 总线证据链（AHB 控制位）接管，测量不再是断点 |
| **22 步全通** | 硬件加速全开（CoreAnimation 丝滑）——请录屏 |
| 卡 6/7（GMU/RSCC 首读） | 下一轮对象：GMU 窗口地址表；回传 klog |
| 计数器这次测出来了（`measured: cxo runs at ...`） | 白得的真频率数据，一并回传 |
| 卡 11/12（TZ 调用） | zap 签名路径；回传 klog |

触摸/按键判读与 R11 相同（真机已验证，预期不变）。

## 回退链

R11 镜像随时可回（触摸/按键/调光同款、GPU 仍软渲染）；R9 = verbose 取证版。
R11 与 R12 的 iOS 卷/启动卷字节相同——**刷过 R11 的手机只需换 boot 镜像**，
userdata 不用重刷。

## R12 观察清单（回复时带上）

1. GPU 镜像 klog 全文（`fastboot oem log && fastboot get_staged r12-klog.txt`）
2. `adreno: 5:` 段落（0x152004 的读写行 + `cxo_aon` 行）——本轮三修复的现场
3. 走到第几步、卡点步号
4. 若 22 全通：桌面动画流畅度（对照 R11 软渲染）、`adreno: 22` 后的彩图照片
