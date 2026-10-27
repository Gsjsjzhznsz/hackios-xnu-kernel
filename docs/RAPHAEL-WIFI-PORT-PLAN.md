# RAPHAEL WiFi 移植方案（R35+ 立项）

参照系（用户指令）：[HeliumStudio-Dev/hackios-xnu-kernel](https://github.com/HeliumStudio-Dev/hackios-xnu-kernel)——同机型（SM8150/raphael）WiFi 已真机验证：**2.4 GHz WPA2、5 GHz WPA3 SAE 关联 + 实际网页加载**（其 `aa21593` + `docs/RAPHAEL-WIFI.md`）。本方案把它译成 R 系轮次纪律。

## 一、硬件事实（fork 实证 + 本机核对）

| 项 | 结论 |
|---|---|
| 芯片 | WCN3990（WCN399x 家族），ath10k-SNOC 形态——**不是**上游 redwood 树的 WCN6750/WPSS/ath11k（那套 `redwood_wifi.c`/`redwood_wlfw.c` 在 raphael 上不能直接跑） |
| 固件通道 | MPSS（modem 处理器，TZ PAS image_id=4 装载）→ ICNSS 控制链 → ath10k-SNOC 主机数据通路 |
| 控制顺序 | 参考 `zundamon-miui-5.4` 的 `raphael_defconfig`（`CONFIG_ICNSS=y`）+ Linux `drivers/soc/qcom/{icnss,icnss_qmi}.c`；同树 icnss2 其他芯片分支不可套用 |
| 供电 | 五路 PMIC：四路常规 + **PM8150L L10C 3312 mV / HPM（外设 0x549，6.12 分支 `f48d0a8dfc4e`）**；关机逆序撤销 |
| 时钟 | WLAN 上电后经 AOSS QMP 发 `{class:clock, res:qdss, val:1}`（`0ad06c1c3f64`）；MPSS 停止前保持、退出撤票 |
| SMMU | 原生 SID `0x640`、mask 1 的上下文；MSA 区 `0x8bc00000 + 0x180000` 恒等映射，MPSS 停止后恢复旧配置 |
| 中断 | 12 路 level-high CE 中断 + NAPI |
| 交织禁止 | **不得同时启动 ath10k 与 qcacld 两个 WLAN 主机** |

## 二、本树已有的地基（直接复用）

- **TZ/PAS 通道**：`raphael_adreno.c` 的 SCMDMP/TZ 调用序 + R28 的 adsp PAS（`adreno_test_adsp`，T2 对照 ACCEPTED (0) 设备实证）——MPSS PAS(4) 是同一调用形态、不同 image_id；`raphael_adsp_fw.h` 的 .mdt 解析/签名链判词（M2 档案）整套可骑乘。
- **SPMI/PMIC**：`raphael_buttons.c` 已在真机读通 PM8150 的 SPMI 仲裁器（通道发现 + 传输 + GPIO），五路供电走同一仲裁器（PM8150L 的外设在另一总线路径，需按 L10C 外设 0x549 扩表）。
- **UFS 只读**：loader/kernel 全 GPT 可读（logdump 在案），modem 分区的 FAT16（firmware_mnt/image）可只读提取私有输入。
- **QMI/GLINK/QRTR**：`redwood_qmi.c`/`redwood_net.c` 在树（WCN6750 时代），协议骨架可参照，芯片语义要按 fork 重写。

## 三、fork 的内核资产（移植对照表）

| fork 文件 | 行数 | 内容 | 本树落点 |
|---|---|---|---|
| `xnu/files/bsd/dev/hakios.mpss.c` | 6395 | MPSS 签名装载（PAS 4）、SMEM、SMP2P、GLINK、QRTR、RMTFS、TFTP、PD mapper、PDR、五路供电、QDSS | `raphael_mpss.c` |
| `xnu/files/bsd/dev/hakios.wifi.c` | 5666 | ICNSS 控制链、MSA/SMMU、BDF 下载、capability、ath10k-SNOC 桥、12 路 CE 中断、NAPI、HTC/WMI | `raphael_icnss.c` + `raphael_ath10k.c` |
| `xnu/files/bsd/dev/hakios.qmi.c` | 963 | DMS（WLAN MAC，WLFW 0x33 提交）、QMI 骨架 | `raphael_wifi_qmi.c` |
| `wifi/lx/ath10k/**`（host.c + include + patches） | — | ath10k 主机栈（`tools/mk-ath10k.py` 钉死源码版本/SHA → `ath10k.o` 内核链接） | `tools/mk-ath10k.py` 原样移植 + `WIFI_ATH10K=1` 编译门 |

用户态：`com.hakios.wifi.plist` 对应本树 `com.redwood.wifi.plist`（已在卷里）；wpa_supplicant 侧 fork 用 `wifi_ask=1` 控制链 + 手机设置手输密码（本树 `redwood_apple80211.c` 的 Settings 对接照旧）。

## 四、私有输入（禁止提交/发布，与 fork 同律）

从本机 modem 分区**只读提取**，打包进 resident 镜像（RMTFS 写只改 RAM 副本）：

- `modem.mdt` + `.bxx`（MPSS 签名镜像）
- `wlanmdsp.mbn`（WLAN PD 固件，TFTP 导出 + RAM 内 `server_check.txt` 写读）
- `firmware-5.bin`（ath10k 固件）
- BDF：**`bd_f11cn.bin`**（本机 Recovery FDT `hwversion=4.9.0、hwc=CN` → 原生 daemon 的选择；GLOBAL 变体是 `bd_f11gl.bin`；**拒绝缺省 bdwlan.bin**，诊断必须显式 `wifi_bdf=`）
- NV 快照：`modemst1`/`modemst2`/`fsg`/`fsc`（RMTFS 用，共享区 MPSS 起动前授权、写请求只改私有 RAM 副本）

## 五、轮次计划（每轮一个变量，判读树先注册）

| 轮 | 变量 | 设备判据（logdump） | 回滚 |
|---|---|---|---|
| R35 | MPSS 装载轮：`raphael_mpss.c` + `wifi_step=1..8` 梯（SMEM→PAS 4→APCS→SMP2P→GLINK→QRTR→RMTFS→TFTP） | PAS answer 0 / 服务 0x45 发现 / GLINK 通道在册；对照 fork 的 step 判决行 | `wifi=0`（cmdline 单 token） |
| R36 | ICNSS + BDF 轮：MSA 授权、SMMU SID 0x640、五路供电、QDSS、BDF 下载、capability、校准报告、`0x21 FW_READY` | fork 的 `132122-run.log` 判据：`ICNSS WLAN firmware READY; MSA indication received`；early crash（`081950`：校准后 PD DOWN/UP）按码分诊 | 同上 |
| R37 | SNOC 数据轮：CE/HTC/WMI、扫描、关联、DHCP、TCP/HTTPS | `wifi_diag` + `raphael-wifi.jobs` 的地址/DNS/HTTPS 判据；WPA2 先行，WPA3 SAE 随后 | 同上 |
| R38 | 稳定性轮：toggle/rescan 恢复（fork 自己标注"连接仍不稳定，此提交保存已有实现，不声称已修复"） | 重复开关机扫描 | — |

已知风险：fork 实测 early crash 与开关后失扫描两类未决问题——移植时按其 `wifi_diag=1`（diagfwd_socket）与 microdump 检查项取证，不预期一次全绿。

## 六、纪律

- 私有输入一律本地提取、不进 git、不进 Release；发布物只含代码与方案。
- 每轮判读树预注册进 Release README；logdump 按 P3 纪律截断判读。
- `tools/mk-ath10k.py` 的源码下载地址/版本/SHA-256 全钉死；fork 的 `tools/check-wifi-*.py`（mdt/smem/doorbell/proxy/memory/rmtfs/tftp/pd/pdr/qrtr/icnss/dms/diag/dump/microdump/smmu，15 件）作为主机回归清单移植。
- "ASan/UBSan 通过不能替代实机扫描、重新关联及网络稳定性验证"（fork 原话，本轮继承）。
