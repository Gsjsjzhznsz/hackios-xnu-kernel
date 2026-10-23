# tools/host — 会话侧私有脚本（随轮入库）

**规矩（R8 起立，用户指令）：每轮的 worklog、私有脚本与工具，随该轮
一起提交进本仓库。** 会话丢了也要能凭仓库独立恢复；判读分析、取证
方法、监控工具，一律不许只活在聊天记录或 `/home/z` 会话目录里。

| 脚本 | 用途 | 首产轮次 |
|---|---|---|
| `r8-kernel-disasm.py` | 对**已交付的** prebuilt 内核反汇编任意符号（Mach-O symtab 直读 + capstone），回答"源码里的改动到底进没进刷出去的二进制"。R8 用它定案：`_gic_v3_irq_cpu_init` 早已含 `GICR_IGROUPR0 \|= (1<<27)`，就在落地了的 ISENABLER0 写前四条指令——非安全组写被 GIC 丢弃、读恒 0（安全态 DS=0），R7 的 "group 0" 是盲读 | R8 |
| `r8-watch.py` | 轮次监视器：轮询 Actions run；成功且 Release 出现后，按 名字+尺寸+sha256 三重校验下载资产到指定目录，并附上该轮 FLASH 文档。`python3 r8-watch.py RUN_ID TAG DEST_DIR [max_wait]`；令牌走 GITHUB_TOKEN 或本仓库 remote 内嵌值 | R8（模板适用于后续各轮） |

各轮新增 host 侧脚本时：照此表登记一行，docstring 里写清它裁决了什么。
