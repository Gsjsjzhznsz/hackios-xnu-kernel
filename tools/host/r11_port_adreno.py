#!/usr/bin/env python3
"""Transform redwood_adreno.c (sc7280 / Adreno 642L a635) into raphael_adreno.c
(sm8150 / Adreno 640 a630-family).

Every change is address-, firmware- or flow-level, each with its source:
- block bases: mainline sm8150.dtsi (gpu@2c00000, gmu@2c6a000, gpucc@2c90000,
  adreno_smmu@2ca0000, gmu_pdc@b290000, gmu_pdc_seq@b490000)
- rscc = gmu + 0x23000 for the a630 family (a6xx_gmu.c: gmu->rscc = gmu->mmio + 0x23000)
- cmd-db: sm8150 reserved-memory cmd_db@85f20000
- DTCM base 0x40000 (a6xx_gmu_fw_load: not the a650 family's 0x10004000)
- chipid 0x06040001 (dt: qcom,adreno-640.1)
- firmware: a630_gmu.bin / a630_sqe.fw (the a630 family's files) + a640_zap.mdt/.b02
  (raphael's own zap, from the phone's Android)
- GPU pwrlevels: sm8150 gpu_opp_table (345/427/585 MHz at RPMH levels 64/80/128)
- PDC: NOT programmed by the always-on processor on sm8150 (pdc_in_aop is a650+ only):
  the full a6xx_load_pdc_ucode / a6xx_load_rsc_ucode, a630-family words, PDC sync
  address 0x30090 (a618/a640), TCS1/TCS3 banks, from a6xx_gmu.c verbatim
- cx_fal/cx_falnext interfaces: a650+ only (a6xx_gmu_fw_start skips them for a640)
"""
import re
import sys

SRC = "/home/z/my-project/work/hackios-xnu-kernel/xnu/files/bsd/dev/raphael_adreno.c"

s = open(SRC).read()
orig = s

def sub1(old, new, count=1, must=True):
    global s
    n = s.count(old)
    if n == 0 and must:
        print(f"MISS: {old[:80]!r}")
        sys.exit(1)
    s = s.replace(old, new, count if count > 0 else n)
    if must and n < count:
        print(f"UNDERCOUNT ({n}<{count}): {old[:80]!r}")
        sys.exit(1)

# ---- the opening comment: replace the redwood story with raphael's -----------------------------
head_end = s.index(" */", s.index("/*\n * The GPU of the POCO X5 Pro"))
new_head = """/*
 * The GPU of raphael (Mi 9 / K20 Pro, SM8150), an Adreno 640, woken step by step up to a
 * picture that it fills and the panel shows. Every address below belongs to this phone, and
 * nothing here is touched unless a boot-arg asks for it.
 *
 * This is redwood_adreno.c (the POCO X5 Pro driver of this tree, written for that phone's
 * Adreno 642L) adapted to the Adreno 640 of SM8150 -- the two are the same a6xx family with
 * the same GMU register map (a6xx_gmu.xml; the offsets are GPU-block-relative and carry
 * over), and what differs is the block bases, the firmware files, the a630-family flows
 * (the DTCM's address, the GPU RSC that lives inside the GMU's window, and the PDC that on
 * this SoC nobody has programmed for us), and the steps' numbers where the phone's own
 * tables say so. Every one of those changes says its source in a comment where it is made.
 *
 * Boot-arg gpu=1, on the phone and with the GPU's firmware in the small image: the GPU is
 * started while the system starts and kept for the window server to draw with (the service
 * RedwoodGPU; "the GPU kept on" below). Should a step fail, one line says which, and the
 * system draws in software as it does without the boot-arg.
 *
 * The steps are how this was found out, and they stay for looking: how much of the GPU a
 * kernel under Qualcomm's hypervisor may reach was not known, and an access the hypervisor
 * does not like ends the run, at once or a quarter of a minute later, and often without a
 * word. adreno=N performs steps 1 to N and stops; a step says what it is about to touch
 * before it touches it and what it found after, reads come before writes, and a step that
 * does not find what it needs ends the sequence instead of going on blind.
 *
 *   1  reads   what feeds the GPU from outside: its clocks in the global clock controller, and
 *              the names RPMh knows its supplies by
 *   2  reads   the fuses that say which GPU this is
 *   3  reads   the GPU's own clock controller, as the boot loader left it
 *   4  writes  the "CX" power switch on: the half of the GPU block that is not the 3D core --
 *              the power management processor ("GMU"), the bus interface, the GPU's SMMU
 *   5  writes  the clocks of that half on, and measures with the SoC's frequency counter whether
 *              they run
 *   6  reads   the first words from the GPU block itself: registers of the GMU (among them a
 *              counter that should be seen counting) and of the bus interface
 *   7  reads   the GMU's own channel to RPMh
 *   8  reads   the SMMU in front of the GPU: its ID registers, and which stream goes where
 *   9  reads   that SMMU's first translation context
 *  10  writes  two words into the GMU's memory and reads them back: the way its firmware goes
 *  11  asks    TrustZone questions that pass no memory: whether it takes calls from this kernel,
 *              and which firmware images it says it authenticates
 *  12  asks    TrustZone to check the signature of the GPU's "zap shader", the signed image
 *              without which the 3D core stays locked in secure mode: its header goes from the
 *              small image (fw/a640_zap.mdt) into memory of ours that TrustZone is pointed at
 *  13  writes  page tables for the memory the GMU's firmware asks for, and the SMMU: a
 *              translation context with those tables, and the GMU's requests sent through it
 *  14  writes  the GMU's firmware (fw/a630_gmu.bin) into its own memory and into that memory
 *  15  writes  the GMU's configuration, and lets its processor out of reset: the firmware runs
 *  16  writes  the GPU's part of the always-on sequencer (the PDC, which on this SoC nobody
 *              has programmed for us: its uCode and its TCS banks, the a630 family's words)
 *              and the GMU's connection to RPMh; starts its message queues ("HFI")
 *  17  talks   to the GMU: the tables it needs, and the request to power the 3D core, whose
 *              registers are then read
 *  18  writes  the 3D core's translation context, with its ring and its microcode
 *              (fw/a630_sqe.fw) in it
 *  19  asks    TrustZone to load the zap shader (and first to give up the one a kernel before
 *              this one may have left; the stop at the end of a run asks for that too)
 *  20  writes  the 3D core's registers; starts its command processor and gives it its first
 *              command
 *  21  commands the 3D core out of secure mode, and to write a word into a page of ours
 *  22  commands a picture filled with three colours, and shows it on the panel
 * When the steps are over, what they started is stopped again, in reverse, and the CX half
 * switched off: nothing of the GPU block outlives the run (its SMMU contexts point into this
 * kernel's memory), and the next one starts from what the phone's own start leaves.
 *
 * The firmware is the phone's own: the GMU's and the SQE's from Linux's linux-firmware
 * (qcom/a630_gmu.bin, qcom/a630_sqe.fw -- the a630 family's files, which the a640 shares),
 * the zap shader from the phone's Android (a640_zap.mdt + a640_zap.b02), all in fw/ of the
 * small image. There is no register that holds the chip ID (0x06040001, "qcom,adreno-640.1"
 * in the device tree): the fuses of step 2 are all the hardware says about itself, and the
 * GMU's version is a property of its firmware.
 *
 * More boot-args:
 *   adreno=N        the first N steps only, then (unless N says a picture) everything started
 *                   is stopped again; the default with gpu=1 is all of them
 *   gpu=1           all the steps, and the GPU kept for the system to draw with
 *   adreno_keep=1   with adreno=21: as if gpu=1 (the boot-arg the window server's side knows)
 *   gpu_recover=1   a submission the core never finishes costs the run nothing (see below)
 *   gpu_va=N        how addresses in the GPU are described (0 none, 1 lines, 2 also guarded)
 *   adreno_speed=N  of the 3D core's speeds, the Nth (1 the lowest; the default is the second)
 *   adreno_dirty=1  with gpu=1: every frame is written in full, not only what changed
 *   adreno_pause=N  milliseconds between the steps (as this was found out; 0 the default)
 *   display_probe=1 before any step: read what the firmware left the display controller
 *                   doing (the registers of the DSI interface and its two frame counters)
 *
 * What the boot loader leaves: the CX power switch off, the GPU's registers dead until it is
 * on. The firmware's own display setup (cont_splash) stays running the panel meanwhile -- the
 * loader's screen is the same one.
 *
 * Register layouts: a6xx.xml / a6xx_gmu.xml (Mesa's freedreno database, which Linux's msm
 * driver builds from), drivers/clk/qcom/gpucc-sm8150.c, drivers/soc/qcom/cmd-db.c,
 * drivers/firmware/qcom/qcom_scm{,-smc}.c, drivers/soc/qcom/mdt_loader.c, a6xx_gmu.c and
 * a6xx_gpu.c with the a630 family's branches, and the phone's own device tree
 * (sm8150-xiaomi-raphael: gpu@2c00000, gmu@2c6a000 with gmu_pdc@b290000 and
 * gmu_pdc_seq@b490000, gpucc@2c90000, adreno_smmu@2ca0000, cmd_db@85f20000).
 */
"""
s = new_head + s[head_end + 3:]

# ---- block bases --------------------------------------------------------------------------------
sub1('static struct block gpucc = { "gpucc", 0x03d90000, 0x9000 };',
     'static struct block gpucc = { "gpucc", 0x02c90000, 0x9000 };    /* dt sm8150: clock-controller@2c90000 */')
sub1('static struct block gmu = { "gmu", 0x03d6a000, 0x34000 };',
     'static struct block gmu = { "gmu", 0x02c6a000, 0x30000 };      /* dt sm8150: qcom,gmu@2c6a000 ("gmu") */')
sub1('static struct block gbif = { "gbif", 0x03d00000 + 4 * 0x3c00, 0x1000 };',
     'static struct block gbif = { "gbif", 0x02c00000 + 4 * 0x3c00, 0x1000 };   /* the GPU block\'s own GBIF */')
sub1('static struct block cx_misc = { "cx_misc", 0x03d9e000, 0x1000 };',
     'static struct block cx_misc = { "cx_misc", 0x02c9e000, 0x1000 };')
sub1('static struct block rscc = { "rscc", 0x03de0000, 0x2000 };',
     'static struct block rscc = { "rscc", 0x02c8d000, 0x2000 };     /* the a630 family\'s GPU RSC: inside the GMU\'s window at +0x23000 (a6xx_gmu.c) */')
sub1('static struct block smmu = { "smmu", 0x03da0000, 0x20000 };',
     'static struct block smmu = { "smmu", 0x02ca0000, 0x20000 };    /* dt sm8150: adreno_smmu@2ca0000 */')
sub1('static struct block gpu = { "gpu", 0x03d00000, 0x40000 };',
     'static struct block gpu = { "gpu", 0x02c00000, 0x40000 };      /* dt sm8150: gpu@2c00000 ("kgsl_3d0_reg_memory") */')
sub1('#define CMD_DB_PHYS             0x80860000UL',
     '#define CMD_DB_PHYS             0x85f20000UL   /* dt sm8150: reserved-memory cmd_db@85f20000 */')

# ---- PDC: on sm8150 nobody programs it for us; add the sequence region ---------------------------
sub1('static struct block pdc = { "pdc", 0x0b290000, 0x10000 };       /* dt: qcom,gmu "kgsl_gmu_pdc_cfg" */',
     'static struct block pdc = { "pdc", 0x0b290000, 0x10000 };       /* dt sm8150: qcom,gmu "gmu_pdc" */\n'
     'static struct block pdc_seq = { "pdc_seq", 0x0b490000, 0x10000 };       /* dt sm8150: qcom,gmu "gmu_pdc_seq": the sequencer\'s own program */')

# ---- the DTCM's address and the chip id ------------------------------------------------------------
sub1('#define GMU_DTCM_ADDRESS        0x10004000u     /* a6xx_gmu_fw_load: the 650 family\'s */',
     '#define GMU_DTCM_ADDRESS        0x00040000u     /* a6xx_gmu_fw_load: every family but the 650\'s */')
sub1('#define GMU_CHIP                0x06035000      /* dt qcom,chipid 0x06030500 the way the firmware wants it */',
     '#define GMU_CHIP                0x06040001      /* dt: "qcom,adreno-640.1" */')
sub1('#define GPU_CHIP_ID             0x06030500ull   /* dt: qcom,chipid */',
     '#define GPU_CHIP_ID             0x06040001ull   /* dt: "qcom,adreno-640.1" */')

# ---- firmware file names ---------------------------------------------------------------------------
sub1('header = image_file("fw", "a660_zap.mdt", &size);',
     'header = image_file("fw", "a640_zap.mdt", &size);')
sub1('announce("find the zap shader\'s header, fw/a660_zap.mdt, in the small image that came with the kernel");',
     'announce("find the zap shader\'s header, fw/a640_zap.mdt, in the small image that came with the kernel");')
sub1('gmu_firmware = image_file("fw", "a660_gmu.bin", &gmu_firmware_size);',
     'gmu_firmware = image_file("fw", "a630_gmu.bin", &gmu_firmware_size);')
sub1('announce("find the GMU\'s firmware, fw/a660_gmu.bin, in the small image, and make the memory it asks for");',
     'announce("find the GMU\'s firmware, fw/a630_gmu.bin, in the small image, and make the memory it asks for");')
sub1('microcode = image_file("fw", "a660_sqe.fw", &size);',
     'microcode = image_file("fw", "a630_sqe.fw", &size);')
sub1('segment = image_file("fw", "a660_zap.b02", &size);',
     'segment = image_file("fw", "a640_zap.b02", &size);')
sub1('static const char *const files[] = { "a660_gmu.bin", "a660_sqe.fw", "a660_zap.mdt", "a660_zap.b02" };',
     'static const char *const files[] = { "a630_gmu.bin", "a630_sqe.fw", "a640_zap.mdt", "a640_zap.b02" };')
s = s.replace("fw/a660_gmu.bin", "fw/a630_gmu.bin").replace("fw/a660_sqe.fw", "fw/a630_sqe.fw")
s = s.replace("a660_gmu.bin", "a630_gmu.bin").replace("a660_sqe.fw", "a630_sqe.fw")
s = s.replace("a660_zap.mdt", "a640_zap.mdt").replace("a660_zap.b02", "a640_zap.b02")

# ---- the 3D core's speeds: sm8150's gpu_opp_table ---------------------------------------------------
sub1('\t} speeds[] = { { 0, 0 }, { 315000, 64 }, { 450000, 128 }, { 550000, 192 } };',
     '\t} speeds[] = { { 0, 0 }, { 345000, 64 }, { 427000, 80 }, { 585000, 128 } };   /* dt sm8150 gpu_opp_table: 345/427/585 MHz at SVS/SVS_L1/NOM */')
sub1('static const uint32_t program[5] = { 0xeaaae5a0, 0xe1a1ebab, 0xa2e0a581, 0xecac82e2, 0x0020edad };',
     'static const uint32_t program[5] = { 0xa7a506a0, 0xa1e6a6e7, 0xa2e081e1, 0xe9a982e2, 0x0020e8a8 };   /* the a630 family\'s (a6xx_gmu.c) */')

# ---- cx_fal interfaces: the a650 family's only (a6xx_gmu_fw_start) ---------------------------------
sub1('\tput(&gmu, "cx_falnext_intf", GMU_CX_FALNEXT_INTF, 1);\n\tput(&gmu, "cx_fal_intf", GMU_CX_FAL_INTF, 1);\n',
     '\t/* the "FAL10" veto interfaces are the a650 family\'s (a6xx_gmu_fw_start): not written here */\n')

# ---- the PDC step: the full a630-family load (on sm8150 nobody has done it for us) ------------------
old_step = '''\tannounce("enable the GPU's part of the always-on sequencer (pdc, 0xb294500 and 0xb294520: a part of the SoC not "
\t    "touched before)");
\tget(&pdc, "gpu_enable", PDC_GPU_ENABLE);
\tget(&pdc, "gpu_seq_start_addr", PDC_GPU_SEQ_START_ADDR);
\tput(&pdc, "gpu_seq_start_addr", PDC_GPU_SEQ_START_ADDR, 0);
\tput(&pdc, "gpu_enable", PDC_GPU_ENABLE, 0x80000001);
\tget(&pdc, "gpu_enable", PDC_GPU_ENABLE);
'''
new_step = '''\t/*
\t * On sm8150 the always-on processor has NOT programmed the GPU's part of the PDC (that
\t * is the a650 family's "pdc_in_aop" luxury): the whole of the vendor's a6xx_load_pdc_ucode
\t * and a6xx_load_rsc_ucode, a630 family's words, is here (a6xx_gmu.c). First the
\t * sequencer's own program, into its memory at 0xb490000.
\t */
\t{
\t\tstatic const uint32_t pdc_program[5] = { 0xfebea1e1, 0xa5a4a3a2, 0x8382a6e0, 0xbce3e284, 0x002081fc };

\t\tannounce("program the GPU's part of the always-on sequencer: its own memory (pdc_seq, 0xb490000), then its "
\t\t    "command banks (pdc, TCS1 and TCS3)");
\t\tfor (unsigned i = 0; i < 5; i++) {
\t\t\tput(&pdc_seq, "gpu_seq_mem", PDC_GPU_SEQ_MEM_0 + 4 * i, pdc_program[i]);
\t\t}
\t\t/* TCS1: the three commands of the power-up bank */
\t\tput(&pdc, "tcs1_cmd_enable_bank", PDC_TCS1_CMD_ENABLE, 7);
\t\tput(&pdc, "tcs1_cmd_wait_for_cmpl", PDC_TCS1_CMD_WAIT, 0);
\t\tput(&pdc, "tcs1_control", PDC_TCS1_CONTROL, 0);
\t\tput(&pdc, "tcs1_cmd0_msgid", PDC_TCS1_CMD0_MSGID, 0x10108);
\t\tput(&pdc, "tcs1_cmd0_addr", PDC_TCS1_CMD0_ADDR, 0x30010);
\t\tput(&pdc, "tcs1_cmd0_data", PDC_TCS1_CMD0_DATA, 1);
\t\tput(&pdc, "tcs1_cmd1_msgid", PDC_TCS1_CMD0_MSGID + 4, 0x10108);
\t\tput(&pdc, "tcs1_cmd1_addr", PDC_TCS1_CMD0_ADDR + 4, 0x30000);
\t\tput(&pdc, "tcs1_cmd1_data", PDC_TCS1_CMD0_DATA + 4, 0);
\t\tput(&pdc, "tcs1_cmd2_msgid", PDC_TCS1_CMD0_MSGID + 8, 0x10108);
\t\tput(&pdc, "tcs1_cmd2_addr", PDC_TCS1_CMD0_ADDR + 8, PDC_SYNC_ADDRESS);
\t\tput(&pdc, "tcs1_cmd2_data", PDC_TCS1_CMD0_DATA + 8, 0);
\t\t/* TCS3: the three commands of the power-down bank */
\t\tput(&pdc, "tcs3_cmd_enable_bank", PDC_TCS3_CMD_ENABLE, 7);
\t\tput(&pdc, "tcs3_cmd_wait_for_cmpl", PDC_TCS3_CMD_WAIT, 0);
\t\tput(&pdc, "tcs3_control", PDC_TCS3_CONTROL, 0);
\t\tput(&pdc, "tcs3_cmd0_msgid", PDC_TCS3_CMD0_MSGID, 0x10108);
\t\tput(&pdc, "tcs3_cmd0_addr", PDC_TCS3_CMD0_ADDR, 0x30010);
\t\tput(&pdc, "tcs3_cmd0_data", PDC_TCS3_CMD0_DATA, 2);
\t\tput(&pdc, "tcs3_cmd1_msgid", PDC_TCS3_CMD0_MSGID + 4, 0x10108);
\t\tput(&pdc, "tcs3_cmd1_addr", PDC_TCS3_CMD0_ADDR + 4, 0x30000);
\t\tput(&pdc, "tcs3_cmd1_data", PDC_TCS3_CMD0_DATA + 4, 3);   /* the a630 family's (a618/a619/a650 say 2) */
\t\tput(&pdc, "tcs3_cmd2_msgid", PDC_TCS3_CMD0_MSGID + 8, 0x10108);
\t\tput(&pdc, "tcs3_cmd2_addr", PDC_TCS3_CMD0_ADDR + 8, PDC_SYNC_ADDRESS);
\t\tput(&pdc, "tcs3_cmd2_data", PDC_TCS3_CMD0_DATA + 8, 3);
\t\tput(&pdc, "gpu_seq_start_addr", PDC_GPU_SEQ_START_ADDR, 0);
\t\tput(&pdc, "gpu_enable", PDC_GPU_ENABLE, 0x80000001);
\t\tget(&pdc, "gpu_enable", PDC_GPU_ENABLE);
\t}
'''
sub1(old_step, new_step)

# the RSC-side hidden TCS: the a630 family's second command is 0x80000000, not the a650's 0x80000021
sub1('\tput(&rscc, "hidden_tcs_cmd2_data", RSCC_HIDDEN_TCS_CMD0_DATA + 4 * 4, 0x80000021);    /* a650 and later */',
     '\tput(&rscc, "hidden_tcs_cmd2_data", RSCC_HIDDEN_TCS_CMD0_DATA + 4 * 4, 0x80000000);    /* every family but the a740\'s (a6xx_gmu.c) */')
# the "already programmed" check looked for the a650 words; on this phone nothing has programmed it
sub1('\tannounce("give the GMU\'s RPMh channel its sleep and wake program (rscc, 0x3de0000), unless it has it");\n'
     '\tif (get(&rscc, "seq_mem_0_drv0", RSCC_SEQ_MEM_0_DRV0) == program[0] &&\n'
     '\t    get(&rscc, "hidden_tcs_cmd2_data", RSCC_HIDDEN_TCS_CMD0_DATA + 4 * 4) == 0x80000021) {\n'
     '\t\tgoto programmed;\n'
     '\t}\n',
     '\tannounce("give the GMU\'s RPMh channel its sleep and wake program (the GPU RSC, inside the GMU\'s window at " \n'
     '\t    "+0x23000), unless it has it");\n'
     '\tif (get(&rscc, "seq_mem_0_drv0", RSCC_SEQ_MEM_0_DRV0) == program[0] &&\n'
     '\t    get(&rscc, "hidden_tcs_cmd2_data", RSCC_HIDDEN_TCS_CMD0_DATA + 4 * 4) == 0x80000000) {\n'
     '\t\tgoto programmed;\n'
     '\t}\n')

# ---- the PDC register names the new step uses ------------------------------------------------------
sub1('#define PDC_GPU_ENABLE          (4 * 0x1140)\n'
     '#define PDC_GPU_SEQ_START_ADDR  (4 * 0x1148)\n',
     '#define PDC_GPU_ENABLE          (4 * 0x1140)\n'
     '#define PDC_GPU_SEQ_START_ADDR  (4 * 0x1148)\n'
     '#define PDC_TCS1_CONTROL        (4 * 0x1572)    /* a6xx.xml, domain A6XX_PDC */\n'
     '#define PDC_TCS1_CMD_ENABLE     (4 * 0x1573)\n'
     '#define PDC_TCS1_CMD_WAIT       (4 * 0x1574)\n'
     '#define PDC_TCS1_CMD0_MSGID     (4 * 0x1575)\n'
     '#define PDC_TCS1_CMD0_ADDR      (4 * 0x1576)\n'
     '#define PDC_TCS1_CMD0_DATA      (4 * 0x1577)\n'
     '#define PDC_TCS3_CONTROL        (4 * 0x15d6)\n'
     '#define PDC_TCS3_CMD_ENABLE     (4 * 0x15d7)\n'
     '#define PDC_TCS3_CMD_WAIT       (4 * 0x15d8)\n'
     '#define PDC_TCS3_CMD0_MSGID     (4 * 0x15d9)\n'
     '#define PDC_TCS3_CMD0_ADDR      (4 * 0x15da)\n'
     '#define PDC_TCS3_CMD0_DATA      (4 * 0x15db)\n'
     '#define PDC_GPU_SEQ_MEM_0       (4 * 0x0)       /* a6xx.xml, domain A6XX_PDC_GPU_SEQ: at the head of pdc_seq */\n'
     '#define PDC_SYNC_ADDRESS        0x30090u        /* the a630 family\'s sync register, as the PDC\'s commands name it (a618/a640: 0x30090) */\n')

# ---- announce strings that name the old phone's addresses -------------------------------------------
s = s.replace("0x3d6d000", "0x2c6d000").replace("0x3d71000", "0x2c71000")
s = s.replace("(0x3d00000", "(0x2c00000").replace(" 0x3d00000 ", " 0x2c00000 ")
s = s.replace("0x3de0000", "0x2c8d000")
s = s.replace("at 0x3d6a000", "at 0x2c6a000")

# ---- the tsens block: sm8150's GPU sensors ----------------------------------------------------------
sub1('static struct block tsens = { "tsens1", 0x0c265000, 0x200 };',
     'static struct block tsens = { "tsens1", 0x0c265000, 0x200 };    /* sm8150\'s tsens1 (GPU), as the redwood\'s was: a read, for the once-a-minute line */')

# ---- the redwood panel story in the display probe section: raphael's panel -------------------------
s = s.replace("qcom,mdss_dsi_m20_36_02_0a_dsc_vid", "samsung,ams639rq08 (raphael's AMOLED)")
s = s.replace("1080 x 2400 at 120", "1080 x 2340 at 60")

# ---- the speedbin/gpu speed note --------------------------------------------------------------------
s = s.replace("dt: qcom,gpu-pwrlevels: 315, 450, 550 MHz at \"levels\" 64, 128, 192",
              "dt sm8150 gpu_opp_table: 345, 427, 585 MHz at RPMH levels 64, 80, 128")

open(SRC, "w").write(s)
print(f"raphael_adreno.c transformed: {len(orig)} -> {len(s)} bytes")
print("remaining 'a660' references:", s.count("a660"))
print("remaining '0x03d' references:", s.count("0x03d"))
print("remaining '315000':", s.count("315000"))
