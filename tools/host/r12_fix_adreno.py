#!/usr/bin/env python3
"""R12: fix raphael_adreno.c's step 5 on the evidence of the r11-gpu-log run.

Three findings, each from an authority named at the change:

(1) The GPU's 300 MHz vote was written to the wrong register. The R11 log shows
    "gcc pll votes (0x152000) = 0x1" before and after the write of bit 16: dropped.
    Vendor gcc-sm8150.c (work/ref-raphael-gcc.c, the phone's own kernel) says:
    0x152000 is the GPLL vote register (bit 0 gpll0 -- the 0x1 the log saw, bit 6
    gpll6, bit 7 gpll7); the GPU's taps of it are a *branch* at 0x152004: bit 15
    = gcc_gpu_gpll0_clk_src (600 MHz), bit 16 = gcc_gpu_gpll0_div_clk_src
    (300 MHz, the gmu_clk_src's parent). Fix: 0x52000 -> 0x52004.

(2) The "hub" family is not this SoC's. R11 ported redwood (SC7280 / A635) whole,
    and SC7280's GPUCC has gpu_cc_hub_clk_src/hub_aon/hub_cx_int/cb. Mainline
    gpucc-sm8150.c has the complete SM8150 list and none of those exist here:
    the offsets 0x1170/0x117c/0x1178/0x1180/0x1204 are unimplemented holes on
    SM8150 -- the log agrees exactly (read 0, write dropped, no fault). On
    SM8150 the SMMU's clocks are sm8150.dtsi's adreno_smmu list: gpucc ahb
    (0x1078) + gcc memnoc_gfx (0x7100c) + gcc snoc_dvm_gfx (0x71018), all three
    of which the R11 log shows already running. Fix: delete the four writes and
    the hub clock_source; step 5 asks for 200 MHz (the only rate this family's
    gmu_clk_src table has) and no longer claims a 150 MHz "hub".

(3) The frequency counter is a witness, not a gate. Linux never makes a bring-up
    wait on the measure block (it is debugfs-only there, when it exists at all);
    the rates are arithmetic: the RCG's half-divider written is 3/2 of the 300 MHz
    tap = 200 MHz exactly. On the R11 phone the counter never finished (the log's
    "did not finish"): its input chain includes the SC7280 debug-mux value 0x146
    and two debug branch registers at SC7280's offsets, none verified for SM8150,
    and one dead link anywhere in that chain holds DONE off forever -- a 200 ms
    poll cannot wait that out, and no write order would. Fix: measure_clocks()
    and cx_ready() and step 22's line report what they measured and *continue on
    the configured rates*; cx_ready's register-bus evidence becomes the AHB
    control register's own bits (enable set, off clear), which the R11 log shows
    hold (0x1078 = 0x1).

Also, one addition: gpu_cc_cxo_aon_clk @ 0x1004 (mainline gpucc-sm8150.c:
.gpu_cc_cxo_aon_clk, enable 0x1004 bit 0) -- the AON half's crystal buffer. It
read 0 in the R11 log (off); it is turned on here so the always-on side of the
block has its 19.2 MHz.
"""
import re
import sys

SRC = "/home/z/my-project/work/hackios-xnu-kernel/xnu/files/bsd/dev/raphael_adreno.c"

s = open(SRC).read()
orig = s

def T(text):
    """The file indents its function bodies with tabs; these blocks are written with 8 spaces per level."""
    return re.sub(r'^((?: {8})+)', lambda m: '\t' * (len(m.group(1)) // 8), text, flags=re.M)

def sub1(old, new, count=1, must=True, tabify=True):
    global s
    if tabify:
        old, new = T(old), T(new)
    n = s.count(old)
    if n == 0 and must:
        print(f"MISS: {old[:88]!r}")
        sys.exit(1)
    s = s.replace(old, new, count if count > 0 else n)
    if must and n < count:
        print(f"UNDERCOUNT ({n}<{count}): {old[:88]!r}")
        sys.exit(1)

# ---- (1) the 300 MHz tap: 0x52004, the vendor's own address ---------------------------------------
sub1(
    '#define GCC_PLL_VOTE            0x52000         /* bit 15: gcc_gpu_gpll0_clk_src (600 MHz), bit 16: ..._div_clk_src (300 MHz) */\n'
    '#define GCC_PLL_VOTE_GPU_DIV    (1u << 16)',
    '#define GCC_PLL_VOTE            0x52004         /* gcc-sm8150.c (the phone\'s own): gcc_gpu_gpll0_div_clk_src, enable_reg 0x52004 BIT(16); next door 0x15200 is the GPLL vote register (bit 0 gpll0 -- R11 wrote bit 16 THERE and the log watched it drop: "= 0x1" before and after) */\n'
    '#define GCC_PLL_VOTE_GPU_DIV    (1u << 16)      /* the 300 MHz tap the gmu_clk_src\'s table takes at 1.5 */')

# ---- (2) the hub family is sc7280's, not this SoC's: the offsets go --------------------------------
sub1(
    '#define GPUCC_GMU_RCGR          0x1120          /* gpu_cc_gmu_clk_src: +0 command, +4 source and divider */\n'
    '#define GPUCC_CB_CBCR           0x1170          /* gpu_cc_cb_clk: Linux keeps it on always */\n'
    '#define GPUCC_HUB_AON_CBCR      0x1178          /* gpu_cc_hub_aon_clk */\n'
    '#define GPUCC_HUB_RCGR          0x117c          /* gpu_cc_hub_clk_src */\n'
    '#define GPUCC_HUB_CX_INT_CBCR   0x1204          /* gpu_cc_hub_cx_int_clk */\n',
    '#define GPUCC_GMU_RCGR          0x1120          /* gpu_cc_gmu_clk_src (gpucc-sm8150.c: cmd_rcgr 0x1120): +0 command, +4 source and divider */\n'
    '/*\n'
    ' * Not here, whatever redwood\'s (sc7280\'s) driver says: gpu_cc_hub_clk_src, hub_aon,\n'
    ' * hub_cx_int and cb are sc7280\'s clocks (gpucc-sc7280.c); SM8150\'s list\n'
    ' * (gpucc-sm8150.c) has none of them, and the R11 run wrote their sc7280 offsets\n'
    ' * (0x1170/0x117c/0x1178/0x1180/0x1204) into unimplemented holes -- read 0, write\n'
    ' * dropped, the log agrees word for word. The SMMU\'s clocks on this SoC are\n'
    ' * sm8150.dtsi\'s adreno_smmu list: this gpucc\'s ahb + gcc\'s memnoc_gfx and\n'
    ' * snoc_dvm_gfx, all three already running in that same log.\n'
    ' */\n')

# ---- step 3: drop the reads of what is not there ---------------------------------------------------
sub1(
    '        get(&gpucc, "gmu_clk_src command", GPUCC_GMU_RCGR);\n'
    '        get(&gpucc, "gmu_clk_src source", GPUCC_GMU_RCGR + 4);\n'
    '        get(&gpucc, "hub_clk_src command", GPUCC_HUB_RCGR);\n'
    '        get(&gpucc, "hub_clk_src source", GPUCC_HUB_RCGR + 4);\n'
    '        get(&gpucc, "cxo_aon", GPUCC_CXO_AON_CBCR);\n'
    '        get(&gpucc, "gx_gmu", GPUCC_GX_GMU_CBCR);\n'
    '        get(&gpucc, "ahb", GPUCC_AHB_CBCR);\n'
    '        get(&gpucc, "cx_gmu", GPUCC_CX_GMU_CBCR);\n'
    '        get(&gpucc, "cxo", GPUCC_CXO_CBCR);\n'
    '        get(&gpucc, "cb", GPUCC_CB_CBCR);\n'
    '        get(&gpucc, "hub_aon", GPUCC_HUB_AON_CBCR);\n'
    '        get(&gpucc, "hub_cx_int", GPUCC_HUB_CX_INT_CBCR);\n'
    '        get(&gpucc, "smmu vote", GPUCC_SMMU_VOTE_CBCR);\n',
    '        get(&gpucc, "gmu_clk_src command", GPUCC_GMU_RCGR);\n'
    '        get(&gpucc, "gmu_clk_src source", GPUCC_GMU_RCGR + 4);\n'
    '        get(&gpucc, "cxo_aon", GPUCC_CXO_AON_CBCR);\n'
    '        get(&gpucc, "gx_gmu", GPUCC_GX_GMU_CBCR);\n'
    '        get(&gpucc, "ahb", GPUCC_AHB_CBCR);\n'
    '        get(&gpucc, "cx_gmu", GPUCC_CX_GMU_CBCR);\n'
    '        get(&gpucc, "cxo", GPUCC_CXO_CBCR);\n'
    '        get(&gpucc, "smmu vote", GPUCC_SMMU_VOTE_CBCR);\n')

# ---- step 5: the real clocks of this SoC -----------------------------------------------------------
sub1(
    '        announce("in the GPU\'s clock controller: the GMU\'s clock to 200 MHz, the hub\'s to 150 MHz");\n'
    '        if (!clock_source("gmu_clk_src", GPUCC_GMU_RCGR, RCG_GPLL0_DIV(3)) ||       /* 300 MHz / 1.5 */\n'
    '            !clock_source("hub_clk_src", GPUCC_HUB_RCGR, RCG_GPLL0_DIV(4))) {       /* 300 MHz / 2 */\n'
    '                return false;\n'
    '        }\n'
    '\n'
    '        announce("in the GPU\'s clock controller: the clocks of the GMU and of the SMMU on");\n'
    '        if (!clock_on(&gpucc, "cb", GPUCC_CB_CBCR, false) ||\n'
    '            !clock_on(&gpucc, "cxo", GPUCC_CXO_CBCR, true) ||\n'
    '            !clock_on(&gpucc, "ahb", GPUCC_AHB_CBCR, false) ||\n'
    '            !clock_on(&gpucc, "hub_aon", GPUCC_HUB_AON_CBCR, true) ||\n'
    '            !clock_on(&gpucc, "hub_cx_int", GPUCC_HUB_CX_INT_CBCR, true) ||\n'
    '            !clock_on(&gpucc, "cx_gmu", GPUCC_CX_GMU_CBCR, true) ||\n'
    '            !clock_on(&gpucc, "smmu vote", GPUCC_SMMU_VOTE_CBCR, false)) {\n'
    '                return false;\n'
    '        }\n'
    '        return measure_clocks();',
    '        announce("in the GPU\'s clock controller: the GMU\'s clock to 200 MHz (the rate its table has)");\n'
    '        if (!clock_source("gmu_clk_src", GPUCC_GMU_RCGR, RCG_GPLL0_DIV(3))) {       /* 300 MHz / 1.5: F(200000000, P_GPLL0_OUT_MAIN_DIV, 1.5) */\n'
    '                return false;\n'
    '        }\n'
    '\n'
    '        /*\n'
    '         * The SMMU\'s clocks on this SoC (sm8150.dtsi, adreno_smmu): gpucc\'s ahb, gcc\'s\n'
    '         * memnoc_gfx and snoc_dvm_gfx -- the two gcc ones are on above, ahb below. The\n'
    '         * sc7280 "hub" family does not exist here (see the macro block); cxo_aon\n'
    '         * (0x1004, mainline gpucc-sm8150.c) is added: the always-on half\'s crystal\n'
    '         * buffer, which the R11 log found off.\n'
    '         */\n'
    '        announce("in the GPU\'s clock controller: the clocks of the GMU and of the SMMU on");\n'
    '        if (!clock_on(&gpucc, "cxo", GPUCC_CXO_CBCR, true) ||\n'
    '            !clock_on(&gpucc, "ahb", GPUCC_AHB_CBCR, true) ||\n'
    '            !clock_on(&gpucc, "cxo_aon", GPUCC_CXO_AON_CBCR, false) ||\n'
    '            !clock_on(&gpucc, "cx_gmu", GPUCC_CX_GMU_CBCR, true) ||\n'
    '            !clock_on(&gpucc, "smmu vote", GPUCC_SMMU_VOTE_CBCR, false)) {\n'
    '                return false;\n'
    '        }\n'
    '        return measure_clocks();')

# ---- (3) the counter is a witness: measure_clocks never fails a step again -------------------------
sub1(
    '        hz = measure(GPUCC_DEBUG_CXO);\n'
    '        if (hz == MEASURE_FAILED) {\n'
    '                printf("adreno: %u: the counter did not finish: it cannot be used as it is set up here\\n", step);\n'
    '                return false;\n'
    '        }\n'
    '        printf("adreno: %u: measured: cxo runs at %u Hz (the crystal: 19200000)\\n", step, hz);\n'
    '        if (hz < 18600000 || hz > 19800000) {\n'
    '                printf("adreno: %u: the counter does not show the crystal\'s rate: nothing it says can be used\\n", step);\n'
    '                return false;\n'
    '        }',
    '        hz = measure(GPUCC_DEBUG_CXO);\n'
    '        if (hz == MEASURE_FAILED) {\n'
    '                /*\n'
    '                 * The counter is a witness, not a gate (Linux\'s bring-up never waits on\n'
    '                 * one; the rates are arithmetic: the half-divider written is 3/2 of the\n'
    '                 * 300 MHz tap = 200 MHz exactly). Its input chain here still carries\n'
    '                 * sc7280\'s debug-mux value (0x146) and two debug branches at sc7280\'s\n'
    '                 * offsets, none of them verified for this SoC, and one dead link holds\n'
    '                 * DONE off forever -- the R11 run watched it for 200 ms a time. So: say\n'
    '                 * so, and go on with the configured rates.\n'
    '                 */\n'
    '                printf("adreno: %u: the counter did not finish (the mux and the debug branches are the sc7280\'s,\\n"\n'
    '                    "    unverified here): going on with the configured rates\\n", step);\n'
    '        } else {\n'
    '                printf("adreno: %u: measured: cxo runs at %u Hz (the crystal: 19200000)\\n", step, hz);\n'
    '                if (hz < 18600000 || hz > 19800000) {\n'
    '                        printf("adreno: %u: the counter does not show the crystal\'s rate: its word is not one to use\\n", step);\n'
    '                }\n'
    '        }')

# the end of measure_clocks: the AHB verdict no longer fails the step either (literal tabs:
# the row[4] continuation line is 3 tabs + 12 spaces, not what the 8-space rule builds)
sub1(
    '\tif (ahb_hz != MEASURE_FAILED && ahb_hz >= 1000000) {\n'
    '\t\tprintf("adreno: %u: THE REGISTER BUS OF THE GPU BLOCK HAS ITS CLOCK, whatever its control register says\\n", step);\n'
    '\t\treturn true;\n'
    '\t}\n'
    '\t/* for study: what else is there in the clock controller that could be its switch */\n'
    '\tprintf("adreno: %u: the register bus of the GPU block has no clock; what is not 0 in gpucc 0x1000..0x15fc:\\n", step);\n'
    '\tfor (uint32_t offset = 0x1000; offset < 0x1600; offset += 16) {\n'
    '\t\tuint32_t row[4] = { rd(gpucc.base + offset), rd(gpucc.base + offset + 4), rd(gpucc.base + offset + 8),\n'
    '\t\t\t            rd(gpucc.base + offset + 12) };\n'
    '\n'
    '\t\tif (row[0] | row[1] | row[2] | row[3]) {\n'
    '\t\t\tprintf("adreno: %u: gpucc %#x: %#x %#x %#x %#x\\n", step, gpucc.phys + offset, row[0], row[1], row[2],\n'
    '\t\t\t    row[3]);\n'
    '\t\t}\n'
    '\t}\n'
    '\treturn false;\n'
    '}',
    '\tif (ahb_hz != MEASURE_FAILED && ahb_hz >= 1000000) {\n'
    '\t\tprintf("adreno: %u: THE REGISTER BUS OF THE GPU BLOCK HAS ITS CLOCK, whatever its control register says\\n", step);\n'
    '\t\treturn true;\n'
    '\t}\n'
    '\t/*\n'
    '\t * Not measured: the AHB control register\'s own bits are the evidence now (the\n'
    '\t * R11 log read 0x1 there: enable set, off clear) -- the counter\'s word was\n'
    '\t * never needed to go on, and the step is not failed for lack of a witness.\n'
    '\t */\n'
    '\tprintf("adreno: %u: the register bus\'s clock is not measured; its control register says %#x; going on\\n",\n'
    '\t    step, rd(gpucc.base + GPUCC_AHB_CBCR));\n'
    '\treturn true;\n'
    '}',
    tabify=False)

# ---- cx_ready: the register-bus evidence is the control register\'s, the counter is a bonus ----------
sub1(
    '        ahb_hz = measure_quietly(GPUCC_DEBUG_AHB);\n'
    '        if (ahb_hz == MEASURE_FAILED) {\n'
    '                printf("adreno: %u: the frequency counter did not finish (step 5 shows whether it works)\\n", step);\n'
    '        } else {\n'
    '                printf("adreno: %u: the GPU block\'s register bus clock (ahb) measures %u Hz\\n", step, ahb_hz);\n'
    '        }\n'
    '        if (ahb_hz == MEASURE_FAILED || ahb_hz < 1000000) {\n'
    '                printf("adreno: %u: without that clock a register of the GPU block would not answer: not touching it\\n", step);\n'
    '                return false;\n'
    '        }\n'
    '        return true;\n'
    '}',
    '        /*\n'
    '         * The register bus\'s evidence is the control register\'s own bits (enable set,\n'
    '         * off clear -- BRANCH_HALT_SKIP is Linux\'s name for not trusting this one\'s\n'
    '         * status; the R11 log read 0x1: bit 0 on, bit 31 clear). The counter, where it\n'
    '         * works, is quoted as a bonus; it is no longer a gate anywhere.\n'
    '         */\n'
    '        if ((rd(gpucc.base + GPUCC_AHB_CBCR) & (CBCR_ENABLE | CBCR_OFF)) != CBCR_ENABLE) {\n'
    '                printf("adreno: %u: the register bus\'s clock is off (%#x): without it a register of the GPU block would not answer\\n",\n'
    '                    step, rd(gpucc.base + GPUCC_AHB_CBCR));\n'
    '                return false;\n'
    '        }\n'
    '        ahb_hz = measure_quietly(GPUCC_DEBUG_AHB);\n'
    '        if (ahb_hz == MEASURE_FAILED) {\n'
    '                printf("adreno: %u: the frequency counter did not finish (step 5 says why); the control register\'s word stands\\n", step);\n'
    '        } else {\n'
    '                printf("adreno: %u: the GPU block\'s register bus clock (ahb) measures %u Hz\\n", step, ahb_hz);\n'
    '        }\n'
    '        return true;\n'
    '}',
    count=1)

# ---- step 22's line: quote what was measured, keep going either way --------------------------------
sub1(
    '        printf("adreno: %u: measured: the 3D core\'s clock runs at %u Hz (%u MHz was asked for), the GMU\'s in it at %u Hz\\n", step,\n'
    '            measure_quietly(GPUCC_DEBUG_GX_GFX3D), core_khz / 1000, measure_quietly(GPUCC_DEBUG_GX_GMU));',
    '        /* the counter, where it works, is quoted; it is not waited on anywhere (step 5 says why) */\n'
    '        printf("adreno: %u: the 3D core\'s clock measured %u Hz (%u MHz was asked for), the GMU\'s in it %u Hz\\n", step,\n'
    '            measure_quietly(GPUCC_DEBUG_GX_GFX3D), core_khz / 1000, measure_quietly(GPUCC_DEBUG_GX_GMU));')

# ---- the announce that still names sc7280 addresses in its prose ----------------------------------
sub1(
    '        announce("measure the GPU block\'s clocks with the SoC\'s frequency counter (debug registers at gcc 0x162000.., "\n'
    '            "gpucc 0x3d910fc, 0x3d91100, 0x3d91568)");',
    '        announce("measure the GPU block\'s clocks with the SoC\'s frequency counter (debug registers at gcc 0x162000.., "\n'
    '            "gpucc 0x2c910fc, 0x2c91100, 0x2c91568 -- sc7280\'s offsets, unverified here; a witness, not a gate)");')

# ---- the stale "150 MHz hub" prose above step_clocks, and the measured list's ghost clocks ---------
sub1(
    '/*\n'
    ' * The clocks the device tree gives the GMU and the SMMU, at the rates both the vendor\'s driver\n'
    ' * and Linux bring the GMU up with: 200 MHz for the GMU, 150 MHz for the "hub" (a6xx_gmu_resume).\n'
    ' * Both come from the 300 MHz tap of the global 600 MHz PLL, which runs anyway: no PLL of the\n'
    ' * GPU\'s own has to be started.\n'
    ' */',
    '/*\n'
    ' * The clock the device tree gives the GMU, at the rate both the vendor\'s driver and\n'
    ' * Linux bring the GMU up with (a6xx_gmu_resume): 200 MHz. It comes from the 300 MHz\n'
    ' * tap of the global 600 MHz PLL, which runs anyway: no PLL of the GPU\'s own has to\n'
    ' * be started. The SMMU\'s clocks (sm8150.dtsi, adreno_smmu) are gcc\'s memnoc_gfx and\n'
    ' * snoc_dvm_gfx plus this controller\'s ahb; the sc7280 "hub" family does not exist here.\n'
    ' */',
    tabify=False)

sub1(
    '        static const struct {\n'
    '                const char *name;\n'
    '                uint32_t select;\n'
    '        } clocks[] = {\n'
    '                { "cx_gmu (200 MHz was asked for)", GPUCC_DEBUG_CX_GMU }, { "hub_cx_int (from 150 MHz)", GPUCC_DEBUG_HUB_CX_INT },\n'
    '                { "hub_aon", GPUCC_DEBUG_HUB_AON }, { "cb", GPUCC_DEBUG_CB }, { "cxo_aon", GPUCC_DEBUG_CXO_AON },\n'
    '                { "gx_gmu (should be off)", GPUCC_DEBUG_GX_GMU }, { "ahb, THE ONE IN QUESTION,", GPUCC_DEBUG_AHB },\n'
    '        };',
    '        static const struct {\n'
    '                const char *name;\n'
    '                uint32_t select;\n'
    '        } clocks[] = {\n'
    '                { "cx_gmu (200 MHz was asked for)", GPUCC_DEBUG_CX_GMU }, { "cxo (the crystal, 19.2 MHz)", GPUCC_DEBUG_CXO },\n'
    '                { "gx_gmu (should be off)", GPUCC_DEBUG_GX_GMU }, { "ahb, THE ONE IN QUESTION,", GPUCC_DEBUG_AHB },\n'
    '        };')

open(SRC, "w").write(s)
print(f"raphael_adreno.c transformed: {len(orig)} -> {len(s)} bytes")
print("hub references left:", s.count("hub_clk") + s.count("hub_aon") + s.count("hub_cx_int") + s.count("GPUCC_CB_CBCR"))
print("0x52000 left:", s.count("0x52000"), "| 0x52004 now:", s.count("0x52004"))
