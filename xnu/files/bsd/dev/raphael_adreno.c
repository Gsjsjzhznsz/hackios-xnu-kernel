/*
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
 *              without which the 3D core stays locked in secure mode: its header and hashes go
 *              from the small image into memory of ours that TrustZone is pointed at
 *  13  writes  page tables for the memory the GMU's firmware asks for, and the SMMU: a
 *              translation context with those tables, and the GMU's requests sent through it
 *  14  writes  the GMU's firmware (fw/a640_gmu.bin) into its own memory and into that memory
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
 * The firmware is from Linux's linux-firmware: qcom/a640_gmu.bin (the file the msm catalog
 * names for chipid 0x06040001) and qcom/a630_sqe.fw, and qcom/sm8150/a640_zap.mbn -- the
 * generic sm8150 zap shader, which the phone's own tree may replace with its own -- all in
 * fw/ of the small image. There is no register that holds the chip ID (0x06040001, "qcom,adreno-640.1"
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

#include <kern/clock.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <mach/vm_param.h>
#include <pexpert/pexpert.h>
#include <sys/redwood_input.h>
#include <sys/stat.h>
#include <sys/systm.h>
#include <vm/vm_kern.h>

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern vm_offset_t ml_io_map_wcomb(vm_offset_t phys_addr, vm_size_t size);
extern uint64_t kvtophys(vm_offset_t va);       /* pmap_paddr_t */

static uint32_t last_step, skip_steps, pause_ms = 2200, fine, watch_seconds = 20;
static uint32_t step;                   /* the one being performed */
static uint32_t speed_bin;              /* which of the GPUs of this kind the fuses say this is (step 2) */
static uint32_t newest_step;            /* the highest one asked for: the one that has not been seen to work */
static char last_write[96] = "nothing written";
static bool quiet;                      /* repeating accesses that have been announced and seen to work: say nothing */

/*
 * The kernel's log reaches the loader when a run is over, and a run that ends in a reset
 * without a word leaves none. The loader also reads a log of the kernel's from another core
 * while it runs (loader_return.c; xl keeps it with XL_FOLLOW): every line said here goes there
 * as well, before the next register is touched.
 */
extern void loader_log_putc(char c);

static int say(const char *format, ...) __printflike(1, 2);

static int
say(const char *format, ...)
{
        char line[400];
        va_list list;

        va_start(list, format);
        vsnprintf(line, sizeof(line), format, list);
        va_end(list);
        for (const char *c = line; *c != 0; c++) {
                loader_log_putc(*c);
        }
        return printf("%s", line);
}
#define printf say

static void
sleep_us(uint32_t us)
{
        delay_for_interval(us, NSEC_PER_USEC);
}

static void
sleep_ms(uint32_t ms)
{
        delay_for_interval(ms, NSEC_PER_MSEC);
}

/* ---- register access ------------------------------------------------------------------------------- */

/*
 * One access is one plain load or store of a general register. Some of what is touched here (the
 * SMMU at least) is not the hardware but the hypervisor's imitation of it: the access traps and
 * is carried out on our behalf, which works for the simplest instruction forms only, and the
 * vendor's SMMU driver warns that this imitation "goes horribly wrong" for a store of the zero
 * register. So the compiler does not get to choose the instruction.
 */
static uint32_t __attribute__((noinline))
rd(vm_offset_t address)
{
        uint32_t value;

        __asm__ volatile ("ldr %w0, [%1]" : "=r" (value) : "r" (address) : "memory");
        return value;
}

static uint32_t __attribute__((noinline))
rd8(vm_offset_t address)
{
        uint32_t value;

        __asm__ volatile ("ldrb %w0, [%1]" : "=r" (value) : "r" (address) : "memory");
        return value;
}

static void __attribute__((noinline))
wr(vm_offset_t address, uint32_t value)
{
        __asm__ volatile ("str %w0, [%1]" : : "r" (value), "r" (address) : "memory");
}

/* A stretch of registers; mapped when a step first needs it. */
struct block {
        const char *name;
        uint32_t phys, size;
        vm_offset_t base;
};

static void
need(struct block *b)
{
        if (b->base == 0) {
                b->base = ml_io_map(b->phys, b->size);
        }
}

/* Time for the loader to note what has just been said, where it is not yet known to be harmless. */
static void
hold(bool single_access)
{
        if (step == newest_step && pause_ms && (!single_access || fine)) {
                sleep_ms(pause_ms);
        }
}

/* What a step is about to do, in a line. */
static void
announce(const char *what)
{
        printf("adreno: step %u: %s\n", step, what);
        hold(false);
}

static uint32_t
get(struct block *b, const char *name, uint32_t offset)
{
        uint32_t value;

        need(b);
        if (quiet) {
                return rd(b->base + offset);
        }
        if (step == newest_step) {
                printf("adreno: %u: about to read %s %s at %#x\n", step, b->name, name, b->phys + offset);
                hold(true);
        }
        value = rd(b->base + offset);
        printf("adreno: %u: %s %s (%#x) = %#x\n", step, b->name, name, b->phys + offset, value);
        return value;
}

static void
put(struct block *b, const char *name, uint32_t offset, uint32_t value)
{
        need(b);
        if (!quiet) {
                printf("adreno: %u: about to write %s %s at %#x: %#x\n", step, b->name, name, b->phys + offset, value);
                snprintf(last_write, sizeof(last_write), "step %u wrote %#x to %s %s at %#x", step, value, b->name, name,
                    b->phys + offset);
                hold(true);
        }
        wr(b->base + offset, value);
}

/* Set bits of a register unless they are set. Returns what the register holds afterwards. */
static uint32_t
set(struct block *b, const char *name, uint32_t offset, uint32_t bits)
{
        uint32_t was = get(b, name, offset);

        if ((was & bits) == bits) {
                return was;
        }
        put(b, name, offset, was | bits);
        return get(b, name, offset);
}

/* Look at a register that has been read before until the masked bits have the value, for so many milliseconds. */
static bool
wait_for(struct block *b, uint32_t offset, uint32_t mask, uint32_t value, uint32_t ms)
{
        for (uint32_t i = 0; i < ms * 10; i++) {
                if ((rd(b->base + offset) & mask) == value) {
                        return true;
                }
                sleep_us(100);
        }
        return (rd(b->base + offset) & mask) == value;
}

/* ---- step 1: the clocks from outside, and RPMh's names ------------------------------------------------ */

/*
 * dt: clock-controller@100000 (qcom,yupik-gcc). The GPU's clocks there, by the names the GPU's
 * nodes give them: gcc_gpu_memnoc_gfx and gcc_gpu_snoc_dvm_gfx (its two bus connections),
 * "axi_clk" = gcc_ddrss_gpu_axi (memory), gcc_gpu_cfg_ahb (the registers of its clock controller);
 * and the two taps of the 600 MHz PLL that its clock controller takes as inputs. None of them is
 * among that node's protected-clocks, the ones the hypervisor keeps to itself. Offsets:
 * gcc-sc7280.c. The touch screen driver uses this controller too.
 */
static struct block gcc = { "gcc", 0x00100000, 0x80000 };
#define GCC_PLL_VOTE            0x52004         /* gcc-sm8150.c (the phone's own): gcc_gpu_gpll0_div_clk_src, enable_reg 0x52004 BIT(16); next door 0x15200 is the GPLL vote register (bit 0 gpll0 -- R11 wrote bit 16 THERE and the log watched it drop: "= 0x1" before and after) */
#define GCC_PLL_VOTE_GPU_DIV    (1u << 16)      /* the 300 MHz tap the gmu_clk_src's table takes at 1.5 */
#define GCC_GPU_CFG_AHB_CBCR    0x71004
#define GCC_GPU_MEMNOC_GFX_CBCR 0x7100c
#define GCC_GPU_SNOC_DVM_GFX_CBCR 0x71018
#define GCC_DDRSS_GPU_AXI_CBCR  0x71154
#define CBCR_ENABLE             (1u << 0)       /* a clock's control register: on */
#define CBCR_OFF                (1u << 31)      /* ... and whether it is in fact off */

/* dt: reserved-memory cmd_db@80860000: the boot loader's list of what RPMh can switch (cmd-db.c) */
#define CMD_DB_PHYS             0x85f20000UL   /* dt sm8150: reserved-memory cmd_db@85f20000 */
#define CMD_DB_SIZE             0x20000UL

struct cmd_db_slave {
        uint16_t id, header_offset, data_offset, count, version, reserved[3];
};
struct cmd_db_header {
        uint32_t version;
        uint8_t magic[4];
        struct cmd_db_slave slave[8];
        uint32_t checksum, reserved;
        uint8_t data[];
};
struct cmd_db_entry {
        char id[8];
        uint32_t priority[2];
        uint32_t address;
        uint16_t length, offset;
};

static const struct cmd_db_header *cmd_db;

/*
 * Where RPMh has a resource (0: nowhere) and, for a supply rail, the levels it knows: the GMU's
 * firmware is told the levels by their position in this list.
 */
static uint32_t
rpmh_resource(const char *name, uint16_t *levels, unsigned *level_count)
{
        const struct cmd_db_header *db = cmd_db;
        char id[8] = { 0 };

        if (level_count != NULL) {
                *level_count = 0;
        }
        if (db == NULL) {
                db = cmd_db = (const struct cmd_db_header *)ml_io_map_wcomb(CMD_DB_PHYS, CMD_DB_SIZE);  /* memory, not registers */
        }
        if (db->magic[0] != 0xdb || db->magic[1] != 0x30 || db->magic[2] != 0x03 || db->magic[3] != 0x0c) {
                return 0;
        }
        strlcpy(id, name, sizeof(id));
        for (unsigned s = 0; s < 8; s++) {
                const struct cmd_db_slave *slave = &db->slave[s];
                const struct cmd_db_entry *entry = (const struct cmd_db_entry *)(db->data + slave->header_offset);

                if (slave->id == 0 || slave->count > 512) {
                        continue;
                }
                for (unsigned i = 0; i < slave->count; i++) {
                        const uint8_t *data = db->data + slave->data_offset + entry[i].offset;
                        unsigned count = entry[i].length / 2u;

                        if (memcmp(entry[i].id, id, sizeof(id)) != 0) {
                                continue;
                        }
                        if ((unsigned)slave->data_offset + entry[i].offset + entry[i].length > CMD_DB_SIZE - 0x1000) {
                                count = 0;
                        }
                        for (unsigned l = 0; levels != NULL && l < count && l < 16; l++) {
                                levels[l] = (uint16_t)(data[2 * l] | data[2 * l + 1] << 8);
                                if (l > 0 && levels[l] == 0) {
                                        break;
                                }
                                *level_count = l + 1;
                        }
                        return entry[i].address;
                }
        }
        return 0;
}

static void
show_rpmh_resource(const char *name)
{
        uint16_t level[16];
        unsigned count = 0;
        uint32_t address = rpmh_resource(name, level, &count);
        char levels[160];
        int at = 0;

        if (address == 0) {
                printf("adreno: %u: RPMh has no \"%s\"\n", step, name);
                return;
        }
        levels[0] = 0;
        for (unsigned l = 0; l < count && at < (int)sizeof(levels) - 8; l++) {
                at += snprintf(levels + at, sizeof(levels) - (size_t)at, " %u", level[l]);
        }
        printf("adreno: %u: RPMh has \"%s\" at %#x; levels:%s\n", step, name, address, levels);
}

static bool
step_outside(void)
{
        announce("read the GPU's clocks in the global clock controller (registers the touch screen driver uses too)");
        get(&gcc, "pll votes", GCC_PLL_VOTE);
        get(&gcc, "gpu_cfg_ahb", GCC_GPU_CFG_AHB_CBCR);
        get(&gcc, "gpu_memnoc_gfx", GCC_GPU_MEMNOC_GFX_CBCR);
        get(&gcc, "gpu_snoc_dvm_gfx", GCC_GPU_SNOC_DVM_GFX_CBCR);
        get(&gcc, "ddrss_gpu_axi", GCC_DDRSS_GPU_AXI_CBCR);

        announce("look up the GPU's supply rails in the boot loader's list of RPMh resources (memory at 0x80860000)");
        show_rpmh_resource("gfx.lvl");          /* the 3D core's own rail (dt: VDD_GFX_LEVEL, PM8350C S7) */
        show_rpmh_resource("cx.lvl");           /* the rail of the CX half, shared with most of the SoC */
        show_rpmh_resource("mx.lvl");           /* memories */
        return true;
}

/* ---- step 2: the fuses ------------------------------------------------------------------------------- */

/*
 * dt: qfprom@780000 with gpu_speed_bin@1e9 (two bytes, 8 bits from bit 5) and gpu_gaming_bin@1f5
 * (bit 5). Linux has the same cell 0x4000 further on, in the error-corrected copy of the fuses
 * (sc7280.dtsi: efuse@784000), and that is the copy it reads under this hypervisor on sibling
 * boards; so that one first.
 */
static struct block qfprom = { "qfprom", 0x00780000, 0x7000 };
#define QFPROM_CORRECTED        0x4000
#define QFPROM_GPU_SPEED_BIN    0x1e9
#define QFPROM_GPU_GAMING_BIN   0x1f5

static uint32_t
get8(struct block *b, const char *name, uint32_t offset)
{
        uint32_t value;

        need(b);
        if (step == newest_step) {
                printf("adreno: %u: about to read %s %s at %#x (a byte)\n", step, b->name, name, b->phys + offset);
                hold(true);
        }
        value = rd8(b->base + offset);
        printf("adreno: %u: %s %s (%#x) = %#x\n", step, b->name, name, b->phys + offset, value);
        return value;
}

static bool
step_fuses(void)
{
        uint32_t bin, gaming;

        announce("read the GPU's fuses in the error-corrected copy (0x784000), which Linux reads");
        bin = get8(&qfprom, "speed bin, low", QFPROM_CORRECTED + QFPROM_GPU_SPEED_BIN);
        bin |= get8(&qfprom, "speed bin, high", QFPROM_CORRECTED + QFPROM_GPU_SPEED_BIN + 1) << 8;
        bin = bin >> 5 & 0xff;
        speed_bin = bin;
        gaming = get8(&qfprom, "gaming bin", QFPROM_CORRECTED + QFPROM_GPU_GAMING_BIN) >> 5 & 1;
        /* dt: qcom,gpu-models: 0xac and 0xbe are sold as Adreno 643, anything else is the 642L */
        printf("adreno: %u: speed bin %u: an Adreno %s; gaming bin %u\n", step, bin, bin == 0xac || bin == 0xbe ? "643" : "642L",
            gaming);

        announce("read the same fuses where the phone's device tree has them (0x780000)");
        bin = get8(&qfprom, "raw speed bin, low", QFPROM_GPU_SPEED_BIN);
        bin |= get8(&qfprom, "raw speed bin, high", QFPROM_GPU_SPEED_BIN + 1) << 8;
        printf("adreno: %u: raw speed bin %u\n", step, bin >> 5 & 0xff);
        return true;
}

/* ---- step 3: the GPU's clock controller ----------------------------------------------------------------- */

/*
 * dt: clock-controller@3d90000 (qcom,yupik-gpucc), 0x9000 long; offsets: gpucc-sc7280.c. Its
 * registers answer only while gcc_gpu_cfg_ahb runs, which Linux turns on for good when it
 * starts (gcc-sc7280.c, the end of the probe function).
 */
static struct block gpucc = { "gpucc", 0x02c90000, 0x9000 };    /* dt sm8150: clock-controller@2c90000 */
#define GPUCC_PLL0_MODE         0x0000          /* the 3D core's PLL: bit 31 locked, bit 0 output on; +4: times 19.2 MHz */
#define GPUCC_PLL1_MODE         0x0100          /* a 500 MHz PLL, an alternative source for the GMU's clock */
#define GPUCC_CXO_AON_CBCR      0x1004
#define GPUCC_GX_SW_RESET       0x1008          /* dt: gpu_gx_sw_reset */
#define GPUCC_GX_GDSCR          0x100c          /* dt: gpu_gx_gdsc, the 3D core's power switch: the GMU's to operate */
#define GPUCC_GX_GMU_CBCR       0x1064
#define GPUCC_CX_GDSCR          0x106c          /* dt: gpu_cx_gdsc */
#define GPUCC_AHB_CBCR          0x1078          /* gpu_cc_ahb_clk: the register bus inside the GPU block */
#define GPUCC_CX_GMU_CBCR       0x1098          /* gpu_cc_cx_gmu_clk: the GMU */
#define GPUCC_CXO_CBCR          0x109c          /* gpu_cc_cxo_clk: the crystal, for the GMU's timers */
#define GPUCC_GMU_RCGR          0x1120          /* gpu_cc_gmu_clk_src (gpucc-sm8150.c: cmd_rcgr 0x1120): +0 command, +4 source and divider */
/*
 * Not here, whatever redwood's (sc7280's) driver says: gpu_cc_hub_clk_src, hub_aon,
 * hub_cx_int and cb are sc7280's clocks (gpucc-sc7280.c); SM8150's list
 * (gpucc-sm8150.c) has none of them, and the R11 run wrote their sc7280 offsets
 * (0x1170/0x117c/0x1178/0x1180/0x1204) into unimplemented holes -- read 0, write
 * dropped, the log agrees word for word. The SMMU's clocks on this SoC are
 * sm8150.dtsi's adreno_smmu list: this gpucc's ahb + gcc's memnoc_gfx and
 * snoc_dvm_gfx, all three already running in that same log.
 */
#define GPUCC_CX_HW_CTRL        0x1540          /* dt: gpu_cx_hw_ctrl: bit 31 is where the vendor's driver sees CX come on */
#define GPUCC_GX_DOMAIN_MISC    0x158c          /* dt: gpu_gx_domain_addr */
#define GPUCC_SMMU_VOTE_CBCR    0x5000          /* gpu_cc_hlos1_vote_gpu_smmu_clk: our vote for the SMMU's clock */

#define GDSC_SW_COLLAPSE        (1u << 0)       /* a power switch's control register (gdsc.c): off, by software */
#define GDSC_HW_CONTROL         (1u << 1)       /* something else than software operates it */
#define GDSC_POWER_ON           (1u << 31)

static bool
step_gpucc(void)
{
        announce("make sure the clock of the GPU clock controller's registers runs (gcc_gpu_cfg_ahb)");
        if ((set(&gcc, "gpu_cfg_ahb", GCC_GPU_CFG_AHB_CBCR, CBCR_ENABLE) & CBCR_OFF) &&
            !wait_for(&gcc, GCC_GPU_CFG_AHB_CBCR, CBCR_OFF, 0, 10)) {
                printf("adreno: %u: it stays off (%#x): the GPU's clock controller would not answer\n", step,
                    rd(gcc.base + GCC_GPU_CFG_AHB_CBCR));
                return false;
        }

        announce("read the GPU's clock controller (0x3d90000) as it is: the first access to the GPU's part of the SoC");
        get(&gpucc, "cx_gdscr", GPUCC_CX_GDSCR);
        get(&gpucc, "cx_hw_ctrl", GPUCC_CX_HW_CTRL);
        get(&gpucc, "gx_gdscr", GPUCC_GX_GDSCR);
        get(&gpucc, "gx_sw_reset", GPUCC_GX_SW_RESET);
        get(&gpucc, "gx_domain_misc", GPUCC_GX_DOMAIN_MISC);
        get(&gpucc, "pll0 mode", GPUCC_PLL0_MODE);
        get(&gpucc, "pll0 multiplier", GPUCC_PLL0_MODE + 4);
        get(&gpucc, "pll1 mode", GPUCC_PLL1_MODE);
        get(&gpucc, "pll1 multiplier", GPUCC_PLL1_MODE + 4);
        get(&gpucc, "gmu_clk_src command", GPUCC_GMU_RCGR);
        get(&gpucc, "gmu_clk_src source", GPUCC_GMU_RCGR + 4);
        get(&gpucc, "cxo_aon", GPUCC_CXO_AON_CBCR);
        get(&gpucc, "gx_gmu", GPUCC_GX_GMU_CBCR);
        get(&gpucc, "ahb", GPUCC_AHB_CBCR);
        get(&gpucc, "cx_gmu", GPUCC_CX_GMU_CBCR);
        get(&gpucc, "cxo", GPUCC_CXO_CBCR);
        get(&gpucc, "smmu vote", GPUCC_SMMU_VOTE_CBCR);
        return true;
}

/* ---- step 4: CX power --------------------------------------------------------------------------------- */

/* Not to touch the GPU's clock controller without the clock of its registers: where step 3 has not been. */
static bool
gpucc_answers(void)
{
        need(&gcc);
        need(&gpucc);
        if (rd(gcc.base + GCC_GPU_CFG_AHB_CBCR) & CBCR_OFF) {
                printf("adreno: %u: the GPU clock controller's registers have no clock (gcc gpu_cfg_ahb %#x): step 3\n", step,
                    rd(gcc.base + GCC_GPU_CFG_AHB_CBCR));
                return false;
        }
        return true;
}

static bool
cx_is_on(void)
{
        return (rd(gpucc.base + GPUCC_CX_GDSCR) | rd(gpucc.base + GPUCC_CX_HW_CTRL)) & GDSC_POWER_ON;
}

static void stale_stop(void);

/*
 * The switch hangs on the rail "cx.lvl", which is up for as long as the SoC runs at all; the
 * vendor's driver (gdsc-regulator.c) clears the collapse bit and waits for bit 31 of the register
 * the device tree calls hw-ctrl-addr, Linux for bit 31 of the control register itself.
 */
static bool
step_cx_power(void)
{
        uint32_t gdscr;

        if (!gpucc_answers()) {
                return false;
        }
        announce("switch the CX half of the GPU block on (gpu_cx_gdsc): clear the collapse bit of its power switch");
        gdscr = get(&gpucc, "cx_gdscr", GPUCC_CX_GDSCR);
        get(&gpucc, "cx_hw_ctrl", GPUCC_CX_HW_CTRL);
        if (cx_is_on()) {
                /*
                 * A kernel that kept the GPU for the system to use (adreno_keep) leaves it running when
                 * its run ends. What it left -- the GMU's processor, the 3D core's power, the GMU's
                 * votes in RPMh, the SMMU's contexts pointing into memory that is somebody else's now --
                 * is taken down as gpu_block_stop() would have, and the start is from nothing.
                 */
                printf("adreno: %u: CX is on already: an earlier kernel left the GPU block running; stopping it first\n", step);
                stale_stop();
                if (cx_is_on()) {
                        printf("adreno: %u: CX stays on: going on with it as it is\n", step);
                        return true;
                }
                gdscr = rd(gpucc.base + GPUCC_CX_GDSCR);
        }
        if (gdscr & GDSC_HW_CONTROL) {
                printf("adreno: %u: the switch is under hardware control: not ours to operate\n", step);
                return false;
        }
        put(&gpucc, "cx_gdscr", GPUCC_CX_GDSCR, gdscr & ~GDSC_SW_COLLAPSE);
        for (int i = 0; i < 500 && !cx_is_on(); i++) {
                sleep_us(100);
        }
        get(&gpucc, "cx_gdscr", GPUCC_CX_GDSCR);
        get(&gpucc, "cx_hw_ctrl", GPUCC_CX_HW_CTRL);
        if (!cx_is_on()) {
                printf("adreno: %u: CX does not come on\n", step);
                return false;
        }
        printf("adreno: %u: CX is on\n", step);
        if (rd(gpucc.base + GPUCC_GX_GDSCR) & GDSC_POWER_ON) {
                /*
                 * The 3D core kept its power while the rest of the block lost it: an ending that went
                 * wrong (a GMU that did not answer, before gpu_block_stop() switched the core off
                 * itself). Such a core is where it was, in the middle of things, and does not take the
                 * steps' commands (seen: step 21); switching it off from here has hung the whole phone
                 * (seen once). Left alone: no GPU until the phone has been restarted.
                 */
                printf("adreno: %u: THE 3D CORE HAS POWER ALREADY, left on by an earlier kernel: not usable until the phone is "
                    "restarted\n", step);
                return false;
        }
        return true;
}

/* ---- step 5: clocks ------------------------------------------------------------------------------------ */

#define RCG_UPDATE              (1u << 0)       /* command register (clk-rcg2.c): take the new source and divider */
#define RCG_SOURCE_MASK         0x371fu         /* [4:0] 2*divider-1, [10:8] source, [13:12] mode */
#define RCG_GPLL0_DIV(twice)    (6u << 8 | ((twice) - 1))       /* source 6: the 300 MHz tap; twice the divider wanted */

/* Turn a clock on and see it run. Not every clock's status can be believed (halt_check in Linux): `must` says which. */
static bool
clock_on(struct block *b, const char *name, uint32_t cbcr, bool must)
{
        if (!(set(b, name, cbcr, CBCR_ENABLE) & CBCR_OFF) || wait_for(b, cbcr, CBCR_OFF, 0, 10)) {
                return true;
        }
        printf("adreno: %u: %s %s stays off (%#x)%s\n", step, b->name, name, rd(b->base + cbcr),
            must ? "" : "; its status bit is not one to rely on, going on");
        return !must;
}

static bool
clock_source(const char *name, uint32_t rcgr, uint32_t source)
{
        uint32_t was = get(&gpucc, name, rcgr + 4);

        if ((was & RCG_SOURCE_MASK) == source) {
                return true;
        }
        put(&gpucc, name, rcgr + 4, (was & ~RCG_SOURCE_MASK) | source);
        put(&gpucc, name, rcgr, rd(gpucc.base + rcgr) | RCG_UPDATE);
        if (!wait_for(&gpucc, rcgr, RCG_UPDATE, 0, 10)) {
                printf("adreno: %u: %s does not take its new source (command %#x)\n", step, name, rd(gpucc.base + rcgr));
                return false;
        }
        get(&gpucc, name, rcgr + 4);
        return true;
}

static bool measure_clocks(void);

/*
 * The clock the device tree gives the GMU, at the rate both the vendor's driver and
 * Linux bring the GMU up with (a6xx_gmu_resume): 200 MHz. It comes from the 300 MHz
 * tap of the global 600 MHz PLL, which runs anyway: no PLL of the GPU's own has to
 * be started. The SMMU's clocks (sm8150.dtsi, adreno_smmu) are gcc's memnoc_gfx and
 * snoc_dvm_gfx plus this controller's ahb; the sc7280 "hub" family does not exist here.
 */
static bool
step_clocks(void)
{
        if (!gpucc_answers()) {
                return false;
        }
        if (!cx_is_on()) {
                printf("adreno: %u: CX is off: step 4\n", step);
                return false;
        }
        announce("in the global clock controller: ask for the 300 MHz tap for the GPU; the GPU's bus and memory clocks on");
        set(&gcc, "pll votes", GCC_PLL_VOTE, GCC_PLL_VOTE_GPU_DIV);
        if (!clock_on(&gcc, "gpu_memnoc_gfx", GCC_GPU_MEMNOC_GFX_CBCR, false) ||
            !clock_on(&gcc, "gpu_snoc_dvm_gfx", GCC_GPU_SNOC_DVM_GFX_CBCR, true) ||
            !clock_on(&gcc, "ddrss_gpu_axi", GCC_DDRSS_GPU_AXI_CBCR, false)) {
                return false;
        }

        announce("in the GPU's clock controller: the GMU's clock to 200 MHz (the rate its table has)");
        if (!clock_source("gmu_clk_src", GPUCC_GMU_RCGR, RCG_GPLL0_DIV(3))) {       /* 300 MHz / 1.5: F(200000000, P_GPLL0_OUT_MAIN_DIV, 1.5) */
                return false;
        }

        /*
         * The SMMU's clocks on this SoC (sm8150.dtsi, adreno_smmu): gpucc's ahb, gcc's
         * memnoc_gfx and snoc_dvm_gfx -- the two gcc ones are on above, ahb below. The
         * sc7280 "hub" family does not exist here (see the macro block); cxo_aon
         * (0x1004, mainline gpucc-sm8150.c) is added: the always-on half's crystal
         * buffer, which the R11 log found off.
         */
        announce("in the GPU's clock controller: the clocks of the GMU and of the SMMU on");
        if (!clock_on(&gpucc, "cxo", GPUCC_CXO_CBCR, true) ||
            !clock_on(&gpucc, "ahb", GPUCC_AHB_CBCR, true) ||
            !clock_on(&gpucc, "cxo_aon", GPUCC_CXO_AON_CBCR, false) ||
            !clock_on(&gpucc, "cx_gmu", GPUCC_CX_GMU_CBCR, true) ||
            !clock_on(&gpucc, "smmu vote", GPUCC_SMMU_VOTE_CBCR, false)) {
                return false;
        }
        return measure_clocks();
}

/*
 * Whether a clock runs is not always to be had from its control register: the one of the register
 * bus inside the GPU block, gpu_cc_ahb, reads 0 on this phone before and after its enable bit is
 * written (Linux, for which it has "no bit to check", writes the bit and waits 10 us). The SoC can
 * measure instead: a clock controller can put any of its clocks on a debug output, and the global
 * controller counts what it is given there against the crystal. The vendor's kernel shows the
 * result as "clk_measure"; registers, selections and procedure are its debugcc-yupik.c and
 * clk-debug.c (mainline Linux has no counterpart).
 */
#define GCC_DEBUG_MUX           0x62000         /* [9:0] which clock goes to the counter */
#define GCC_DEBUG_MUX_GPUCC     0x146           /* ... the debug output of the GPU's clock controller */
#define GCC_DEBUG_DIV           0x62004         /* [3:0] divider - 1; both controllers divide by 2 */
#define GCC_DEBUG_CBCR          0x62008
#define GCC_XO_DIV4_CBCR        0x6200c         /* the counter's time base: the crystal / 4, 4.8 MHz */
#define GCC_MEASURE_CTL         0x62038         /* [19:0] how many ticks of the time base to count for */
#define MEASURE_RUN             (1u << 20)
#define MEASURE_CLEAR           (1u << 21)
#define GCC_MEASURE_STATUS      0x6203c         /* [24:0] the count */
#define MEASURE_DONE            (1u << 25)
#define MEASURE_BRIEF           0x1000          /* ticks: a millisecond */
#define MEASURE_FULL            0x20000         /* 27 ms */
#define MEASURE_FAILED          0xffffffffu
#define GPUCC_DEBUG_DIV         0x10fc
#define GPUCC_DEBUG_CBCR        0x1100
#define GPUCC_DEBUG_MUX         0x1568          /* [7:0] which of the GPU's clocks */
#define GPUCC_DEBUG_CXO_AON     0x0b
#define GPUCC_DEBUG_GX_GFX3D    0x0d            /* the 3D core's own clock */
#define GPUCC_DEBUG_GX_GMU      0x11
#define GPUCC_DEBUG_AHB         0x12
#define GPUCC_DEBUG_CX_GMU      0x1a
#define GPUCC_DEBUG_CXO         0x1b
#define GPUCC_DEBUG_HUB_CX_INT  0x1c
#define GPUCC_DEBUG_CB          0x26
#define GPUCC_DEBUG_HUB_AON     0x27

/* Let the counter run for so many ticks of its time base; what it counted of the clock on its input. */
static uint32_t
count_clock(uint32_t ticks)
{
        uint32_t control = get(&gcc, "measure control", GCC_MEASURE_CTL) & ~(MEASURE_RUN | MEASURE_CLEAR | 0xfffffu);
        uint32_t status = 0;

        put(&gcc, "measure control", GCC_MEASURE_CTL, control | MEASURE_CLEAR);
        sleep_us(10);
        put(&gcc, "measure control", GCC_MEASURE_CTL, control | ticks);
        put(&gcc, "measure control", GCC_MEASURE_CTL, control | ticks | MEASURE_RUN);
        for (int i = 0; i < 2000 && !(status & MEASURE_DONE); i++) {
                sleep_us(100);
                status = rd(gcc.base + GCC_MEASURE_STATUS);
        }
        put(&gcc, "measure control", GCC_MEASURE_CTL, control | ticks);
        return status & MEASURE_DONE ? get(&gcc, "measure status", GCC_MEASURE_STATUS) & 0x1ffffff : MEASURE_FAILED;
}

/*
 * The rate in Hz of one of the GPU clock controller's clocks; 0 if it does not run, MEASURE_FAILED
 * if the counter does not finish. Everything that is changed for it is put back.
 */
static uint32_t
measure(uint32_t select)
{
        uint32_t gpu_mux = get(&gpucc, "debug mux", GPUCC_DEBUG_MUX), gpu_div = get(&gpucc, "debug divider", GPUCC_DEBUG_DIV);
        uint32_t gpu_cbcr = get(&gpucc, "debug output", GPUCC_DEBUG_CBCR), mux = get(&gcc, "debug mux", GCC_DEBUG_MUX);
        uint32_t div = get(&gcc, "debug divider", GCC_DEBUG_DIV), cbcr = get(&gcc, "debug input", GCC_DEBUG_CBCR);
        uint32_t base = get(&gcc, "crystal/4", GCC_XO_DIV4_CBCR), control = get(&gcc, "measure control", GCC_MEASURE_CTL);
        uint32_t brief, full;

        put(&gpucc, "debug mux", GPUCC_DEBUG_MUX, (gpu_mux & ~0xffu) | select);
        put(&gpucc, "debug divider", GPUCC_DEBUG_DIV, (gpu_div & ~0xfu) | 1);
        put(&gpucc, "debug output", GPUCC_DEBUG_CBCR, gpu_cbcr | CBCR_ENABLE);
        put(&gcc, "debug mux", GCC_DEBUG_MUX, (mux & ~0x3ffu) | GCC_DEBUG_MUX_GPUCC);
        put(&gcc, "debug divider", GCC_DEBUG_DIV, (div & ~0xfu) | 1);
        put(&gcc, "debug input", GCC_DEBUG_CBCR, cbcr | CBCR_ENABLE);
        put(&gcc, "crystal/4", GCC_XO_DIV4_CBCR, base | CBCR_ENABLE);
        /* a counter whose input stands still keeps its last count: the two counts differ only if the clock runs */
        brief = count_clock(MEASURE_BRIEF);
        full = brief == MEASURE_FAILED ? MEASURE_FAILED : count_clock(MEASURE_FULL);
        put(&gcc, "measure control", GCC_MEASURE_CTL, control);
        put(&gcc, "crystal/4", GCC_XO_DIV4_CBCR, base);
        put(&gcc, "debug input", GCC_DEBUG_CBCR, cbcr);
        put(&gcc, "debug divider", GCC_DEBUG_DIV, div);
        put(&gcc, "debug mux", GCC_DEBUG_MUX, mux);
        put(&gpucc, "debug output", GPUCC_DEBUG_CBCR, gpu_cbcr);
        put(&gpucc, "debug divider", GPUCC_DEBUG_DIV, gpu_div);
        put(&gpucc, "debug mux", GPUCC_DEBUG_MUX, gpu_mux);

        if (full == MEASURE_FAILED) {
                return MEASURE_FAILED;
        }
        if (full == brief) {
                return 0;
        }
        /* the vendor's formula; times 4 for the two dividers on the way to the counter */
        return (uint32_t)(((uint64_t)full * 10 + 15) * 4800000 / ((uint64_t)MEASURE_FULL * 10 + 35) * 4);
}

static uint32_t
measure_quietly(uint32_t select)
{
        uint32_t hz;

        quiet = true;
        hz = measure(select);
        quiet = false;
        return hz;
}

/*
 * Measure the clocks step 5 has turned on. The crystal's comes first and aloud: its rate is known,
 * so it shows whether the counter can be believed; one that is off (the 3D core's) comes last.
 */
static bool
measure_clocks(void)
{
        static const struct {
                const char *name;
                uint32_t select;
        } clocks[] = {
                { "cx_gmu (200 MHz was asked for)", GPUCC_DEBUG_CX_GMU }, { "cxo (the crystal, 19.2 MHz)", GPUCC_DEBUG_CXO },
                { "gx_gmu (should be off)", GPUCC_DEBUG_GX_GMU }, { "ahb, THE ONE IN QUESTION,", GPUCC_DEBUG_AHB },
        };
        uint32_t hz, ahb_hz = MEASURE_FAILED;

        announce("measure the GPU block's clocks with the SoC's frequency counter (debug registers at gcc 0x162000.., "
            "gpucc 0x2c910fc, 0x2c91100, 0x2c91568 -- sc7280's offsets, unverified here; a witness, not a gate)");
        hz = measure(GPUCC_DEBUG_CXO);
        if (hz == MEASURE_FAILED) {
                /*
                 * The counter is a witness, not a gate (Linux's bring-up never waits on
                 * one; the rates are arithmetic: the half-divider written is 3/2 of the
                 * 300 MHz tap = 200 MHz exactly). Its input chain here still carries
                 * sc7280's debug-mux value (0x146) and two debug branches at sc7280's
                 * offsets, none of them verified for this SoC, and one dead link holds
                 * DONE off forever -- the R11 run watched it for 200 ms a time. So: say
                 * so, and go on with the configured rates.
                 */
                printf("adreno: %u: the counter did not finish (the mux and the debug branches are the sc7280's,\n"
                    "    unverified here): going on with the configured rates\n", step);
        } else {
                printf("adreno: %u: measured: cxo runs at %u Hz (the crystal: 19200000)\n", step, hz);
                if (hz < 18600000 || hz > 19800000) {
                        printf("adreno: %u: the counter does not show the crystal's rate: its word is not one to use\n", step);
                }
        }
        for (unsigned i = 0; i < sizeof(clocks) / sizeof(clocks[0]); i++) {
                hz = measure_quietly(clocks[i].select);
                if (hz == MEASURE_FAILED) {
                        printf("adreno: %u: measured: %s: the counter did not finish\n", step, clocks[i].name);
                } else {
                        printf("adreno: %u: measured: %s runs at %u Hz\n", step, clocks[i].name, hz);
                }
                if (clocks[i].select == GPUCC_DEBUG_AHB) {
                        ahb_hz = hz;
                }
        }
        if (ahb_hz != MEASURE_FAILED && ahb_hz >= 1000000) {
                printf("adreno: %u: THE REGISTER BUS OF THE GPU BLOCK HAS ITS CLOCK, whatever its control register says\n", step);
                return true;
        }
        /*
         * Not measured: the AHB control register's own bits are the evidence now (the
         * R11 log read 0x1 there: enable set, off clear) -- the counter's word was
         * never needed to go on, and the step is not failed for lack of a witness.
         */
        printf("adreno: %u: the register bus's clock is not measured; its control register says %#x; going on\n",
            step, rd(gpucc.base + GPUCC_AHB_CBCR));
        return true;
}

/*
 * What every later step stands on: CX powered, the GMU's clock running by its control register,
 * and the register bus's clock running by measurement (see above).
 */
static bool
cx_ready(void)
{
        uint32_t gmu_clock, ahb_hz;

        if (!gpucc_answers()) {
                return false;
        }
        gmu_clock = rd(gpucc.base + GPUCC_CX_GMU_CBCR);
        if (!cx_is_on() || (gmu_clock & (CBCR_ENABLE | CBCR_OFF)) != CBCR_ENABLE) {
                printf("adreno: %u: needs CX on (it is %s) and the GMU's clock running (cx_gmu %#x): steps 4 and 5\n", step,
                    cx_is_on() ? "on" : "off", gmu_clock);
                return false;
        }
        /*
         * The register bus's evidence is the control register's own bits (enable set,
         * off clear -- BRANCH_HALT_SKIP is Linux's name for not trusting this one's
         * status; the R11 log read 0x1: bit 0 on, bit 31 clear). The counter, where it
         * works, is quoted as a bonus; it is no longer a gate anywhere.
         */
        if ((rd(gpucc.base + GPUCC_AHB_CBCR) & (CBCR_ENABLE | CBCR_OFF)) != CBCR_ENABLE) {
                printf("adreno: %u: the register bus's clock is off (%#x): without it a register of the GPU block would not answer\n",
                    step, rd(gpucc.base + GPUCC_AHB_CBCR));
                return false;
        }
        ahb_hz = measure_quietly(GPUCC_DEBUG_AHB);
        if (ahb_hz == MEASURE_FAILED) {
                printf("adreno: %u: the frequency counter did not finish (step 5 says why); the control register's word stands\n", step);
        } else {
                printf("adreno: %u: the GPU block's register bus clock (ahb) measures %u Hz\n", step, ahb_hz);
        }
        return true;
}

/* ---- step 6: the first words from the GPU block ------------------------------------------------------- */

/*
 * dt: qcom,gmu "kgsl_gmu_reg" at 0x2c6a000, 0x34000 long. Offsets in a6xx_gmu.xml are in words.
 */
static struct block gmu = { "gmu", 0x02c6a000, 0x30000 };      /* dt sm8150: qcom,gmu@2c6a000 ("gmu") */
#define GMU_WORD(n)             (4u * (n))
#define GMU_DTCM                GMU_WORD(0x1c00)        /* the processor's 16K of data memory (its code: 0x0c00) */
#define GMU_DTCM_VERSIONS       GMU_WORD(0x1c00 + 0xff8) /* five words where loaded firmware carries its versions */
#define GMU_CM3_SYSRESET        GMU_WORD(0x5000)        /* 1: the processor is held in reset */
#define GMU_CM3_BOOT_CONFIG     GMU_WORD(0x5001)
#define GMU_CM3_FW_INIT_RESULT  GMU_WORD(0x501c)        /* 0xbabeface once firmware has started */
#define GMU_CM3_CFG             GMU_WORD(0x502d)
#define GMU_ALWAYS_ON_COUNTER_L GMU_WORD(0x5088)        /* counts the 19.2 MHz crystal */
#define GMU_ALWAYS_ON_COUNTER_H GMU_WORD(0x5089)
#define GMU_SPTPRAC_PWR_CLK_STATUS GMU_WORD(0x50d0)     /* bit 6: the 3D core has no power, bit 7: no clock */
#define GMU_RPMH_POWER_STATE    GMU_WORD(0x50ec)
#define GMU_HFI_CTRL_STATUS     GMU_WORD(0x5180)
#define GMU_HFI_VERSION_INFO    GMU_WORD(0x5181)
#define GMU_GENERAL_7           GMU_WORD(0x51cc)
#define GMU_AO_HOST_INTERRUPT_STATUS GMU_WORD(0x9305)
#define GMU_RSCC_CONTROL_ACK    GMU_WORD(0x9308)
#define GMU_AO_GPU_CX_BUSY_STATUS GMU_WORD(0x930c)
#define GMU_AHB_FENCE_STATUS    GMU_WORD(0x9313)

/*
 * dt: qcom,kgsl-3d0 "kgsl_3d0_reg_memory" at 0x2c00000 is the 3D core, and without power (the GMU
 * gives it) not to be touched -- except its bus interface ("GBIF", words 0x3c00 on in a6xx.xml),
 * which is in the CX half: the vendor's driver writes GBIF_HALT before it starts the GMU.
 * "cx_misc" at 0x3d9e000 is a page of odds and ends in the CX half.
 */
static struct block gbif = { "gbif", 0x02c00000 + 4 * 0x3c00, 0x1000 };   /* the GPU block's own GBIF */
#define GBIF_SCACHE_CNTL0       (4 * 0x01)
#define GBIF_HALT               (4 * 0x45)
#define GBIF_HALT_ACK           (4 * 0x46)
static struct block cx_misc = { "cx_misc", 0x02c9e000, 0x1000 };
#define CX_MISC_SYSTEM_CACHE_CNTL_0 (4 * 1)
#define CX_MISC_SYSTEM_CACHE_CNTL_1 (4 * 2)

static bool
step_first_words(void)
{
        uint64_t start, nanos;
        uint32_t before, hz;

        if (!cx_ready()) {
                return false;
        }
        announce("read registers of the GMU (0x3d6a000): the first words from the GPU block itself");
        get(&gmu, "cm3_sysreset", GMU_CM3_SYSRESET);
        get(&gmu, "cm3_boot_config", GMU_CM3_BOOT_CONFIG);
        get(&gmu, "cm3_fw_init_result", GMU_CM3_FW_INIT_RESULT);
        get(&gmu, "cm3_cfg", GMU_CM3_CFG);
        get(&gmu, "hfi_ctrl_status", GMU_HFI_CTRL_STATUS);
        get(&gmu, "hfi_version_info", GMU_HFI_VERSION_INFO);
        get(&gmu, "general_7", GMU_GENERAL_7);
        get(&gmu, "rpmh_power_state", GMU_RPMH_POWER_STATE);
        get(&gmu, "sptprac_pwr_clk_status", GMU_SPTPRAC_PWR_CLK_STATUS);
        get(&gmu, "ao_host_interrupt_status", GMU_AO_HOST_INTERRUPT_STATUS);
        get(&gmu, "ao_gpu_cx_busy_status", GMU_AO_GPU_CX_BUSY_STATUS);
        get(&gmu, "ahb_fence_status", GMU_AHB_FENCE_STATUS);
        get(&gmu, "always_on_counter_h", GMU_ALWAYS_ON_COUNTER_H);
        get(&gmu, "always_on_counter_l", GMU_ALWAYS_ON_COUNTER_L);
        start = mach_absolute_time();
        before = rd(gmu.base + GMU_ALWAYS_ON_COUNTER_L);
        sleep_ms(10);
        hz = rd(gmu.base + GMU_ALWAYS_ON_COUNTER_L) - before;
        absolutetime_to_nanoseconds(mach_absolute_time() - start, &nanos);
        hz = nanos ? (uint32_t)((uint64_t)hz * NSEC_PER_SEC / nanos) : 0;
        printf("adreno: %u: the GMU's counter counts at %u Hz: %s\n", step, hz, hz > 18000000 && hz < 20500000 ?
            "the 19.2 MHz crystal: THE GPU BLOCK IS ALIVE" : "not the crystal's 19.2 MHz (firmware may start it): see the rest");
        for (uint32_t i = 0; i < 5; i++) {
                get(&gmu, "dtcm, where firmware has its versions", GMU_DTCM_VERSIONS + 4 * i);
        }

        announce("read the GPU's bus interface (0x3d0f000) and the CX odds and ends (0x3d9e000)");
        get(&gbif, "scache_cntl0", GBIF_SCACHE_CNTL0);
        get(&gbif, "halt", GBIF_HALT);
        get(&gbif, "halt_ack", GBIF_HALT_ACK);
        get(&cx_misc, "system_cache_cntl_0", CX_MISC_SYSTEM_CACHE_CNTL_0);
        get(&cx_misc, "system_cache_cntl_1", CX_MISC_SYSTEM_CACHE_CNTL_1);
        return true;
}

/* ---- step 7: the GMU's channel to RPMh ----------------------------------------------------------------- */

/*
 * dt: qcom,kgsl-3d0 "rscc" at 0x2c8d000: the same kind of command slots through which this
 * kernel asks RPMh for the touch screen's supply, but the GMU's own; through them its firmware
 * raises the 3D core's rail. Offsets in words: a6xx_gmu.xml, domain A6XX_RSCC.
 */
static struct block rscc = { "rscc", 0x02c8d000, 0x2000 };     /* the a630 family's GPU RSC: inside the GMU's window at +0x23000 (a6xx_gmu.c) */
#define RSCC_RSC_STATUS0_DRV0   (4 * 0x004)
#define RSCC_PDC_SLAVE_ID_DRV0  (4 * 0x00b)
#define RSCC_SEQ_BUSY_DRV0      (4 * 0x101)
#define RSCC_SEQ_MEM_0_DRV0     (4 * 0x180)
#define RSCC_TCS0_DRV0_STATUS   (4 * 0x346)
#define RSCC_ASLEEP             (1u << 16)      /* of rsc_status0: handed over to the always-on sequencer */
#define RSCC_DRV_CONFIG         0x00c
#define RSCC_SLOTS              0xd00           /* +0 interrupts enabled, +4 pending */
#define RSCC_SLOT_SIZE          0x2a0

/*
 * The channel's state and its four command slots (Linux's rpmh-rsc.c: slot n at 0xd00 + 0x2a0 n:
 * +0x10 commands whose completion is waited for, +0x14 control, +0x18 idle, +0x1c commands
 * enabled, then 20 bytes a command from +0x30: number, address, data, status).
 */
static void
rscc_show(void)
{
        get(&rscc, "drv config", RSCC_DRV_CONFIG);
        get(&rscc, "rsc_status0_drv0", RSCC_RSC_STATUS0_DRV0);
        get(&rscc, "seq_busy_drv0", RSCC_SEQ_BUSY_DRV0);
        get(&rscc, "slots' interrupts enabled", RSCC_SLOTS);
        get(&rscc, "slots' interrupts pending", RSCC_SLOTS + 4);
        for (uint32_t slot = 0; slot < 4; slot++) {
                vm_offset_t at = rscc.base + RSCC_SLOTS + RSCC_SLOT_SIZE * slot;

                printf("adreno: %u: rscc slot %u: wait %#x, control %#x, idle %#x, enabled %#x; commands (number, address, data, "
                    "status):\n", step, slot, rd(at + 0x10), rd(at + 0x14), rd(at + 0x18), rd(at + 0x1c));
                printf("adreno: %u:   %x %x %x %x | %x %x %x %x | %x %x %x %x | %x %x %x %x\n", step,
                    rd(at + 0x30), rd(at + 0x34), rd(at + 0x38), rd(at + 0x3c), rd(at + 0x44), rd(at + 0x48), rd(at + 0x4c),
                    rd(at + 0x50), rd(at + 0x58), rd(at + 0x5c), rd(at + 0x60), rd(at + 0x64), rd(at + 0x6c), rd(at + 0x70),
                    rd(at + 0x74), rd(at + 0x78));
        }
}

static bool
step_rscc(void)
{
        if (!cx_ready()) {
                return false;
        }
        announce("read the GMU's RPMh channel (0x2c8d000)");
        get(&rscc, "rsc_status0_drv0", RSCC_RSC_STATUS0_DRV0);
        get(&rscc, "pdc_slave_id_drv0", RSCC_PDC_SLAVE_ID_DRV0);
        get(&rscc, "seq_busy_drv0", RSCC_SEQ_BUSY_DRV0);
        get(&rscc, "seq_mem_0_drv0", RSCC_SEQ_MEM_0_DRV0);
        get(&rscc, "tcs0_drv0_status", RSCC_TCS0_DRV0_STATUS);
        get(&gmu, "rscc_control_ack", GMU_RSCC_CONTROL_ACK);
        return true;
}

/* ---- steps 8 and 9: the SMMU --------------------------------------------------------------------------- */

/*
 * dt sm8150 (mainline sm8150.dtsi): adreno_smmu: iommu@2ca0000, reg <0 0x02ca0000 0 0x10000>.
 * The sc7280's kgsl-smmu@3da0000 (this port's mother) is 0x20000 long, and R11 carried that
 * number over with the address; the R12 phone stopped at step 8 on exactly that ("that makes
 * 0x10000 of registers, the device tree says 0x20000: not reading further") -- and the ID
 * registers, read live, agreed with sm8150's table: 4 KB pages (IDR1 bit 31 = 0), 8 of them
 * (IDR1 [30:28] = 2), 2 x 8 x 0x1000 = 0x10000. Same register map as the sc7280's, in half
 * the space; the stream table is left as the boot loader and hypervisor set it ("qcom,skip-init").
 * Streams (dt: qcom,kgsl-iommu and qcom,gmu, "iommus"): 0 the GPU, 1 its low-priority twin,
 * 2 secure content, 5 the GMU. The global registers here are the ones the hypervisor
 * imitates; offsets: arm-smmu.h.
 */
static struct block smmu = { "smmu", 0x02ca0000, 0x10000 };    /* dt sm8150: adreno_smmu@2ca0000, 0x10000 */
#define SMMU_SCR0               0x000
#define SMMU_IDR0               0x020           /* [7:0] stream match registers */
#define SMMU_IDR1               0x024           /* bit 31: 64K pages, [30:28] log2(pages)-1, [7:0] translation contexts */
#define SMMU_IDR2               0x028
#define SMMU_IDR7               0x03c
#define SMMU_SGFSR              0x048
#define SMMU_SMR(n)             (0x800 + 4 * (n))       /* bit 31 valid, [30:16] mask, [14:0] stream */
#define SMMU_S2CR(n)            (0xc00 + 4 * (n))       /* [17:16] 0 translate 1 bypass 2 fault, [7:0] context */
#define SMMU_TLBIALLNSNH        0x068           /* arm-smmu.h: every translation of the non-secure side, in one stroke */
#define SMMU_STLBGSYNC          0x070
#define SMMU_STLBGSTATUS        0x074           /* bit 0: a global invalidation still going */
#define SMMU_SGFSYNR0           0x050           /* a global fault's syndrome, and the stream it names */
#define SMMU_SGFSYNR1           0x054
#define SMMU_CBFRSYNRA(n)       (0x400 + 4 * (n))       /* in the second page: the stream a context bank names for its last fault */
#define SMMU_CBAR(n)            (4 * (n))               /* in the second page; [17:16] kind of context */
#define SMMU_CB_SCTLR           0x00            /* a context's own page: bit 0 = translation on */
#define SMMU_CB_TTBR0           0x20
#define SMMU_CB_TCR             0x30
#define SMMU_CB_FSR             0x58

static uint32_t smmu_page, smmu_pages, smmu_contexts, smmu_matches;

/* The layout of the SMMU's register space from its ID registers. The ID registers are the
 * hardware's own word for it and win over the size this driver was compiled with: a mismatch
 * is a slip in the port's table (R12's was the sc7280's 0x20000 against this chip's 0x10000),
 * not a reason to stop. A smaller sane word is adopted as it is; a larger one would put the
 * contexts outside the window the ID registers were just read through, and only nonsense
 * larger than the map or below any real SMMU stops the ladder. */
static bool
smmu_layout(void)
{
        uint32_t idr0 = get(&smmu, "idr0", SMMU_IDR0), idr1 = get(&smmu, "idr1", SMMU_IDR1);
        uint32_t size;

        smmu_page = idr1 >> 31 ? 0x10000 : 0x1000;
        smmu_pages = 2u << (idr1 >> 28 & 7);
        smmu_contexts = idr1 & 0xff;
        smmu_matches = idr0 & 0xff;
        size = 2 * smmu_pages * smmu_page;
        printf("adreno: %u: SMMU: %u stream match registers, %u contexts, %u pages of %#x before the contexts' own\n", step,
            smmu_matches, smmu_contexts, smmu_pages, smmu_page);
        if (size == smmu.size) {
                return true;
        }
        if (size < 0x4000 || size > smmu.size) {
                printf("adreno: %u: the ID registers make %#x of registers against the %#x mapped: not reading further\n", step,
                    size, smmu.size);
                return false;
        }
        printf("adreno: %u: the device tree says %#x, the ID registers say %#x: going with the ID registers\n", step,
            smmu.size, size);
        smmu.size = size;
        return true;
}

static bool
step_smmu(void)
{
        if (!cx_ready()) {
                return false;
        }
        announce("read the ID registers of the GPU's SMMU (0x3da0000): global registers, which the hypervisor answers for");
        if (!smmu_layout()) {
                return false;
        }
        get(&smmu, "idr2", SMMU_IDR2);
        get(&smmu, "idr7", SMMU_IDR7);
        get(&smmu, "scr0", SMMU_SCR0);
        get(&smmu, "sgfsr", SMMU_SGFSR);

        announce("read the SMMU's stream table and the kinds of its contexts, as the boot loader and hypervisor left them");
        for (uint32_t i = 0; i < smmu_matches && i < 16; i++) {
                get(&smmu, "smr", SMMU_SMR(i));
                get(&smmu, "s2cr", SMMU_S2CR(i));
        }
        for (uint32_t i = 0; i < smmu_contexts && i < 16; i++) {
                get(&smmu, "cbar", smmu_page + SMMU_CBAR(i));
        }
        return true;
}

/*
 * Only context 0, the one both the vendor's driver and Linux give the GPU itself: of the others
 * at least one belongs to TrustZone (secure content), and not all can be expected to answer.
 */
static bool
step_smmu_context(void)
{
        uint32_t context;

        if (!cx_ready()) {
                return false;
        }
        announce("read translation context 0 of the SMMU: registers of the hardware itself, not the hypervisor's");
        if (!smmu_layout()) {
                return false;
        }
        context = smmu_pages * smmu_page;
        get(&smmu, "cb0 sctlr", context + SMMU_CB_SCTLR);
        get(&smmu, "cb0 ttbr0, low", context + SMMU_CB_TTBR0);
        get(&smmu, "cb0 ttbr0, high", context + SMMU_CB_TTBR0 + 4);
        get(&smmu, "cb0 tcr", context + SMMU_CB_TCR);
        get(&smmu, "cb0 fsr", context + SMMU_CB_FSR);
        return true;
}

/* ---- step 10: the GMU's memory -------------------------------------------------------------------------- */

/*
 * Firmware is loaded by writing it through these registers into the processor's memory while
 * the processor is held in reset (a6xx_gmu_fw_load). Two words of data memory go there and back,
 * and what was there is put back.
 */
static bool
step_gmu_memory(void)
{
        static const uint32_t pattern[2] = { 0x600d0001, 0x0a110002 };
        uint32_t was[2], back[2];

        if (!cx_ready()) {
                return false;
        }
        announce("hold the GMU's processor in reset, write two words into its data memory (0x2c71000), read them back");
        if (get(&gmu, "cm3_fw_init_result", GMU_CM3_FW_INIT_RESULT) == 0xbabeface) {
                printf("adreno: %u: firmware is running in the GMU: leaving its memory alone\n", step);
                return false;
        }
        put(&gmu, "cm3_sysreset", GMU_CM3_SYSRESET, 1);
        get(&gmu, "cm3_sysreset", GMU_CM3_SYSRESET);
        for (int i = 0; i < 2; i++) {
                was[i] = get(&gmu, "dtcm", GMU_DTCM + 4 * i);
        }
        for (int i = 0; i < 2; i++) {
                put(&gmu, "dtcm", GMU_DTCM + 4 * i, pattern[i]);
        }
        for (int i = 0; i < 2; i++) {
                back[i] = get(&gmu, "dtcm", GMU_DTCM + 4 * i);
        }
        for (int i = 0; i < 2; i++) {
                put(&gmu, "dtcm", GMU_DTCM + 4 * i, was[i]);
        }
        printf("adreno: %u: the GMU's memory %s\n", step, back[0] == pattern[0] && back[1] == pattern[1] ?
            "TAKES WHAT IS WRITTEN: its firmware can be loaded" : "does not hold what was written");
        return back[0] == pattern[0] && back[1] == pattern[1];
}

/* ---- memory the GPU block reaches ------------------------------------------------------------------- */

/*
 * Whatever in the GPU block reads or writes memory -- the GMU's processor, later the 3D core --
 * does it through the SMMU, which makes of the address it uses ("the GPU's address" below) a
 * physical one by walking page tables of ours, as a processor's MMU does: here always three
 * levels of 4K pages, 39 bits of address (dt: qcom,use-3-lvl-tables). The SMMU has seven such
 * translations ("contexts") and a table of which stream of requests -- 0 is the 3D core, 5 the
 * GMU (dt: iommus) -- goes through which. Formats and the order of programming: Linux
 * drivers/iommu/io-pgtable-arm.c and arm-smmu.c.
 */
#define PTE_TABLE               3ull
#define PTE_PAGE                3ull
#define PTE_CACHED              (1ull << 2)     /* memory attribute 1 of SMMU_MAIR0; without it 0, uncached */
#define PTE_UNPRIVILEGED        (1ull << 6)     /* the 3D core's accesses are; the GMU's are not */
#define PTE_SHARED              (2ull << 8)
#define PTE_ACCESSED            (1ull << 10)
#define PTE_NOT_GLOBAL          (1ull << 11)
#define PTE_ADDRESS             0x0000fffffffff000ull
#define PTE_GMU                 (PTE_SHARED | PTE_ACCESSED | PTE_NOT_GLOBAL)

#define SMMU_S2CR_FAULT         (2u << 16)
#define SMMU_SMR_VALID          (1u << 31)
/* R20: the second cell of the "iommus" each stream comes through, as the phone's own dt has it.
 * The port carried sc7280's over unchanged -- one mask 0x400 for everything -- but raphael's dt
 * (kgsl-iommu@2ca0000: gfx3d_user sid 0, gfx3d_secure sid 2; gmu@2c6a000: gmu_user sid 4,
 * gmu_kernel sid 5) gives the gfx streams 0x401 -- one entry for streams 0 and 1 -- and the gmu
 * streams 0x400, which is by coincidence the form sc7280 uses for everything. What R17-R19 wrote
 * for the gfx streams was sc7280's form, and the SMMU took none of it; the gmu's landed every
 * time. Read out of this phone's own dtb (the 499823-byte one), not sc7280's. */
#define SMMU_MASK_GMU           0x400           /* dt raphael: gmu_user/gmu_kernel (4/5) -- sc7280's form, by coincidence; the GMU's writes have always landed */
#define SMMU_MASK_GFX           0x401           /* dt raphael: gfx3d_user (0) -- one entry for streams 0 and 1 */
#define SMMU_CBAR_STAGE_1       (1u << 16 | 3u << 8 | 0xfu << 12)       /* our tables alone decide; weakest defaults */
#define SMMU_CBA2R(n)           (0x800 + 4 * (n))                       /* in the second page, like CBAR */
#define SMMU_CBA2R_64_BIT       1
#define SMMU_CB_TCR2            0x10
#define SMMU_CB_TTBR1           0x28
#define SMMU_CB_MAIR0           0x38
#define SMMU_CB_MAIR1           0x3c
#define SMMU_CB_FAR             0x60
#define SMMU_CB_FSYNR0          0x68
#define SMMU_CB_TLBIASID        0x610
#define SMMU_CB_TLBIALL         0x618
#define SMMU_CB_TLBSYNC         0x7f0
#define SMMU_CB_TLBSTATUS       0x7f4
#define SMMU_SCTLR_ON           (1u << 0 | 1u << 1 | 1u << 2 | 1u << 5)    /* on; faults recorded, no interrupt.  Bit 12 was
                                                                         * written since R13 and never once read back (R15:
                                                                         * 0x1027 written, 0x27 landed); R16 writes the 0x27
                                                                         * that actually runs and demands the readback match */
#define SMMU_TCR_39_BITS        (25u | 1u << 8 | 1u << 10 | 3u << 12 | 1u << 23)   /* one table of 4K pages, cached walks */
#define SMMU_TCR2               (1u | 1u << 4 | 7u << 15)       /* 36 bits of physical address, 16 of context tag */
#define SMMU_MAIR0              0xf404ff44      /* Linux's: 0 uncached, 1 cached, 2 device */
#define SMMU_FSR_ALL            0xc00001fe      /* every kind of fault, and "several" */
#define SMMU_FSR_FATAL          (1u << 3 | 1u << 31)    /* a bus error, or several at once: the waiting
                                                                 * ends.  Every other kind -- access flag,
                                                                 * permission, translation -- is named, cleared,
                                                                 * and waited over (R17: the R16 phone's firmware
                                                                 * answered the speed wanted right across a
                                                                 * refusal of that kind, once the wait itself
                                                                 * was allowed to run) */

extern void loader_return(void);

/* Until every write to memory so far has been carried out. */
static void
settle(void)
{
        __asm__ volatile ("dsb sy");
}

/* Push what this processor has written to a stretch of memory out of its cache, and forget it: the GPU block reads memory. */
static void
to_memory(vm_offset_t start, vm_size_t size)
{
        for (vm_offset_t line = start & ~(vm_offset_t)63; line < start + size; line += 64) {
                __asm__ volatile ("dc civac, %0" : : "r" (line));
        }
        settle();
}

#define SPACE_TABLES            1024            /* 2 GB of addresses, were they all in use */

struct gpu_space {
        const char *name;
        uint32_t context;               /* which of the SMMU's */
        uint32_t control;               /* bits of its control register besides the usual */
        uint32_t table_count;
        uint64_t *root;
        struct {
                uint64_t phys;
                uint64_t *table;
                bool changed;           /* since it was last written out of this processor's cache (space_commit()) */
        } tables[SPACE_TABLES];
        uint64_t *last_entry, *last_table;      /* the level 2 entry looked up last, and its table */
        uint32_t last_changed;                  /* the table an entry was written in last */
};

static uint64_t *
space_new_table(struct gpu_space *space)
{
        static vm_offset_t spare;
        static unsigned spare_left;
        uint64_t *table;

        if (space->table_count == SPACE_TABLES) {
                return NULL;
        }
        if (spare_left == 0) {
                if (kmem_alloc(kernel_map, &spare, PAGE_SIZE, KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) !=
                    KERN_SUCCESS) {
                        return NULL;
                }
                spare_left = PAGE_SIZE / 4096;
        }
        table = (uint64_t *)(spare + PAGE_SIZE - 4096 * spare_left--);
        space->tables[space->table_count].phys = kvtophys((vm_offset_t)table);
        space->tables[space->table_count++].table = table;
        return table;
}

/* The table an entry of a higher table leads to; made if the entry is empty. */
/* An entry of one of the tables has been written: that table is to go out to memory. */
static void
space_changed(struct gpu_space *space, const uint64_t *entry)
{
        if ((uintptr_t)entry - (uintptr_t)space->tables[space->last_changed].table < 4096) {
                space->tables[space->last_changed].changed = true;      /* page after page of one buffer */
                return;
        }
        for (uint32_t i = space->table_count; i-- > 0;) {
                if ((uintptr_t)entry - (uintptr_t)space->tables[i].table < 4096) {
                        space->tables[i].changed = true;
                        space->last_changed = i;
                        return;
                }
        }
}

/* The SMMU reads the tables from memory, not from this processor's cache. */
static void
space_commit(struct gpu_space *space)
{
        for (uint32_t i = 0; i < space->table_count; i++) {
                if (space->tables[i].changed) {
                        to_memory((vm_offset_t)space->tables[i].table, 4096);
                        space->tables[i].changed = false;
                }
        }
}

static uint64_t *
space_next(struct gpu_space *space, uint64_t *entry)
{
        uint64_t *table;

        if (*entry & 1) {
                for (uint32_t i = 0; i < space->table_count; i++) {
                        if (space->tables[i].phys == (*entry & PTE_ADDRESS)) {
                                return space->tables[i].table;
                        }
                }
                return NULL;
        }
        table = space_new_table(space);
        if (table != NULL) {
                *entry = kvtophys((vm_offset_t)table) | PTE_TABLE;
                space_changed(space, entry);
        }
        return table;
}

/* One 4K page. */
static bool
space_map(struct gpu_space *space, uint64_t address, uint64_t phys, uint64_t attributes)
{
        uint64_t *level2, *level3;

        if (address >> 39 || (space->root == NULL && (space->root = space_new_table(space)) == NULL)) {
                return false;
        }
        level2 = space_next(space, &space->root[address >> 30 & 0x1ff]);
        if (level2 == NULL) {
                return false;
        }
        if (space->last_entry == &level2[address >> 21 & 0x1ff]) {
                level3 = space->last_table;             /* page after page of one buffer: the same table */
        } else {
                level3 = space_next(space, &level2[address >> 21 & 0x1ff]);
                space->last_entry = &level2[address >> 21 & 0x1ff];
                space->last_table = level3;
        }
        if (level3 == NULL) {
                space->last_entry = NULL;
                return false;
        }
        level3[address >> 12 & 0x1ff] = phys == 0 && attributes == 0 ? 0 : phys | attributes | PTE_PAGE;
        space_changed(space, level3);
        return true;
}

/* Memory of ours that the GPU block sees at an address of its own. */
struct gpu_block {
        uint64_t address;               /* the GPU's */
        uint32_t size;
        vm_offset_t memory;             /* ours */
};

static bool
block_new(struct gpu_space *space, struct gpu_block *block, uint64_t address, uint32_t size, uint64_t attributes)
{
        block->address = address;
        block->size = (size + 4095) & ~4095u;
        if (kmem_alloc(kernel_map, &block->memory, round_page(block->size), KMA_KOBJECT | KMA_ZERO | KMA_DATA,
            VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
                return false;
        }
        for (uint32_t offset = 0; offset < block->size; offset += 4096) {
                if (!space_map(space, address + offset, kvtophys(block->memory + offset), attributes)) {
                        return false;
                }
        }
        to_memory(block->memory, block->size);
        printf("adreno: %u: %s sees %#llx..%#llx in memory of ours (%#llx)\n", step, space->name, address,
            address + block->size - 1, kvtophys(block->memory));
        return true;
}

/*
 * A second view of a block without this processor's cache in between, for memory that both
 * sides write a word at a time. Only blocks of one page of ours: those are in one piece.
 */
static vm_offset_t
block_uncached(const struct gpu_block *block)
{
        uint64_t phys = kvtophys(block->memory);

        for (uint32_t offset = 0; offset < block->size; offset += 4096) {
                if (kvtophys(block->memory + offset) != phys + offset) {
                        return 0;
                }
        }
        return ml_io_map_wcomb(phys, block->size);
}

static uint32_t
smmu_context(const struct gpu_space *space)
{
        return (smmu_pages + space->context) * smmu_page;
}

/* What our own tables say for an address: every descriptor of the walk, so the log can tell a leaf
 * this driver wrote from an answer that came from somewhere else. */
static void
space_show_walk(const struct gpu_space *space, uint64_t address)
{
        const uint64_t *level2 = NULL, *level3 = NULL;
        uint64_t e1, e2;

        if (space->root == NULL) {
                printf("adreno: %u: what our own tables say for %#llx: there are none\n", step, address);
                return;
        }
        e1 = space->root[address >> 30 & 0x1ff];
        printf("adreno: %u: what our own tables say for %#llx: L1[%u] = %#llx\n", step, address, address >> 30 & 0x1ff, e1);
        for (uint32_t i = 0; i < space->table_count && (e1 & 3) == 3; i++) {
                if (space->tables[i].phys == (e1 & PTE_ADDRESS)) {
                        level2 = space->tables[i].table;
                }
        }
        if (level2 == NULL) {
                printf("adreno: %u:   L1 leads to a table that is not ours (%#llx)\n", step, e1 & PTE_ADDRESS);
                return;
        }
        e2 = level2[address >> 21 & 0x1ff];
        printf("adreno: %u:   L2[%u] = %#llx\n", step, address >> 21 & 0x1ff, e2);
        for (uint32_t i = 0; i < space->table_count && (e2 & 3) == 3; i++) {
                if (space->tables[i].phys == (e2 & PTE_ADDRESS)) {
                        level3 = space->tables[i].table;
                }
        }
        if (level3 == NULL) {
                printf("adreno: %u:   L2 leads to a table that is not ours (%#llx)\n", step, e2 & PTE_ADDRESS);
                return;
        }
        printf("adreno: %u:   L3[%u] = %#llx -- %s, the access flag %s, our page %#llx\n", step, address >> 12 & 0x1ff,
            level3[address >> 12 & 0x1ff], (level3[address >> 12 & 0x1ff] & 3) == 3 ? "a leaf" : "not a leaf",
            level3[address >> 12 & 0x1ff] & PTE_ACCESSED ? "set" : "CLEAR", level3[address >> 12 & 0x1ff] & PTE_ADDRESS);
}

/* What a context has recorded of a request it could not serve. */
static bool
space_faulted(const struct gpu_space *space)
{
        uint32_t context = smmu_context(space), status = rd(smmu.base + context + SMMU_CB_FSR);
        uint64_t far;

        if ((status & SMMU_FSR_ALL) == 0) {             /* bits 10:9 only tell the format of the syndrome */
                return false;
        }
        far = (uint64_t)rd(smmu.base + context + SMMU_CB_FAR + 4) << 32 | rd(smmu.base + context + SMMU_CB_FAR);
        printf("adreno: %u: THE SMMU REFUSED %s: status %#x, address %#llx, syndrome %#x\n", step, space->name, status, far,
            rd(smmu.base + context + SMMU_CB_FSYNR0));
        /* R16: whose transaction it was and what our own tables say about the address it named -- the
         * R15 fault was an access-flag refusal at an address this driver maps with the flag set */
        printf("adreno: %u: the stream the bank names for it: %#x; the global side: gfsr %#x, syndromes %#x %#x\n", step,
            rd(smmu.base + smmu_page + SMMU_CBFRSYNRA(space->context)), rd(smmu.base + SMMU_SGFSR),
            rd(smmu.base + SMMU_SGFSYNR0), rd(smmu.base + SMMU_SGFSYNR1));
        printf("adreno: %u: the bank as it stands: sctlr %#x, ttbr0 %#x:%#x, tcr %#x\n", step,
            rd(smmu.base + context + SMMU_CB_SCTLR), rd(smmu.base + context + SMMU_CB_TTBR0 + 4),
            rd(smmu.base + context + SMMU_CB_TTBR0), rd(smmu.base + context + SMMU_CB_TCR));
        space_show_walk(space, far);
        return true;
}

/* The refusals the wait has already named, so that one kind is not a hundred lines. */
static uint32_t fault_status_seen, fault_far_lo_seen, fault_far_hi_seen, fault_count;

static void
space_forget_refusals(void)
{
        fault_status_seen = fault_far_lo_seen = fault_far_hi_seen = fault_count = 0;
}

/*
 * What the context has recorded since the last look, with the verdict the wait wants: true
 * to go on (nothing there, or a survivable refusal, named and cleared), false when the
 * SMMU reports a bus error or several faults at once -- the GMU's transactions are being
 * thrown away wholesale, and no patience brings them back.  R17: during the wait for an
 * answer this is asked every few turns, so a refusal like the R15 one no longer ends the
 * exchange -- it is named with its address, cleared (write one to clear), and the wait
 * goes on; one kind of refusal is printed once, then every hundredth of the same.
 */
static bool
space_check(const struct gpu_space *space, const char *when)
{
        uint32_t context = smmu_context(space), status = rd(smmu.base + context + SMMU_CB_FSR);
        uint64_t far;

        if ((status & SMMU_FSR_ALL) == 0) {             /* bits 10:9 only tell the format of the syndrome */
                return true;
        }
        far = (uint64_t)rd(smmu.base + context + SMMU_CB_FAR + 4) << 32 | rd(smmu.base + context + SMMU_CB_FAR);
        if (status & SMMU_FSR_FATAL) {
                printf("adreno: %u: THE SMMU REFUSED %s with a bus error (%s): status %#x, address %#llx\n", step,
                    space->name, when, status, far);
                space_faulted(space);           /* the full naming: the stream, the syndromes, the walk */
                return false;
        }
        if (status != fault_status_seen || far != ((uint64_t)fault_far_hi_seen << 32 | fault_far_lo_seen)) {
                fault_status_seen = status;
                fault_far_lo_seen = (uint32_t)far;
                fault_far_hi_seen = (uint32_t)(far >> 32);
                fault_count = 1;
                printf("adreno: %u: the SMMU refuses %s again (%s): status %#x, address %#llx, syndrome %#x -- "
                    "survivable, cleared, the wait goes on\n", step, space->name, when, status, far,
                    rd(smmu.base + context + SMMU_CB_FSYNR0));
        } else if (++fault_count % 100 == 0) {
                printf("adreno: %u: the same refusal %u times now (%s): status %#x, address %#llx -- still survivable, "
                    "cleared, the wait goes on\n", step, fault_count, when, status, far);
        }
        wr(smmu.base + context + SMMU_CB_FSR, status & SMMU_FSR_ALL);   /* write one to clear */
        return true;
}

/*
 * One more stream through a context that is on. A match is taken only as the device tree has
 * it: on sc7280 (whose driver this one is ported from) a stream and the mask 0x400, one entry
 * for streams 0 and 1 (mask 0x401) reading back 0; on raphael it is the other way round -- the
 * gfx streams' dt mask is 0x401 and the gmu's is 0x400 -- so the mask has come in as an
 * argument since R20, the value written and the value read back the same one.
 */
static bool
space_stream(struct gpu_space *space, uint32_t match, uint32_t stream, uint32_t mask)
{
        put(&smmu, "s2cr", SMMU_S2CR(match), space->context);
        put(&smmu, "smr", SMMU_SMR(match), SMMU_SMR_VALID | mask << 16 | stream);
        if (get(&smmu, "smr", SMMU_SMR(match)) != (SMMU_SMR_VALID | mask << 16 | stream) ||
            (get(&smmu, "s2cr", SMMU_S2CR(match)) & 0x300ff) != space->context) {
                printf("adreno: %u: the SMMU did not take what was written: stream %u is not going through context %u\n", step,
                    stream, space->context);
                return false;
        }
        return true;
}

/* Give a context our tables and send a stream through it; `match` is the entry of the stream table to use. */
static bool
space_on(struct gpu_space *space, uint32_t match, uint32_t stream, uint32_t mask)
{
        uint32_t context = smmu_context(space);
        uint64_t root = space->tables[0].phys | (uint64_t)space->context << 48;        /* tagged with the context's number */

        for (uint32_t i = 0; i < space->table_count; i++) {
                to_memory((vm_offset_t)space->tables[i].table, 4096);
        }
        put(&smmu, "cb sctlr", context + SMMU_CB_SCTLR, 0);
        put(&smmu, "cba2r", smmu_page + SMMU_CBA2R(space->context), SMMU_CBA2R_64_BIT);
        put(&smmu, "cbar", smmu_page + SMMU_CBAR(space->context), SMMU_CBAR_STAGE_1);
        put(&smmu, "cb tcr2", context + SMMU_CB_TCR2, SMMU_TCR2);
        put(&smmu, "cb tcr", context + SMMU_CB_TCR, SMMU_TCR_39_BITS);
        put(&smmu, "cb ttbr0, low", context + SMMU_CB_TTBR0, (uint32_t)root);
        put(&smmu, "cb ttbr0, high", context + SMMU_CB_TTBR0 + 4, (uint32_t)(root >> 32));
        put(&smmu, "cb ttbr1, low", context + SMMU_CB_TTBR1, 0);
        put(&smmu, "cb ttbr1, high", context + SMMU_CB_TTBR1 + 4, 0);
        put(&smmu, "cb mair0", context + SMMU_CB_MAIR0, SMMU_MAIR0);
        put(&smmu, "cb mair1", context + SMMU_CB_MAIR1, 0);
        put(&smmu, "cb fsr", context + SMMU_CB_FSR, SMMU_FSR_ALL);
        /* everything this bank ever cached, not only our ASID's entries: the R14 log's fault
         * was an access-flag fault at an address of ours that was never mapped -- the kind of
         * answer a stale translation left by whoever configured the bank before us gives */
        put(&smmu, "cb tlbiall", context + SMMU_CB_TLBIALL, 0);
        put(&smmu, "cb tlbiasid", context + SMMU_CB_TLBIASID, space->context);
        put(&smmu, "cb tlbsync", context + SMMU_CB_TLBSYNC, 1);
        if (!wait_for(&smmu, context + SMMU_CB_TLBSTATUS, 1, 0, 100)) {
                printf("adreno: %u: the SMMU does not finish forgetting old translations (%#x)\n", step,
                    rd(smmu.base + context + SMMU_CB_TLBSTATUS));
                return false;
        }
        /* R16: and the whole non-secure side besides, the one way of asking that reaches translations a
         * bank may have taken with it from whoever ran before the kernel: the R15 fault was an
         * access-flag refusal at an address our own tables map with the flag set -- an answer out of
         * a translation this driver never made (the global registers are the hypervisor's to grant;
         * if it swallowed this one the bank's own invalidations above still stand) */
        put(&smmu, "tlbiallnsnh", SMMU_TLBIALLNSNH, 0);
        put(&smmu, "stlbgsync", SMMU_STLBGSYNC, 1);
        if (!wait_for(&smmu, SMMU_STLBGSTATUS, 1, 0, 100)) {
                printf("adreno: %u: the SMMU does not finish the global invalidation (%#x)\n", step,
                    rd(smmu.base + SMMU_STLBGSTATUS));
        }
        put(&smmu, "cb sctlr", context + SMMU_CB_SCTLR, SMMU_SCTLR_ON | space->control);
        put(&smmu, "s2cr", SMMU_S2CR(match), space->context);
        put(&smmu, "smr", SMMU_SMR(match), SMMU_SMR_VALID | mask << 16 | stream);

        /* the global ones are the hypervisor's to grant: see what it made of them */
        if (get(&smmu, "smr", SMMU_SMR(match)) != (SMMU_SMR_VALID | mask << 16 | stream) ||
            (get(&smmu, "s2cr", SMMU_S2CR(match)) & 0x300ff) != space->context ||
            (get(&smmu, "cbar", smmu_page + SMMU_CBAR(space->context)) & 0x30000) != (SMMU_CBAR_STAGE_1 & 0x30000) ||
            (get(&smmu, "cb sctlr", context + SMMU_CB_SCTLR) & SMMU_SCTLR_ON) != SMMU_SCTLR_ON ||
            get(&smmu, "cb ttbr0, low", context + SMMU_CB_TTBR0) != (uint32_t)root ||
            get(&smmu, "cb ttbr0, high", context + SMMU_CB_TTBR0 + 4) != (uint32_t)(root >> 32)) {
                printf("adreno: %u: the SMMU did not take what was written: stream %u is not going through context %u\n", step,
                    stream, space->context);
                return false;
        }
        if (space->control != 0 && (rd(smmu.base + context + SMMU_CB_SCTLR) & space->control) != space->control) {
                printf("adreno: %u: the context's own switch bits %#x did not all land (sctlr reads %#x)\n", step, space->control,
                    rd(smmu.base + context + SMMU_CB_SCTLR));
        }
        get(&smmu, "cb tcr", context + SMMU_CB_TCR);
        get(&smmu, "cba2r", smmu_page + SMMU_CBA2R(space->context));
        get(&smmu, "cb tcr2", context + SMMU_CB_TCR2);
        get(&smmu, "cb fsr", context + SMMU_CB_FSR);
        get(&smmu, "sgfsr", SMMU_SGFSR);
        return true;
}

/* A stream goes nowhere again, and its context translates nothing. */
static void
space_off(struct gpu_space *space, uint32_t match)
{
        put(&smmu, "smr", SMMU_SMR(match), 0);
        put(&smmu, "s2cr", SMMU_S2CR(match), SMMU_S2CR_FAULT);
        put(&smmu, "cb sctlr", smmu_context(space) + SMMU_CB_SCTLR, 0);
}

/* ---- step 11: TrustZone -------------------------------------------------------------------------------- */

/*
 * The 3D core comes out of reset in "secure" mode, and on a phone only TrustZone lets it out: it
 * does when handed the vendor's signed zap shader to authenticate (a6xx_gpu.c: a6xx_hw_init;
 * qcom_scm.c: qcom_scm_pas_*; the GPU is image 13). Whether it will do that for this kernel
 * decides whether there can be a GPU at all. Questions that pass no memory show whether it
 * listens: the one Linux asks before anything else (is the call "is this call available"
 * available), whether the calls that authenticate an image are, and which images it says it
 * "supports". The last is asked of the GPU's and, for comparison, of images this phone is known
 * to start that way (dt: qcom,pas-id of the audio, modem and video firmware): no GPU driver asks
 * it, Linux's or the vendor's, so what the answer for the GPU means is not known beforehand.
 * A call is an SMC, which the hypervisor passes on; convention: qcom_scm-smc.c.
 */
#define SCM_FAST                (1ull << 31)            /* answered at once; without it a call may be preempted */
#define SCM_64                  (1ull << 30)
#define SCM_SIP                 (2ull << 24)            /* a call of the SoC maker's, not one of the Arm standard */
#define SCM_CALL(service, command) ((uint64_t)(service) << 8 | (command))
#define SCM_IS_CALL_AVAILABLE   SCM_CALL(6, 1)
#define SCM_PAS_INIT_IMAGE      SCM_CALL(2, 1)
#define SCM_PAS_AUTH_AND_RESET  SCM_CALL(2, 5)
#define SCM_PAS_SHUTDOWN        SCM_CALL(2, 6)
#define SCM_PAS_IS_SUPPORTED    SCM_CALL(2, 7)
#define SCM_ONE_VALUE           1                       /* the word describing the arguments: one, a plain number */
#define SCM_VALUE_AND_BUFFER    (2 | 2 << 6)            /* two: a number, and the address of memory it may read and write */
#define SCM_VALUE_BUFFER_VALUE  (3 | 2 << 6)            /* three (R25): a number, the address of memory it may read and
                                                         * write, and a number -- Linux's pas_init_image(pas, meta, size) */
#define SCM_INTERRUPTED         1                       /* a call that was preempted says so, and is made again so */
#define PAS_GPU                 13

/*
 * R21: which form the calls take. Linux asks first with 64-bit arguments and, if TrustZone
 * says no to that, again with 32-bit ones (qcom_scm.c); the answer is remembered here, and
 * every later call of the run takes the form that was answered. On redwood the first was
 * answered. On this phone R20's first word to TrustZone -- the give-up call, service 2
 * command 6 -- took the processor and never gave it back (the log stops at "about to ask"
 * and the wrapper's answer line never comes; touch lived on: one processor eaten inside the
 * hypervisor or TrustZone itself). So step 19 asks the way this phone's own Linux does when
 * its GPU starts -- is the call available, the image's signature, its place, the
 * authentication -- and does not ask the give-up call by default (adreno_tzunload=1 asks
 * for it, at the very end, with the channel proven).
 */
static uint64_t scm_how = SCM_FAST | SCM_64 | SCM_SIP;

/* One call. What TrustZone returns: 0 or an error of its own, and a word of answer. */
static int32_t
scm(const char *what, uint64_t function, uint64_t arguments, uint64_t first, uint64_t second, uint64_t third,
    uint64_t *answer)
{
        uint64_t session = 0;

        printf("adreno: %u: about to ask TrustZone %s: smc %#llx, arguments %#llx, %#llx, %#llx\n", step, what, function, first,
            second, third);
        snprintf(last_write, sizeof(last_write), "step %u asked TrustZone %#llx(%#llx, %#llx)", step, function, first, second);
        hold(true);
        for (int i = 0; i < 100; i++) {
                register uint64_t x0 __asm__("x0") = i == 0 ? function : SCM_INTERRUPTED;
                register uint64_t x1 __asm__("x1") = arguments;
                register uint64_t x2 __asm__("x2") = first;
                register uint64_t x3 __asm__("x3") = second;
                register uint64_t x4 __asm__("x4") = third;
                register uint64_t x5 __asm__("x5") = 0;
                register uint64_t x6 __asm__("x6") = session;
                register uint64_t x7 __asm__("x7") = 0;

                __asm__ volatile ("smc #0" : "+r" (x0), "+r" (x1), "+r" (x2), "+r" (x3), "+r" (x4), "+r" (x5), "+r" (x6),
                    "+r" (x7) : : "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17", "memory");
                session = x6;
                if (x0 != SCM_INTERRUPTED) {
                        if (i != 0) {
                                printf("adreno: %u: the call was preempted %d time%s before it was answered\n", step, i,
                                    i == 1 ? "" : "s");
                        }
                        printf("adreno: %u: TrustZone returns %d, answer %#llx\n", step, (int32_t)x0, x1);
                        *answer = x1;
                        return (int32_t)x0;
                }
        }
        printf("adreno: %u: TrustZone keeps saying the call was interrupted\n", step);
        return -1;
}

/*
 * R26: the whole log goes to the storage before a call that can reset the phone.
 * The watching loader writes what the log holds this moment -- everything printed
 * so far, this wrapper's own line included -- to the logdump partition, and answers
 * with the ack; a reset that follows does not then eat the record.  (loader_return.c;
 * an old loader that does not know the request is waited out and the run goes on.)
 */
extern int loader_log_flush(void);

static void
put_the_log_away(const char *what)
{
        printf("adreno: %u: %s; the whole log goes to the storage first (a reset must not eat the record)\n",
            step, what);
        if (!loader_log_flush()) {
                printf("adreno: %u: the loader did not put the log away in time (no loader, or an old one); going on\n",
                    step);
        }
}

static bool
step_trustzone(void)
{
        static const struct {
                const char *name;
                uint32_t id;
        } images[] = { { "audio", 1 }, { "modem", 4 }, { "video", 9 }, { "GPU zap shader", PAS_GPU } };
        uint64_t answer = 0, how = SCM_FAST | SCM_64 | SCM_SIP;
        uint32_t fast[4] = { 0 }, slow[4] = { 0 };

        announce("ask TrustZone what Linux asks it first: is the call \"is this call available\" available");
        if (scm("whether it can be asked", how | SCM_IS_CALL_AVAILABLE, SCM_ONE_VALUE, SCM_SIP | SCM_IS_CALL_AVAILABLE, 0,
            0, &answer) != 0 || answer != 1) {
                how = SCM_FAST | SCM_SIP;       /* the same with 32-bit arguments, Linux's second try */
                if (scm("the same, the 32-bit way", how | SCM_IS_CALL_AVAILABLE, SCM_ONE_VALUE, SCM_SIP | SCM_IS_CALL_AVAILABLE,
                    0, 0, &answer) != 0 || answer != 1) {
                        printf("adreno: %u: TrustZone does not answer the way it answers Linux\n", step);
                        return false;
                }
        }

        announce("ask TrustZone whether it has the calls that authenticate a firmware image");
        scm("for the call that takes an image's signature", how | SCM_IS_CALL_AVAILABLE, SCM_ONE_VALUE,
            SCM_SIP | SCM_PAS_INIT_IMAGE, 0, 0, &answer);
        scm("for the call that authenticates and starts an image", how | SCM_IS_CALL_AVAILABLE, SCM_ONE_VALUE,
            SCM_SIP | SCM_PAS_AUTH_AND_RESET, 0, 0, &answer);

        announce("ask TrustZone which images it \"supports\": three this phone starts through it, and the GPU's; each asked "
            "both ways, at once and (as Linux does) as a call that may be preempted");
        for (unsigned i = 0; i < 4; i++) {
                answer = 0;
                fast[i] = scm(images[i].name, how | SCM_PAS_IS_SUPPORTED, SCM_ONE_VALUE, images[i].id, 0, 0, &answer) == 0 && answer;
                answer = 0;
                slow[i] = scm(images[i].name, (how & ~SCM_FAST) | SCM_PAS_IS_SUPPORTED, SCM_ONE_VALUE, images[i].id, 0,
                    0, &answer) == 0 && answer;
        }
        printf("adreno: %u: \"supported\", asked at once / as Linux asks: audio %u/%u, modem %u/%u, video %u/%u, GPU %u/%u\n",
            step, fast[0], slow[0], fast[1], slow[1], fast[2], slow[2], fast[3], slow[3]);
        printf("adreno: %u: %s\n", step, fast[3] || slow[3] ? "TrustZone lists the GPU's zap shader" :
            fast[0] || slow[0] || fast[1] || slow[1] || fast[2] || slow[2] ?
            "TrustZone lists others but not the GPU's: step 12 will show whether that matters" :
            "TrustZone lists none, not even those the phone starts: this question tells nothing here");
        return true;
}

/* ---- step 12: the zap shader's signature ----------------------------------------------------------------- */

extern dev_t mdevlookup(int devid);
extern int mdevgetkernelrange(dev_t dev, vm_offset_t *base, uint64_t *size);


/*
 * A file of the small image that travels with the kernel -- userland/build.sh puts what is in
 * fw-gpu/ into its directory fw/ -- found in the memory of the RAM disk itself: at this point of
 * the start that image is the root if there is no system image, and not mounted at all if there
 * is one (it is /dev/md1 then; see userland/src/stage0.c).
 */
static bool image_asked_quietly;        /* image_file() says nothing: is the file there at all */

extern const uint8_t *redwood_image_file(const char *dir, const char *name, uint64_t *size, int *disk_out);
static const uint8_t *
image_file(const char *directory, const char *name, uint64_t *size)
{
        int disk = -1;
        const uint8_t *data = redwood_image_file(directory, name, size, &disk);  /* (redwood_image.c) */

        if (data != NULL) {
                if (!image_asked_quietly) {
                        printf("adreno: %u: %s/%s: %llu bytes in the image of /dev/md%d\n", step, directory, name, *size, disk);
                }
                return data;
        }
        if (!image_asked_quietly) {
                printf("adreno: %u: no %s/%s in the small image: put the phone's /vendor/firmware/%s into fw-gpu/ and run "
                    "userland/build.sh\n", step, directory, name, name);
        }
        return NULL;
}

/*
 * The first of the three calls with which Linux has a zap shader loaded (mdt_loader.c:
 * qcom_mdt_pas_init, qcom_scm.c: qcom_scm_pas_init_image): TrustZone is given the image's
 * metadata -- the ELF header and the block of hashes and certificates -- and checks the
 * signature. Nothing of the GPU is involved, so it can be asked now. The other two (where the
 * image will be, and "authenticate and start") need the 3D core powered.
 *
 * R22, what the metadata is, after the R21 battle report: TrustZone reads the buffer through
 * the header IT carries -- it parses the ELF header in the buffer and takes the hashes from
 * the phdr's own p_offset (mdt_loader.c: qcom_mdt_read_metadata puts them there; for a
 * redwood .mdt that place IS right after the header pair, so "nothing between them" was true
 * there). a640_zap.mbn declares its hashes at p_offset 0x1000: R21 packed them at 0x94, so
 * TrustZone looked at 0x1000 and found the middle of the packed block -- the -3145797 refusal
 * of the R21 report. Linux's own zap path passes the whole .mbn (qcom_scm.c:
 * qcom_scm_pas_init_image(13, fw->data, fw->size)), in which every p_offset points at the
 * real bytes by construction; 14256 bytes fit in one page of this kernel (16 KB). Both forms
 * are built here, for the round's matrix: the whole file (Linux's), and R21's packed one (the
 * .mdt's shape, kept to isolate the variable).
 *
 * The header goes in memory of ours, page-aligned and in one piece. Qualcomm's note at that
 * call says that for these calls the hypervisor itself arranges TrustZone's access to the memory
 * ("creates SHM bridges on behalf of Linux"), and that TrustZone protects the memory while it
 * works: so it is not touched again here, and never freed.
 *
 * R23, what the refusal turned out to be: not the call at all.  This platform's own Linux, on
 * its own phones, refuses ONE image with exactly -3145797 while six others pass the very same
 * boot (the spss refusal: "scm_call failed: func id 0x42000201, ret: -3145797" then
 * "Initializing image failed(rc:-22)"), and it loads a640_zap.mbn through the same three calls
 * fine.  The code is the image's, not the call's.  And the file this tree has carried since R13
 * is signed with Qualcomm's SecTools TEST certificates -- its hashes end in a chain that names
 * itself "Generated Test Attestation CA" and "General Use Test Key (for testing only)": a chain
 * a fused phone's TrustZone cannot take from anyone.  The chain's own names are read out of the
 * hashes here, before the first call is made, so the log decides the file question by itself.
 *
 * R24, the file changed: this tree ships the phone's OWN a640_zap.mbn now (the /vendor/firmware
 * file, 14256 B, sha256 627ecc6a..., "Xiaomi Attestation CA"/"Xiaomi Root CA" -- its diff against
 * the test-signed file is 3453 bytes, all inside the certificate area; the code is the same
 * bytes).  The verdict names the production chain when it sees it.
 */
/* A bounded strstr: does the first len bytes of buf carry the needle whole?  (The hashes are one blob, not a string.) */
static bool
zap_chain_has(const uint8_t *buf, uint64_t len, const char *needle)
{
        uint64_t n = strlen(needle), i, j;

        for (i = 0; i + n <= len; i++) {
                for (j = 0; j < n && buf[i + j] == (uint8_t)needle[j]; j++) {
                }
                if (j == n) {
                        return true;
                }
        }
        return false;
}

/* The zap shader's metadata in a page of ours, as TrustZone wants it; where that page is in physical memory. */
static bool
zap_header(uint64_t *phys, bool whole)
{
        const uint8_t *header;
        vm_offset_t page = 0;
        uint64_t size = 0;
        uint32_t segments, phoff, header_bytes = 0, hash_at = 0, hash_bytes = 0;

        header = image_file("fw", "a640_zap.mbn", &size);
        if (header == NULL) {
                return false;
        }
        /* a 32-bit ELF: the count of segments at 44, the table of them at 28, 32 bytes each, the file size of one at +16 */
        segments = size >= 52 + 2 * 32 ? header[44] | header[45] << 8 : 0;
        memcpy(&phoff, header + 28, sizeof(phoff));
        if (memcmp(header, "\177ELF\1", 5) != 0 || segments < 2 || segments > 16 ||
            phoff != 52 || size < phoff + 32ull * segments) {
                printf("adreno: %u: that is not such a header (%02x %02x %02x %02x, %u segments)\n", step, header[0], header[1],
                    header[2], header[3], segments);
                return false;
        }
        memcpy(&header_bytes, header + phoff + 16, sizeof(header_bytes));       /* phdr[0].p_filesz: the header pair's size */
        for (uint32_t i = 1; i < segments; i++) {
                uint32_t at = phoff + 32 * i, flags, off, len;

                memcpy(&flags, header + at + 24, sizeof(flags));
                memcpy(&off, header + at + 4, sizeof(off));
                memcpy(&len, header + at + 16, sizeof(len));
                if ((flags & 7u) == 2u || (flags & (7u << 24)) == (2u << 24)) {
                        /* the hash segment: redwood's .mdt marked it in the low bits of
                         * p_flags, this phone's .mbn marks it at 24 (mdt_loader.c:
                         * QCOM_MDT_TYPE_HASH is 2 << 24; a640_zap.mbn's hashes are
                         * phdr[1], p_flags 0x2200000 -- with only the low-bit test the
                         * port found no hashes here at all) */
                        hash_at = off;
                        hash_bytes = len;
                        break;
                }
        }
        printf("adreno: %u: an ELF of %u segments: the header pair %#x bytes, the hashes %#x at %#x, the whole file %llu bytes; "
            "the metadata is built as %s\n", step, segments, header_bytes, hash_bytes, hash_at, size,
            whole ? "the whole file, every p_offset at the real bytes (Linux's)" : "the header pair with the hashes packed "
            "behind them (R21's, kept for the matrix)");
        if (header_bytes == 0 || hash_bytes == 0 || (uint64_t)hash_at + hash_bytes > size || size > PAGE_SIZE) {
                printf("adreno: %u: that file does not make a one-page metadata the way it is asked (%llu bytes, hashes %#x at "
                    "%#x)\n", step, size, hash_bytes, hash_at);
                return false;
        }
        {
                /* R23: the file's own verdict.  The certificates the signature check runs
                 * against name their maker in plain sight inside the hashes; the scan prints
                 * the readable runs (the first few) and, bounded, looks for the test signer's
                 * exact names.  "Generated Test Attestation CA" and "General Use Test Key
                 * (for testing only)" are Qualcomm SecTools' test chain: no fused phone's
                 * TrustZone takes it, whoever calls, wherever the page sits. */
                const uint8_t *hashes = header + hash_at;
                uint32_t at, run_at = 0, run = 0;
                unsigned names = 0;
                bool test_signed = zap_chain_has(hashes, hash_bytes, "Generated Test") ||
                                   zap_chain_has(hashes, hash_bytes, "General Use Test Key");
                bool xiaomi_chain = zap_chain_has(hashes, hash_bytes, "Xiaomi Attestation CA") ||
                                    zap_chain_has(hashes, hash_bytes, "Xiaomi Root CA");

                for (at = 0; at <= hash_bytes; at++) {
                        uint8_t c = at < hash_bytes ? hashes[at] : 32;

                        if (c >= 32 && c < 127) {
                                if (run == 0) {
                                        run_at = at;
                                }
                                run++;
                                continue;
                        }
                        if (run >= 8) {
                                char word[48];
                                uint32_t i;

                                for (i = 0; i < run && i < sizeof(word) - 1; i++) {
                                        word[i] = (char)hashes[run_at + i];
                                }
                                word[i] = 0;
                                if (names < 8) {
                                        printf("adreno: %u:   the hashes carry \"%s\" (at %#x)\n", step, word,
                                            hash_at + run_at);
                                        names++;
                                }
                        }
                        run = 0;
                }
                printf("adreno: %u: THE SIGNATURE CHAIN SAYS: %s\n", step, test_signed ?
                    "A TEST KEY (Qualcomm SecTools \"Generated Test\"/\"General Use Test Key\") -- a fused phone's "
                    "TrustZone refuses this file whoever calls: a640_zap.mbn must be THIS phone's own signed file "
                    "(the stock ROM's vendor.img, vendor/firmware/)" :
                    xiaomi_chain ?
                    "THE PHONE'S OWN PRODUCTION CHAIN (\"Xiaomi Attestation CA\"/\"Xiaomi Root CA\") -- this is the "
                    "file this phone's vendor ships: TrustZone should take it" :
                    "no test-key marks and no Xiaomi marks -- judge by the names printed above");
        }
        if (kmem_alloc(kernel_map, &page, PAGE_SIZE, KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
                printf("adreno: %u: no memory\n", step);
                return false;
        }
        if (whole) {
                memcpy((void *)page, header, size);     /* the whole file: the header pair at 0, the hashes at 0x1000 */
        } else {
                memcpy((void *)page, header, header_bytes);     /* R21's packed form: the hashes at 0x94 */
                memcpy((void *)(page + header_bytes), header + hash_at, hash_bytes);
        }
        to_memory(page, PAGE_SIZE);             /* TrustZone reads memory, not this processor's cache */
        *phys = kvtophys(page);
        return true;
}

static bool
step_zap_signature(void)
{
        uint64_t header = 0, answer = ~0ull;
        int32_t result;

        announce("find the zap shader, fw/a640_zap.mbn, in the small image that came with the kernel");
        if (!zap_header(&header, true)) {
                return false;
        }
        announce("hand the header to TrustZone to check its signature (image 13); a call that may be preempted, as Linux's is");
        result = scm("to take the zap shader's signature", scm_how | SCM_SIP | SCM_PAS_INIT_IMAGE, SCM_VALUE_AND_BUFFER, PAS_GPU,
            header, 0, &answer);
        if (result == 0 && answer == 0) {
                printf("adreno: %u: TRUSTZONE ACCEPTS THE ZAP SHADER'S SIGNATURE FROM THIS KERNEL\n", step);
                return true;
        }
        printf("adreno: %u: TrustZone does not take it: the call returns %d, its answer is %#llx\n", step, result, answer);
        return false;
}

/* ---- step 13: memory for the GMU ------------------------------------------------------------------------ */

/*
 * The GMU is a Cortex-M3 with 16K of code memory and 16K of data memory of its own (written
 * through registers, step 10); the rest of its firmware and everything it shares with us it
 * reaches through the SMMU, at addresses the firmware file names: a file of blocks, each "this
 * goes at address A" or, with no data, "I need N bytes at address A" (vendor's
 * adreno_a6xx_gmu.c: a6xx_gmu_parse_fw, a6xx_gmu_load_fw). Besides those, where the vendor's
 * driver puts them: 16K for the firmware's log and a page for the table and queues of the
 * message protocol ("HFI").
 */
#define GMU_ITCM_ADDRESS        0x00000000u
#define GMU_DTCM_ADDRESS        0x00040000u     /* a6xx_gmu_fw_load: every family but the 650's */
#define GMU_TCM_SIZE            0x4000u
#define GMU_SCRATCH_ADDRESS     0x60000000u     /* the two pages at the head of the uncached range the
                                                 * firmware's own code asks for: 0x60000000 is a literal in
                                                 * its ITCM (the R14 log's step-17 fault was exactly here) */
#define GMU_SCRATCH_SIZE        0x2000u
#define GMU_LOG_ADDRESS         0x60002000u     /* after those two pages */
#define GMU_LOG_SIZE            0x4000u
#define GMU_HFI_ADDRESS         0x60006000u
#define GMU_STREAM              5
#define GMU_MATCH               1               /* entry of the stream table; 0 is kept for the 3D core */

struct gmu_firmware_block {
        uint32_t address, size, type, value;
};
#define GMU_FIRMWARE_NEEDS      1               /* types of blocks without data: memory wanted, value = bytes */
#define GMU_FIRMWARE_NEEDS_KEPT 7

static struct gpu_space gmu_space = { .name = "the GMU", .context = 1 };
static struct gpu_block gmu_blocks[12], gmu_scratch, gmu_log, gmu_hfi;
static unsigned gmu_block_count;
static const uint8_t *gmu_firmware;
static uint64_t gmu_firmware_size;
static uint32_t gmu_fw_core;           /* the loaded firmware's core version word, read back in step 14 */
static bool gmu_space_is_on, gmu_runs, gmu_talks, core_is_on, rsc_awake, cx_off;
static void gmu_post_mortem(void);

static bool
in_tcm(uint32_t address)
{
        return address - GMU_ITCM_ADDRESS < GMU_TCM_SIZE || address - GMU_DTCM_ADDRESS < GMU_TCM_SIZE;
}

static bool
step_gmu_space(void)
{
        if (!cx_ready()) {
                return false;
        }
        announce("find the GMU's firmware, fw/a640_gmu.bin, in the small image, and make the memory it asks for");
        gmu_firmware = image_file("fw", "a640_gmu.bin", &gmu_firmware_size);
        if (gmu_firmware == NULL) {
                return false;
        }
        for (uint64_t at = 0; at + sizeof(struct gmu_firmware_block) <= gmu_firmware_size;) {
                struct gmu_firmware_block block;

                memcpy(&block, gmu_firmware + at, sizeof(block));
                at += sizeof(block) + block.size;
                if (at > gmu_firmware_size) {
                        printf("adreno: %u: the file ends inside a block\n", step);
                        return false;
                }
                if (block.size != 0 || (block.type != GMU_FIRMWARE_NEEDS && block.type != GMU_FIRMWARE_NEEDS_KEPT) ||
                    in_tcm(block.address)) {
                        continue;
                }
                if (gmu_block_count == sizeof(gmu_blocks) / sizeof(gmu_blocks[0]) || (block.address & 4095) ||
                    block.value > 0x100000 || !block_new(&gmu_space, &gmu_blocks[gmu_block_count++], block.address, block.value,
                    PTE_GMU)) {
                        printf("adreno: %u: cannot make %#x bytes at %#x for the firmware\n", step, block.value, block.address);
                        return false;
                }
        }
        /*
         * The a640's firmware (GEN2) asks for nothing: the block walk above made none of the
         * memory it writes. Where its data lands is where Linux allocates the two caches for
         * the a640 family (a6xx_gmu.c: "icache" at 0x4000 and "dcache" at 0x44000, 256K less
         * 16K each); make those, once, where the file writes.
         */
        {
                static const struct {
                        uint32_t address, size;
                        const char *name;
                } pools[] = {
                        { 0x04000, 0x3f000, "icache" },
                        { 0x44000, 0x3f000, "dcache" },
                };

                for (uint64_t at = 0; at + sizeof(struct gmu_firmware_block) <= gmu_firmware_size;) {
                        struct gmu_firmware_block block;
                        bool covered = false;

                        memcpy(&block, gmu_firmware + at, sizeof(block));
                        at += sizeof(block) + block.size;
                        if (block.size == 0 || in_tcm(block.address)) {
                                continue;
                        }
                        for (unsigned i = 0; i < gmu_block_count; i++) {
                                if (block.address >= gmu_blocks[i].address &&
                                    block.address + block.size <= gmu_blocks[i].address + gmu_blocks[i].size) {
                                        covered = true;
                                        break;
                                }
                        }
                        if (covered) {
                                continue;
                        }
                        for (unsigned p = 0; p < sizeof(pools) / sizeof(pools[0]); p++) {
                                if (block.address >= pools[p].address &&
                                    block.address + block.size <= pools[p].address + pools[p].size &&
                                    gmu_block_count < sizeof(gmu_blocks) / sizeof(gmu_blocks[0]) &&
                                    block_new(&gmu_space, &gmu_blocks[gmu_block_count++], pools[p].address, pools[p].size, PTE_GMU)) {
                                        printf("adreno: %u: %#x bytes at %#x: the %s the a640 family's firmware fills (where Linux puts it)\n",
                                            step, pools[p].size, pools[p].address, pools[p].name);
                                        covered = true;
                                        break;
                                }
                        }
                        if (!covered) {
                                printf("adreno: %u: the firmware has %#x bytes at %#x, where neither it nor this driver asked for memory\n",
                                    step, block.size, block.address);
                                return false;
                        }
                }
        }
        if (!block_new(&gmu_space, &gmu_scratch, GMU_SCRATCH_ADDRESS, GMU_SCRATCH_SIZE, PTE_GMU) ||
            !block_new(&gmu_space, &gmu_log, GMU_LOG_ADDRESS, GMU_LOG_SIZE, PTE_GMU) ||
            !block_new(&gmu_space, &gmu_hfi, GMU_HFI_ADDRESS, PAGE_SIZE, PTE_GMU)) {
                return false;
        }
        printf("adreno: %u: %#x bytes at %#x: the two pages the GMU's own code asks for -- 0x60000000 is a literal in its ITCM; R14 left them unmapped and the SMMU refused the GMU there at step 17\n",
            step, GMU_SCRATCH_SIZE, GMU_SCRATCH_ADDRESS);

        announce("give the SMMU's tables to a context bank nobody used before, and send the GMU's requests (stream 5) "
            "through it: the first writes to the SMMU, the global ones (cbar, cba2r, s2cr, smr) through the hypervisor.  "
            "The R15 fault was the SMMU refusing the GMU at 0x60000000 with an access-flag fault -- at an address this "
            "driver maps with the flag set: an answer out of a translation nobody living made, cached by whoever used the "
            "bank before the kernel and not evicted by any invalidation of ours.  A bank nobody used cannot answer that "
            "way; Android's own GMU bank (1) is the fallback if the hypervisor refuses the swap");
        if (!smmu_layout()) {
                return false;
        }
        gmu_space_is_on = true;         /* whatever of it has been written by the time something fails is taken back */
        for (unsigned i = 0;; i++) {
                static const uint32_t candidates[] = { 3, 1 };     /* 3: nobody's (2 is the 3D core's second stream's); 1: Android's own GMU bank */

                gmu_space.context = candidates[i] < smmu_contexts ? candidates[i] : 1;
                printf("adreno: %u: trying context bank %u for the GMU%s\n", step, gmu_space.context,
                    gmu_space.context == 1 ? " (the fallback: Android's own GMU bank)" : "");
                if (space_on(&gmu_space, GMU_MATCH, GMU_STREAM, SMMU_MASK_GMU)) {
                        break;
                }
                if (i + 1 == sizeof(candidates) / sizeof(candidates[0])) {
                        return false;
                }
        }
        /* the two addresses the R15 story turned on: the one the SMMU refused, and the one the whole
         * conversation ran on */
        space_show_walk(&gmu_space, GMU_SCRATCH_ADDRESS);
        space_show_walk(&gmu_space, GMU_HFI_ADDRESS);
        return true;
}

/* ---- step 14: the GMU's firmware ----------------------------------------------------------------------- */

#define GMU_ITCM                GMU_WORD(0x0c00)

static bool
step_gmu_firmware(void)
{
        uint32_t wrong = 0;

        if (!cx_ready() || gmu_firmware == NULL) {
                return false;
        }
        announce("load the GMU's firmware: its first 16K of code and of data through the GMU's registers (0x2c6d000, "
            "0x2c71000), the rest into the memory just made");
        put(&gmu, "cm3_sysreset", GMU_CM3_SYSRESET, 1);
        for (int pass = 0; pass < 2; pass++) {
                for (uint64_t at = 0; at + sizeof(struct gmu_firmware_block) <= gmu_firmware_size;) {
                        struct gmu_firmware_block block;
                        const uint8_t *data = gmu_firmware + at + sizeof(block);

                        memcpy(&block, gmu_firmware + at, sizeof(block));
                        at += sizeof(block) + block.size;
                        if (block.size == 0) {
                                continue;
                        }
                        if (in_tcm(block.address) && in_tcm(block.address + block.size - 1)) {
                                uint32_t registers = block.address < GMU_DTCM_ADDRESS ? GMU_ITCM + block.address - GMU_ITCM_ADDRESS :
                                    GMU_DTCM + block.address - GMU_DTCM_ADDRESS;

                                for (uint32_t offset = 0; offset + 4 <= block.size; offset += 4) {
                                        uint32_t word;

                                        memcpy(&word, data + offset, sizeof(word));
                                        if (pass == 0) {
                                                wr(gmu.base + registers + offset, word);
                                        } else if (rd(gmu.base + registers + offset) != word) {
                                                wrong++;
                                        }
                                }
                                if (pass == 1) {
                                        printf("adreno: %u: %#x bytes at the GMU's %#x, through its registers\n", step, block.size,
                                            block.address);
                                }
                                continue;
                        }
                        for (unsigned i = 0; pass == 0 && i <= gmu_block_count; i++) {
                                if (i == gmu_block_count) {
                                        printf("adreno: %u: the firmware has %#x bytes for %#x, where it asked for no memory\n", step,
                                            block.size, block.address);
                                        return false;
                                }
                                if (block.address >= gmu_blocks[i].address &&
                                    block.address + block.size <= gmu_blocks[i].address + gmu_blocks[i].size) {
                                        memcpy((void *)(gmu_blocks[i].memory + (block.address - gmu_blocks[i].address)), data, block.size);
                                        to_memory(gmu_blocks[i].memory, gmu_blocks[i].size);
                                        printf("adreno: %u: %#x bytes at the GMU's %#x, in memory\n", step, block.size, block.address);
                                        break;
                                }
                        }
                }
        }
        snprintf(last_write, sizeof(last_write), "step %u wrote the GMU's firmware into its code and data memory", step);
        if (wrong) {
                printf("adreno: %u: %u words of the GMU's own memory do not read back as written\n", step, wrong);
                return false;
        }
        gmu_fw_core = rd(gmu.base + GMU_DTCM_VERSIONS);
        printf("adreno: %u: THE GMU'S FIRMWARE IS LOADED; its versions, read back from the GMU: core %#x, power %#x, HFI %#x\n",
            step, gmu_fw_core, rd(gmu.base + GMU_DTCM_VERSIONS + 8), rd(gmu.base + GMU_DTCM_VERSIONS + 16));
        return true;
}

/* ---- step 15: start the GMU ---------------------------------------------------------------------------- */

#define GMU_ICACHE_CONFIG       GMU_WORD(0x4c00)
#define GMU_DCACHE_CONFIG       GMU_WORD(0x4c01)
#define GMU_SYS_BUS_CONFIG      GMU_WORD(0x4c0f)
#define GMU_PWR_COL_INTER_FRAME_CTRL GMU_WORD(0x50c0)
#define GMU_RPMH_CTRL           GMU_WORD(0x50e8)
#define GMU_RPMH_CTRL_ALL       0xf11           /* the RPMh interface, and every vote the firmware may cast */
#define GMU_CX_FAL_INTF         GMU_WORD(0x50f0)
#define GMU_CX_FALNEXT_INTF     GMU_WORD(0x50f1)
#define GMU_PWR_COL_CP_MSG      GMU_WORD(0x5100)        /* at start: where the log is, and its pages - 1 */
#define GMU_PWR_COL_CP_RESP     GMU_WORD(0x5101)        /* ... and how far it was written before */
#define GMU_HFI_SFR_ADDR        GMU_WORD(0x5182)        /* at start: which GPU this is */
#define GMU_HFI_QTBL_INFO       GMU_WORD(0x5184)
#define GMU_HFI_QTBL_ADDR       GMU_WORD(0x5185)
#define GMU_HFI_CTRL_INIT       GMU_WORD(0x5186)
#define GMU_GMU2HOST_INTR_CLR   GMU_WORD(0x5191)
#define GMU_GMU2HOST_INTR_INFO  GMU_WORD(0x5192)        /* bit 0: a message for us; bit 31: the 3D core is ours */
#define GMU_HOST2GMU_INTR_SET   GMU_WORD(0x5194)        /* bit 0: a message for the GMU; bit 30, 31: see step 17 */
#define GMU_RSCC_CONTROL_REQ    GMU_WORD(0x9307)
#define GMU_AO_AHB_FENCE_CTRL   GMU_WORD(0x9310)
#define GMU_AHB_FENCE_RANGE_0   GMU_WORD(0x9311)
#define GMU_CHIP                0x06040001      /* dt: "qcom,adreno-640.1" */
#define GMU_STARTED_MASK        0x1ff           /* of fw_init_result, for firmware newer than core 0x20010004 */
#define GMU_STARTED             0x100
#define GMU_BOOTED_BABEFACE     0xbabeface      /* the whole verdict of an older firmware (vendor's a6xx_gmu_device_start) */
#define GMU_FAILED              0xe00

/* The table at the head of the HFI page and the three queues after it, as the vendor's driver lays them out. */
struct hfi_queue {
        uint32_t status, start, type, words, message_words, unused[5], read, write;
};
struct hfi_table {
        uint32_t version, words, first_queue, queue_words, queues, active_queues;
        struct hfi_queue queue[17];
};
#define HFI_QUEUES              3               /* 0: ours to the GMU; 1: its answers; 2: its debugging messages */
#define HFI_QUEUE_WORDS         1024
#define HFI_QUEUE_AT(n)         (((uint32_t)sizeof(struct hfi_table) + 15) / 16 * 16 + 4096 * (n))

static volatile struct hfi_table *hfi;  /* the uncached view */

/*
 * Everything the vendor's driver writes before it lets the processor go (a6xx_gmu_register_config,
 * a6xx_gmu_power_config), in its order; then out of reset (a6xx_gmu_device_start).
 */
static bool
step_gmu_start(void)
{
        static const uint8_t priority[HFI_QUEUES] = { 10, 10, 40 };
        /*
         * What "started" looks like is the firmware's own business: the vendor's
         * a6xx_gmu_device_start waits for the whole word 0xbabeface on firmware whose
         * core version is 0x20010004 or older, and for bit 8 only on newer ones.  Ours
         * (step 14 says which) is 0x20000105, an older one -- R13 waited for bit 8 all
         * the same, and so timed out against a firmware that had said babeface and was
         * sitting there waiting for its first message.
         */
        uint32_t mask = gmu_fw_core <= 0x20010004 ? 0xffffffffu : GMU_STARTED_MASK;
        uint32_t value = gmu_fw_core <= 0x20010004 ? GMU_BOOTED_BABEFACE : GMU_STARTED;
        uint32_t result = 0;
        bool started = false;

        if (!cx_ready() || !gmu_space_is_on) {
                return false;
        }
        hfi = (volatile struct hfi_table *)block_uncached(&gmu_hfi);
        if (hfi == NULL) {
                printf("adreno: %u: the page for the messages is not in one piece\n", step);
                return false;
        }
        hfi->words = sizeof(struct hfi_table) / 4;
        hfi->first_queue = 6;
        hfi->queue_words = sizeof(struct hfi_queue) / 4;
        hfi->queues = hfi->active_queues = HFI_QUEUES;
        for (unsigned i = 0; i < HFI_QUEUES; i++) {
                hfi->queue[i].start = GMU_HFI_ADDRESS + HFI_QUEUE_AT(i);
                hfi->queue[i].type = i | (uint32_t)priority[i] << 8;
                hfi->queue[i].words = HFI_QUEUE_WORDS;
                hfi->queue[i].status = 1;
        }

        announce("tell the GMU where things are, by its registers: the message table, the log, which GPU this is; its "
            "caches and its use of RPMh; then let its processor out of reset");
        if (get(&gmu, "cm3_sysreset", GMU_CM3_SYSRESET) != 1) {
                printf("adreno: %u: the GMU's processor is not held in reset: not configuring it while it runs\n", step);
                return false;
        }
        /*
         * Our own leaving (gpu_block_stop) hands the GMU's RPMh channel to the always-on sequencer, and
         * until it is taken back nothing the GMU asks of RPMh gets there: the vendor's
         * a6xx_rscc_wakeup_sequence, before the firmware starts.
         */
        rscc_show();
        if (rd(rscc.base + RSCC_RSC_STATUS0_DRV0) & RSCC_ASLEEP) {
                put(&gmu, "rscc_control_req", GMU_RSCC_CONTROL_REQ, 2);
                if (!wait_for(&gmu, GMU_RSCC_CONTROL_ACK, 2, 2, 100) || !wait_for(&rscc, RSCC_SEQ_BUSY_DRV0, ~0u, 0, 100)) {
                        printf("adreno: %u: the GMU's RPMh channel does not wake\n", step);
                }
                get(&gmu, "rscc_control_ack", GMU_RSCC_CONTROL_ACK);
                put(&gmu, "rscc_control_req", GMU_RSCC_CONTROL_REQ, 0);
                get(&rscc, "rsc_status0_drv0", RSCC_RSC_STATUS0_DRV0);
                get(&rscc, "seq_busy_drv0", RSCC_SEQ_BUSY_DRV0);
        }
        rsc_awake = true;
        /* the "FAL10" veto interfaces are the a650 family's (a6xx_gmu_fw_start): not written here */
        put(&gmu, "general_7", GMU_GENERAL_7, 1);
        put(&gmu, "cm3_fw_init_result", GMU_CM3_FW_INIT_RESULT, 0);
        put(&gmu, "cm3_boot_config", GMU_CM3_BOOT_CONFIG, 2);
        put(&gmu, "hfi_qtbl_addr", GMU_HFI_QTBL_ADDR, GMU_HFI_ADDRESS);
        put(&gmu, "hfi_qtbl_info", GMU_HFI_QTBL_INFO, 1);
        put(&gmu, "ahb_fence_range_0", GMU_AHB_FENCE_RANGE_0, 1u << 31 | (0xa << 2) << 18 | 0x8a0);
        put(&gmu, "cm3_cfg", GMU_CM3_CFG, 0x4052);
        put(&gbif, "halt", GBIF_HALT, 0);
        put(&gmu, "pwr_col_cp_resp", GMU_PWR_COL_CP_RESP, 0);
        put(&gmu, "hfi_sfr_addr", GMU_HFI_SFR_ADDR, GMU_CHIP);
        put(&gmu, "pwr_col_cp_msg", GMU_PWR_COL_CP_MSG, GMU_LOG_ADDRESS | (GMU_LOG_SIZE / 4096 - 1));
        put(&gmu, "sys_bus_config", GMU_SYS_BUS_CONFIG, 1);
        put(&gmu, "icache_config", GMU_ICACHE_CONFIG, 1);
        put(&gmu, "dcache_config", GMU_DCACHE_CONFIG, 1);
        put(&gmu, "pwr_col_inter_frame_ctrl", GMU_PWR_COL_INTER_FRAME_CTRL, 0x9c40400);
        set(&gmu, "rpmh_ctrl", GMU_RPMH_CTRL, GMU_RPMH_CTRL_ALL);
        put(&gmu, "gmu2host_intr_clr", GMU_GMU2HOST_INTR_CLR, 0xffffffff);
        /* whatever fault the bank latched until now is the last owner's story, not ours: clear it, so
         * anything the log shows from here on happened while the GMU ran (R16) */
        put(&smmu, "cb fsr", smmu_context(&gmu_space) + SMMU_CB_FSR, SMMU_FSR_ALL);

        printf("adreno: %u: this firmware's verdict of started: (result & %#x) == %#x (its core version is %#x)\n", step, mask,
            value, gmu_fw_core);
        gmu_runs = true;
        put(&gmu, "cm3_sysreset", GMU_CM3_SYSRESET, 0);
        for (int i = 0; i < 50000 && (result & mask) != value && !(result & GMU_FAILED); i++) {
                sleep_us(100);
                result = rd(gmu.base + GMU_CM3_FW_INIT_RESULT);
        }
        started = (result & mask) == value;
        get(&gmu, "cm3_fw_init_result", GMU_CM3_FW_INIT_RESULT);
        get(&gmu, "gmu2host_intr_info", GMU_GMU2HOST_INTR_INFO);
        get(&gmu, "ao_host_interrupt_status", GMU_AO_HOST_INTERRUPT_STATUS);
        get(&gmu, "hfi_ctrl_status", GMU_HFI_CTRL_STATUS);
        if (space_faulted(&gmu_space) || !started) {
                printf("adreno: %u: the GMU's firmware does not come up\n", step);
                gmu_post_mortem();
                return false;
        }
        printf("adreno: %u: THE GMU'S FIRMWARE RUNS\n", step);
        return true;
}

/* ---- step 16: the GMU's way to RPMh, and its message queues ------------------------------------------------ */

/*
 * The GMU raises and drops the 3D core's supply by itself, through command slots of its own
 * (step 7) and a sequencer in the always-on part of the SoC, "PDC". On this SoC the always-on
 * processor programs the PDC; left to us are its two enabling registers and the GMU side's
 * little program (vendor's a6xx_load_pdc_ucode with pdc_in_aop, a6xx_load_rsc_ucode: after the
 * firmware has started and before the message queues are).
 */
static struct block pdc = { "pdc", 0x0b290000, 0x10000 };       /* dt sm8150: qcom,gmu "gmu_pdc" */
static struct block pdc_seq = { "pdc_seq", 0x0b490000, 0x10000 };       /* dt sm8150: qcom,gmu "gmu_pdc_seq": the sequencer's own program */
#define PDC_GPU_ENABLE          (4 * 0x1140)
#define PDC_GPU_SEQ_START_ADDR  (4 * 0x1148)
#define PDC_TCS1_CONTROL        (4 * 0x1572)    /* a6xx.xml, domain A6XX_PDC */
#define PDC_TCS1_CMD_ENABLE     (4 * 0x1573)
#define PDC_TCS1_CMD_WAIT       (4 * 0x1574)
#define PDC_TCS1_CMD0_MSGID     (4 * 0x1575)
#define PDC_TCS1_CMD0_ADDR      (4 * 0x1576)
#define PDC_TCS1_CMD0_DATA      (4 * 0x1577)
#define PDC_TCS3_CONTROL        (4 * 0x15d6)
#define PDC_TCS3_CMD_ENABLE     (4 * 0x15d7)
#define PDC_TCS3_CMD_WAIT       (4 * 0x15d8)
#define PDC_TCS3_CMD0_MSGID     (4 * 0x15d9)
#define PDC_TCS3_CMD0_ADDR      (4 * 0x15da)
#define PDC_TCS3_CMD0_DATA      (4 * 0x15db)
#define PDC_GPU_SEQ_MEM_0       (4 * 0x0)       /* a6xx.xml, domain A6XX_PDC_GPU_SEQ: at the head of pdc_seq */
#define PDC_SYNC_ADDRESS        0x30090u        /* the a630 family's sync register, as the PDC's commands name it (a618/a640: 0x30090) */
#define RSCC_PDC_SEQ_START_ADDR (4 * 0x008)
#define RSCC_PDC_MATCH_VALUE_LO (4 * 0x009)
#define RSCC_PDC_MATCH_VALUE_HI (4 * 0x00a)
#define RSCC_HIDDEN_TCS_CMD0_ADDR (4 * 0x00d)
#define RSCC_HIDDEN_TCS_CMD0_DATA (4 * 0x00e)
#define RSCC_OVERRIDE_START_ADDR (4 * 0x100)

static bool
step_gmu_queues(void)
{
        static const uint32_t program[5] = { 0xa7a506a0, 0xa1e6a6e7, 0xa2e081e1, 0xe9a982e2, 0x0020e8a8 };   /* the a630 family's (a6xx_gmu.c) */

        if (!cx_ready() || !gmu_runs) {
                return false;
        }
        /*
         * On sm8150 the always-on processor has NOT programmed the GPU's part of the PDC (that
         * is the a650 family's "pdc_in_aop" luxury): the whole of the vendor's a6xx_load_pdc_ucode
         * and a6xx_load_rsc_ucode, a630 family's words, is here (a6xx_gmu.c). First the
         * sequencer's own program, into its memory at 0xb490000.
         */
        {
                static const uint32_t pdc_program[5] = { 0xfebea1e1, 0xa5a4a3a2, 0x8382a6e0, 0xbce3e284, 0x002081fc };

                announce("program the GPU's part of the always-on sequencer: its own memory (pdc_seq, 0xb490000), then its "
                    "command banks (pdc, TCS1 and TCS3)");
                for (unsigned i = 0; i < 5; i++) {
                        put(&pdc_seq, "gpu_seq_mem", PDC_GPU_SEQ_MEM_0 + 4 * i, pdc_program[i]);
                }
                /* TCS1: the three commands of the power-up bank */
                put(&pdc, "tcs1_cmd_enable_bank", PDC_TCS1_CMD_ENABLE, 7);
                put(&pdc, "tcs1_cmd_wait_for_cmpl", PDC_TCS1_CMD_WAIT, 0);
                put(&pdc, "tcs1_control", PDC_TCS1_CONTROL, 0);
                put(&pdc, "tcs1_cmd0_msgid", PDC_TCS1_CMD0_MSGID, 0x10108);
                put(&pdc, "tcs1_cmd0_addr", PDC_TCS1_CMD0_ADDR, 0x30010);
                put(&pdc, "tcs1_cmd0_data", PDC_TCS1_CMD0_DATA, 1);
                put(&pdc, "tcs1_cmd1_msgid", PDC_TCS1_CMD0_MSGID + 4, 0x10108);
                put(&pdc, "tcs1_cmd1_addr", PDC_TCS1_CMD0_ADDR + 4, 0x30000);
                put(&pdc, "tcs1_cmd1_data", PDC_TCS1_CMD0_DATA + 4, 0);
                put(&pdc, "tcs1_cmd2_msgid", PDC_TCS1_CMD0_MSGID + 8, 0x10108);
                put(&pdc, "tcs1_cmd2_addr", PDC_TCS1_CMD0_ADDR + 8, PDC_SYNC_ADDRESS);
                put(&pdc, "tcs1_cmd2_data", PDC_TCS1_CMD0_DATA + 8, 0);
                /* TCS3: the three commands of the power-down bank */
                put(&pdc, "tcs3_cmd_enable_bank", PDC_TCS3_CMD_ENABLE, 7);
                put(&pdc, "tcs3_cmd_wait_for_cmpl", PDC_TCS3_CMD_WAIT, 0);
                put(&pdc, "tcs3_control", PDC_TCS3_CONTROL, 0);
                put(&pdc, "tcs3_cmd0_msgid", PDC_TCS3_CMD0_MSGID, 0x10108);
                put(&pdc, "tcs3_cmd0_addr", PDC_TCS3_CMD0_ADDR, 0x30010);
                put(&pdc, "tcs3_cmd0_data", PDC_TCS3_CMD0_DATA, 2);
                put(&pdc, "tcs3_cmd1_msgid", PDC_TCS3_CMD0_MSGID + 4, 0x10108);
                put(&pdc, "tcs3_cmd1_addr", PDC_TCS3_CMD0_ADDR + 4, 0x30000);
                put(&pdc, "tcs3_cmd1_data", PDC_TCS3_CMD0_DATA + 4, 3);   /* the a630 family's (a618/a619/a650 say 2) */
                put(&pdc, "tcs3_cmd2_msgid", PDC_TCS3_CMD0_MSGID + 8, 0x10108);
                put(&pdc, "tcs3_cmd2_addr", PDC_TCS3_CMD0_ADDR + 8, PDC_SYNC_ADDRESS);
                put(&pdc, "tcs3_cmd2_data", PDC_TCS3_CMD0_DATA + 8, 3);
                put(&pdc, "gpu_seq_start_addr", PDC_GPU_SEQ_START_ADDR, 0);
                put(&pdc, "gpu_enable", PDC_GPU_ENABLE, 0x80000001);
                get(&pdc, "gpu_enable", PDC_GPU_ENABLE);
        }

        announce("give the GMU's RPMh channel its sleep and wake program (the GPU RSC, inside the GMU's window at " 
            "+0x23000), unless it has it");
        if (get(&rscc, "seq_mem_0_drv0", RSCC_SEQ_MEM_0_DRV0) == program[0] &&
            get(&rscc, "hidden_tcs_cmd2_data", RSCC_HIDDEN_TCS_CMD0_DATA + 4 * 4) == 0x80000000) {
                goto programmed;
        }
        put(&rscc, "rsc_status0_drv0", RSCC_RSC_STATUS0_DRV0, 1u << 24);
        put(&rscc, "pdc_slave_id_drv0", RSCC_PDC_SLAVE_ID_DRV0, 1);
        put(&rscc, "hidden_tcs_cmd0_data", RSCC_HIDDEN_TCS_CMD0_DATA, 0);
        put(&rscc, "hidden_tcs_cmd0_addr", RSCC_HIDDEN_TCS_CMD0_ADDR, 0);
        put(&rscc, "hidden_tcs_cmd1_data", RSCC_HIDDEN_TCS_CMD0_DATA + 4 * 2, 0);
        put(&rscc, "hidden_tcs_cmd1_addr", RSCC_HIDDEN_TCS_CMD0_ADDR + 4 * 2, 0);
        put(&rscc, "hidden_tcs_cmd2_data", RSCC_HIDDEN_TCS_CMD0_DATA + 4 * 4, 0x80000000);    /* every family but the a740's (a6xx_gmu.c) */
        put(&rscc, "hidden_tcs_cmd2_addr", RSCC_HIDDEN_TCS_CMD0_ADDR + 4 * 4, 0);
        put(&rscc, "override_start_addr", RSCC_OVERRIDE_START_ADDR, 0);
        put(&rscc, "pdc_seq_start_addr", RSCC_PDC_SEQ_START_ADDR, 0x4520);
        put(&rscc, "pdc_match_value_lo", RSCC_PDC_MATCH_VALUE_LO, 0x4510);
        put(&rscc, "pdc_match_value_hi", RSCC_PDC_MATCH_VALUE_HI, 0x4514);
        for (unsigned i = 0; i < 5; i++) {
                put(&rscc, "seq_mem_drv0", RSCC_SEQ_MEM_0_DRV0 + 4 * i, program[i]);
        }
        get(&rscc, "seq_mem_0_drv0", RSCC_SEQ_MEM_0_DRV0);

programmed:
        announce("ask the GMU to start its message queues");
        put(&gmu, "hfi_ctrl_init", GMU_HFI_CTRL_INIT, 1);
        if (!wait_for(&gmu, GMU_HFI_CTRL_STATUS, 1, 1, 500)) {
                get(&gmu, "hfi_ctrl_status", GMU_HFI_CTRL_STATUS);
                get(&gmu, "cm3_fw_init_result", GMU_CM3_FW_INIT_RESULT);
                space_faulted(&gmu_space);
                printf("adreno: %u: the GMU does not start its message queues\n", step);
                return false;
        }
        get(&gmu, "hfi_ctrl_status", GMU_HFI_CTRL_STATUS);
        get(&gmu, "hfi_version_info", GMU_HFI_VERSION_INFO);
        gmu_talks = true;
        printf("adreno: %u: THE GMU'S MESSAGE QUEUES ARE UP\n", step);
        return !space_faulted(&gmu_space);
}

/* ---- step 17: talk to the GMU; the 3D core powered ---------------------------------------------------------- */

/*
 * A message is words: the first says which (bits 7:0), how many words (15:8), a request or an
 * answer (19:16), and a number (31:20) that the answer repeats in its second word; the third word
 * of an answer is the GMU's verdict, 0 for done. Vendor's adreno_a6xx_hfi.c, Linux a6xx_hfi.c.
 */
#define HFI_BW_TABLE            3
#define HFI_PERF_TABLE          4
#define HFI_START               10
#define HFI_CORE_FW_START       14
#define HFI_GX_BW_PERF_VOTE     30
#define HFI_PREPARE_SLUMBER     33
#define HFI_ERROR               100
#define HFI_ANSWER              1               /* in bits 19:16 */
#define HFI_NO_INDEX            0xff

struct hfi_perf_table {
        uint32_t header, gpu_levels, gmu_levels;
        struct {
                uint32_t vote, acd, khz;
        } gpu[16];
        struct {
                uint32_t vote, khz;
        } gmu[4];
};
struct hfi_bw_table {
        uint32_t header, levels, cnoc_commands, ddr_commands, cnoc_wait, ddr_wait;
        uint32_t cnoc_address[6], cnoc_data[2][6], ddr_address[8], ddr_data[16][8];
};

static uint32_t hfi_number;
static bool post_mortem_done;

/* Everything waiting in a queue of the GMU's; true if the answer to the message numbered `number` was among it. */
static bool
hfi_receive(unsigned which, uint32_t number, uint32_t *verdict)
{
        volatile struct hfi_queue *queue = &hfi->queue[which];
        volatile uint32_t *data = (volatile uint32_t *)((vm_offset_t)hfi + HFI_QUEUE_AT(which));
        bool answered = false;

        while (queue->read != queue->write) {
                uint32_t message[8] = { 0 }, at = queue->read % HFI_QUEUE_WORDS, words = data[at] >> 8 & 0xff;

                for (uint32_t i = 0; i < words; i++) {
                        if (i < 8) {
                                message[i] = data[at];
                        }
                        at = (at + 1) % HFI_QUEUE_WORDS;
                }
                queue->read = (at + 3) / 4 * 4 % HFI_QUEUE_WORDS;
                if ((message[0] >> 16 & 0xf) == HFI_ANSWER && message[1] >> 20 == number) {
                        *verdict = message[2];
                        answered = true;
                } else {
                        message[7] = 0;
                        if ((message[0] & 0xff) == HFI_ERROR) {         /* vendor's adreno_a6xx_receive_err_req */
                                printf("adreno: %u: THE GMU REPORTS AN ERROR: %u, %u, \"%s\"\n", step, message[1] >> 16,
                                    message[1] & 0xffff, (const char *)&message[2]);
                        } else {
                                printf("adreno: %u: the GMU says (queue %u, %u words): %#x %#x %#x %#x %#x %#x\n", step, which, words,
                                    message[0], message[1], message[2], message[3], message[4], message[5]);
                        }
                }
                if (words == 0) {
                        break;
                }
        }
        return answered;
}

/* A word of the GMU's own data, by the GMU's address for it; only what lies in memory of ours. */
static uint32_t
gmu_data(uint32_t address)
{
        for (unsigned i = 0; i < gmu_block_count; i++) {
                if (address - (uint32_t)gmu_blocks[i].address < gmu_blocks[i].size - 3) {
                        vm_offset_t at = gmu_blocks[i].memory + (address - (uint32_t)gmu_blocks[i].address);

                        to_memory(at, 4);               /* what the GMU wrote, not what our cache remembers */
                        return *(volatile uint32_t *)at;
                }
        }
        return 0xdeadbeef;
}

/*
 * What can be seen of a GMU that has stopped answering: the tail of its log (16 bytes an event:
 * which, the crystal's counter, two values; the firmware's function at 0x1a98; where it writes
 * next is in pwr_col_cp_resp), its record of what it was asked for and what it has (firmware
 * 1.8's data at 0x108454: +0x28 is what is in flight, +0x40.. what is in force), its RPMh
 * channel, and the 3D core's switch and PLLs, which only the GMU's firmware writes.
 */
#define GMU_STATE_ADDRESS       0x108454

static void
gmu_post_mortem(void)
{
        uint32_t next = rd(gmu.base + GMU_PWR_COL_CP_RESP) & 0xffff, entries = GMU_LOG_SIZE / 16;
        const volatile uint32_t *log = (const volatile uint32_t *)gmu_log.memory;

        to_memory(gmu_log.memory, GMU_LOG_SIZE);
        printf("adreno: %u: the GMU's log (event, time, two values), %u events in, the last 40:\n", step, next);
        for (uint32_t i = next > 40 ? next - 40 : 0; i < next && i + 4 <= entries; i += 4) {
                printf("adreno: %u:   %x %x %x %x | %x %x %x %x | %x %x %x %x | %x %x %x %x\n", step,
                    log[4 * i], log[4 * i + 1], log[4 * i + 2], log[4 * i + 3],
                    log[4 * i + 4], log[4 * i + 5], log[4 * i + 6], log[4 * i + 7],
                    log[4 * i + 8], log[4 * i + 9], log[4 * i + 10], log[4 * i + 11],
                    log[4 * i + 12], log[4 * i + 13], log[4 * i + 14], log[4 * i + 15]);
        }
        {
                const volatile uint32_t *scratch = (const volatile uint32_t *)gmu_scratch.memory;
                uint32_t live = 0;

                to_memory(gmu_scratch.memory, GMU_SCRATCH_SIZE);
                for (uint32_t i = 0; i < GMU_SCRATCH_SIZE / 4; i++) {
                        live += scratch[i] != 0;
                }
                printf("adreno: %u: the GMU's own two pages at %#x: %u of %u words written; the first 16:\n",
                    step, GMU_SCRATCH_ADDRESS, live, GMU_SCRATCH_SIZE / 4);
                for (uint32_t i = 0; i < 16; i += 8) {
                        printf("adreno: %u:   %x %x %x %x %x %x %x %x\n", step,
                            scratch[i], scratch[i + 1], scratch[i + 2], scratch[i + 3],
                            scratch[i + 4], scratch[i + 5], scratch[i + 6], scratch[i + 7]);
                }
        }
        for (uint32_t i = 0; i < 24; i += 8) {
                printf("adreno: %u: the GMU's state +%#x: %x %x %x %x %x %x %x %x\n", step, 4 * i,
                    gmu_data(GMU_STATE_ADDRESS + 4 * i), gmu_data(GMU_STATE_ADDRESS + 4 * i + 4),
                    gmu_data(GMU_STATE_ADDRESS + 4 * i + 8), gmu_data(GMU_STATE_ADDRESS + 4 * i + 12),
                    gmu_data(GMU_STATE_ADDRESS + 4 * i + 16), gmu_data(GMU_STATE_ADDRESS + 4 * i + 20),
                    gmu_data(GMU_STATE_ADDRESS + 4 * i + 24), gmu_data(GMU_STATE_ADDRESS + 4 * i + 28));
        }
        rscc_show();
        get(&gpucc, "gx_gdscr", GPUCC_GX_GDSCR);
        get(&gpucc, "gx_sw_reset", GPUCC_GX_SW_RESET);
        get(&gpucc, "gx_domain_misc", GPUCC_GX_DOMAIN_MISC);
        get(&gpucc, "pll0 mode", GPUCC_PLL0_MODE);
        get(&gpucc, "pll0 multiplier", GPUCC_PLL0_MODE + 4);
        get(&gpucc, "pll1 mode", GPUCC_PLL1_MODE);
        get(&gpucc, "pll1 multiplier", GPUCC_PLL1_MODE + 4);
        get(&gmu, "rpmh_power_state", GMU_RPMH_POWER_STATE);
        get(&gmu, "sptprac_pwr_clk_status", GMU_SPTPRAC_PWR_CLK_STATUS);
        get(&gmu, "ao_host_interrupt_status", GMU_AO_HOST_INTERRUPT_STATUS);
        get(&gmu, "ahb_fence_status", GMU_AHB_FENCE_STATUS);
}

/* Send a message whose first word is yet to be made and wait for the GMU's verdict on it. */
static bool
hfi_send(const char *what, uint32_t id, uint32_t *message, uint32_t words)
{
        volatile struct hfi_queue *queue = &hfi->queue[0];
        volatile uint32_t *data = (volatile uint32_t *)((vm_offset_t)hfi + HFI_QUEUE_AT(0));
        uint32_t at = queue->write % HFI_QUEUE_WORDS, number = ++hfi_number & 0xfff, verdict = 0, waiting = 0, turns = 0;
        /* the vote makes the GMU run the whole ramp -- RPMh round trips, the PDC's sequencer -- and the
         * R15 phone answered this very message only after the asking was given up: its answer sat in
         * the answer queue when 'slumber' went out, verdict 0, and the GX switch read powered at the
         * leave-taking.  A slow answer is not a refusal; the budget for it is ten seconds, and from
         * the core power step on every message carries the same (R17: R16 wrote the budget for the
         * vote alone; its neighbours keep the short one no longer). */
        uint32_t budget = step >= 17 || id == HFI_GX_BW_PERF_VOTE ? 100000 : 10000;
        bool answered = false;

        message[0] = id | words << 8 | number << 20;
        printf("adreno: %u: about to send the GMU %s: message %u, %u words, number %u\n", step, what, id, words, number);
        snprintf(last_write, sizeof(last_write), "step %u sent the GMU message %u (%s)", step, id, what);
        hold(true);
        for (uint32_t i = 0; i < (words + 3) / 4 * 4; i++) {
                data[at] = i < words ? message[i] : 0xfafafafa;
                at = (at + 1) % HFI_QUEUE_WORDS;
        }
        queue->write = at;
        settle();
        space_forget_refusals();               /* each exchange counts its own refusals */
        wr(gmu.base + GMU_HOST2GMU_INTR_SET, 1);
        for (uint32_t i = 0; i < budget && !answered; i++) {
                turns = i + 1;
                sleep_us(100);
                waiting = rd(gmu.base + GMU_GMU2HOST_INTR_INFO);
                if (waiting & 1) {
                        wr(gmu.base + GMU_GMU2HOST_INTR_CLR, 1);
                        answered = hfi_receive(1, number, &verdict);
                }
                /* R17, the log's confession read whole: the wait never ran.  The firmware's verdict
                 * of started, 0xbabeface, stands with bits 9 and 11 up -- 0xa00 -- and GMU_FAILED
                 * is 0xe00: the healthy steady state read as a failure, and every wait ended on
                 * its first turn.  The tables went through only because the GMU answers those
                 * within the first 100 us; the vote, which takes seconds, never stood a chance.
                 * A failure is a word that is NOT the started verdict and carries the failure
                 * bits; the started verdict waits like everything else. */
                if ((i & 7) == 7 && !space_check(&gmu_space, "while the GMU was deciding")) {
                        break;                  /* a bus error: no patience brings the transactions back */
                }
                waiting = rd(gmu.base + GMU_CM3_FW_INIT_RESULT);
                if (waiting != GMU_BOOTED_BABEFACE && (waiting & GMU_FAILED)) {
                        printf("adreno: %u: the GMU's firmware has stopped answering: fw_init_result %#x\n", step, waiting);
                        break;
                }
        }
        hfi_receive(2, ~0u, &waiting);
        if (!answered || verdict != 0) {
                printf("adreno: %u: the GMU %s (%#x) to %s; fw_init_result %#x, gmu2host_intr_info %#x\n", step,
                    answered ? "says no" : "does not answer", verdict, what, rd(gmu.base + GMU_CM3_FW_INIT_RESULT),
                    rd(gmu.base + GMU_GMU2HOST_INTR_INFO));
                if (hfi_receive(1, number, &verdict) && verdict == 0) {
                        /* the R15 phone did exactly this: answered the speed wanted after the asking
                         * was given up (the answer sat in the answer queue when 'slumber' went out,
                         * and the GX switch read powered at the leave-taking) */
                        printf("adreno: %u: the GMU's answer to %s arrived late: verdict 0 -- a slow answer, not a refusal\n",
                            step, what);
                        space_faulted(&gmu_space);
                        return true;
                }
                space_faulted(&gmu_space);
                if (!post_mortem_done) {
                        post_mortem_done = true;
                        gmu_post_mortem();
                }
                return false;
        }
        printf("adreno: %u: the GMU accepts %s\n", step, what);
        if (turns >= 1000) {
                printf("adreno: %u: the answer took about %u ms to come -- the patience was not wasted\n", step, turns / 10);
        }
        return space_check(&gmu_space, "as the answer came");   /* the moment a fault appears, not the step's end:
                                                                 * the R15 fault's time was never in the log; a
                                                                 * survivable one is named, cleared, forgiven */
}

/*
 * A vote for a level of a supply: the level, its place in RPMh's list for the rail, and the place
 * of the level the memories' rail must have with it (vendor's setup_volt_dependency_tbl).
 */
static bool
rail_vote(uint32_t level, const uint16_t *rail, unsigned rail_levels, const uint16_t *mx, unsigned mx_levels, uint32_t *vote)
{
        unsigned at, with = 0;

        for (at = 0; at < rail_levels && rail[at] < level; at++) {
        }
        if (at == rail_levels || mx_levels == 0) {
                return false;
        }
        while (with + 1 < mx_levels && mx[with] < rail[at]) {
                with++;
        }
        *vote = (uint32_t)rail[at] << 16 | with << 8 | at;
        return true;
}

/* dt: kgsl_3d0_reg_memory: the 3D core's registers; a6xx.xml counts in words */
static struct block gpu = { "gpu", 0x02c00000, 0x40000 };      /* dt sm8150: gpu@2c00000 ("kgsl_3d0_reg_memory") */
#define GPU_WORD(n)             (4u * (n))
#define RBBM_INT_0_STATUS       GPU_WORD(0x0201)
#define RBBM_STATUS             GPU_WORD(0x0210)
#define RBBM_CLOCK_CNTL         GPU_WORD(0x00ae)
#define CP_RB_BASE              GPU_WORD(0x0800)
#define GMU_CORE_REQUEST        (1u << 30)      /* of host2gmu_intr_set: keep the 3D core up for us; bit 31: no more */
#define GMU_CORE_GRANTED        (1u << 31)      /* of gmu2host_intr_info */

static uint32_t core_khz;               /* the 3D core's speed, as asked of the GMU */

/*
 * What the vendor's driver sends at this point (a6xx_hfi_start; Linux the same with fewer
 * levels): the table of speeds of the 3D core with the supply level each needs -- the phone's
 * three lowest (dt sm8150 gpu_opp_table: 345, 427, 585 MHz at RPMH levels 64, 80, 128) -- and of the
 * GMU itself; the table of bus bandwidth votes, of which Linux gets by with the one for "off"
 * (a6xx_hfi.c: adreno_7c3_build_bw_table); "start"; and the speed wanted now. Then the request
 * to keep the 3D core powered while we use it (a6xx_gmu_oob_set), and its registers can be read.
 */
static bool
step_core_power(void)
{
        static const struct {
                uint32_t khz, level;
        } speeds[] = { { 0, 0 }, { 345000, 64 }, { 427000, 80 }, { 585000, 128 } };   /* dt sm8150 gpu_opp_table: 345/427/585 MHz at SVS/SVS_L1/NOM */
        static struct hfi_perf_table perf;
        static struct hfi_bw_table bw;
        uint32_t core_start[2] = { 0, 0 }, start[1] = { 0 }, vote[4] = { 0, 1, 3, HFI_NO_INDEX }, granted = 0, speed = 0;
        uint16_t gfx[16], cx[16], mx[16];
        unsigned gfx_levels, cx_levels, mx_levels;

        if (!cx_ready() || !gmu_talks) {
                return false;
        }
        if (!rpmh_resource("gfx.lvl", gfx, &gfx_levels) || !rpmh_resource("cx.lvl", cx, &cx_levels) ||
            !rpmh_resource("mx.lvl", mx, &mx_levels)) {
                printf("adreno: %u: RPMh's list lacks one of the rails\n", step);
                return false;
        }
        perf.gpu_levels = sizeof(speeds) / sizeof(speeds[0]);
        for (unsigned i = 0; i < perf.gpu_levels; i++) {
                perf.gpu[i].khz = speeds[i].khz;
                perf.gpu[i].acd = 0xffffffff;           /* no adaptive clocking */
                if (!rail_vote(speeds[i].level, gfx, gfx_levels, mx, mx_levels, &perf.gpu[i].vote)) {
                        printf("adreno: %u: RPMh has no level %u for the 3D core's rail\n", step, speeds[i].level);
                        return false;
                }
        }
        perf.gmu_levels = 2;
        perf.gmu[1].khz = 200000;
        if (!rail_vote(0, cx, cx_levels, mx, mx_levels, &perf.gmu[0].vote) ||
            !rail_vote(48, cx, cx_levels, mx, mx_levels, &perf.gmu[1].vote)) {  /* "minimum SVS", the vendor's choice */
                return false;
        }
        printf("adreno: %u: votes: 3D core %#x %#x %#x %#x, GMU %#x %#x\n", step, perf.gpu[0].vote, perf.gpu[1].vote,
            perf.gpu[2].vote, perf.gpu[3].vote, perf.gmu[0].vote, perf.gmu[1].vote);

        bw.levels = 1;
        bw.ddr_commands = 3;
        bw.ddr_wait = 7;
        bw.ddr_address[0] = rpmh_resource("SH0", NULL, NULL);
        bw.ddr_address[1] = rpmh_resource("MC0", NULL, NULL);
        bw.ddr_address[2] = rpmh_resource("ACV", NULL, NULL);
        bw.ddr_data[0][0] = bw.ddr_data[0][1] = bw.ddr_data[0][2] = 0x40000000;
        bw.cnoc_commands = 1;
        bw.cnoc_wait = 1;
        bw.cnoc_address[0] = rpmh_resource("CN0", NULL, NULL);
        bw.cnoc_data[0][0] = 0x40000000;
        bw.cnoc_data[1][0] = 0x60000001;
        printf("adreno: %u: RPMh's bus resources: SH0 %#x, MC0 %#x, ACV %#x, CN0 %#x (Linux has 0x50004, 0x50000, 0x50088, "
            "0x5006c)\n", step, bw.ddr_address[0], bw.ddr_address[1], bw.ddr_address[2], bw.cnoc_address[0]);
        if (bw.ddr_address[0] == 0 || bw.ddr_address[1] == 0 || bw.ddr_address[2] == 0 || bw.cnoc_address[0] == 0) {
                return false;
        }

        if (PE_parse_boot_argn("adreno_speed", &speed, sizeof(speed)) && speed >= 1 && speed < perf.gpu_levels) {
                vote[2] = speed;
        }
        core_khz = speeds[vote[2]].khz;
        printf("adreno: %u: the speed wanted: %u MHz\n", step, core_khz / 1000);
        announce("send the GMU its tables (speeds and supply levels, bus votes), \"start\", and the speed wanted; "
            "from here on it casts votes in RPMh by itself");
        if (!hfi_send("the table of speeds", HFI_PERF_TABLE, &perf.header, sizeof(perf) / 4) ||
            !hfi_send("the table of bus votes", HFI_BW_TABLE, &bw.header, sizeof(bw) / 4) ||
            !hfi_send("\"start the core firmware\"", HFI_CORE_FW_START, core_start, 2)) {
                return false;
        }
        /*
         * The R15 fault came exactly here: the firmware touching 0x60000000 as it ramped the rail,
         * and the SMMU refusing it with an access-flag fault at an address this driver maps with
         * the flag set -- an answer out of a translation nobody living made.  Everything the SMMU
         * can be asked to forget, it is asked now, the bank's own entries and the whole non-secure
         * side's both, ours included, so the ramp begins from our tables alone.
         */
        announce("make the SMMU forget every translation again, ours included, before the GMU ramps the rail");
        put(&smmu, "cb tlbiall", smmu_context(&gmu_space) + SMMU_CB_TLBIALL, 0);
        put(&smmu, "cb tlbiasid", smmu_context(&gmu_space) + SMMU_CB_TLBIASID, gmu_space.context);
        put(&smmu, "cb tlbsync", smmu_context(&gmu_space) + SMMU_CB_TLBSYNC, 1);
        (void)wait_for(&smmu, smmu_context(&gmu_space) + SMMU_CB_TLBSTATUS, 1, 0, 100);
        put(&smmu, "tlbiallnsnh", SMMU_TLBIALLNSNH, 0);
        put(&smmu, "stlbgsync", SMMU_STLBGSYNC, 1);
        (void)wait_for(&smmu, SMMU_STLBGSTATUS, 1, 0, 100);
        if (!hfi_send("the speed wanted", HFI_GX_BW_PERF_VOTE, vote, 4)) {
                return false;
        }

        announce("ask the GMU to power the 3D core and keep it powered");
        get(&gmu, "sptprac_pwr_clk_status", GMU_SPTPRAC_PWR_CLK_STATUS);
        get(&gpucc, "gx_gdscr", GPUCC_GX_GDSCR);
        put(&gmu, "host2gmu_intr_set", GMU_HOST2GMU_INTR_SET, GMU_CORE_REQUEST);
        /* the same patience as the vote: the ramp the GMU runs for this can be a slow one */
        for (int i = 0; i < 100000 && !(granted & GMU_CORE_GRANTED); i++) {
                sleep_us(100);
                granted = rd(gmu.base + GMU_GMU2HOST_INTR_INFO);
                if ((i & 63) == 63 && !space_check(&gmu_space, "while the 3D core was powered")) {
                        break;                  /* a bus error under the ramp: nothing to wait for */
                }
        }
        core_is_on = true;                      /* asked for, at least: to be given back */
        put(&gmu, "gmu2host_intr_clr", GMU_GMU2HOST_INTR_CLR, GMU_CORE_GRANTED);
        /* the rail can come up as slowly as the R15 answer came: give the witness time before asking it */
        for (int i = 0; i < 100000 && (rd(gmu.base + GMU_SPTPRAC_PWR_CLK_STATUS) & 0xc0) != 0; i++) {
                sleep_us(100);
        }
        get(&gmu, "sptprac_pwr_clk_status", GMU_SPTPRAC_PWR_CLK_STATUS);
        get(&gpucc, "gx_gdscr", GPUCC_GX_GDSCR);
        get(&gmu, "rpmh_power_state", GMU_RPMH_POWER_STATE);
        if (!(granted & GMU_CORE_GRANTED) || (rd(gmu.base + GMU_SPTPRAC_PWR_CLK_STATUS) & 0xc0) != 0) {
                printf("adreno: %u: the GMU does not power the 3D core (gmu2host_intr_info %#x, fw_init_result %#x)\n", step,
                    granted, rd(gmu.base + GMU_CM3_FW_INIT_RESULT));
                space_faulted(&gmu_space);
                return false;
        }
        if (!hfi_send("\"start\"", HFI_START, start, 1)) {
                return false;
        }
        /* the counter, where it works, is quoted; it is not waited on anywhere (step 5 says why) */
        printf("adreno: %u: the 3D core's clock measured %u Hz (%u MHz was asked for), the GMU's in it %u Hz\n", step,
            measure_quietly(GPUCC_DEBUG_GX_GFX3D), core_khz / 1000, measure_quietly(GPUCC_DEBUG_GX_GMU));

        announce("read registers of the 3D core itself (0x2c00000): the first time it has power");
        get(&gpu, "rbbm_status", RBBM_STATUS);
        get(&gpu, "rbbm_int_0_status", RBBM_INT_0_STATUS);
        get(&gpu, "rbbm_clock_cntl", RBBM_CLOCK_CNTL);
        get(&gpu, "cp_rb_base", CP_RB_BASE);
        printf("adreno: %u: THE 3D CORE HAS POWER AND ANSWERS\n", step);
        return space_check(&gmu_space, "at the step's end");    /* a survivable refusal no longer costs the step;
                                                                 * a bus error still does */
}

/* ---- step 18: the 3D core's memory and its microcode -------------------------------------------------------- */

/*
 * The 3D core takes its orders as words in a ring of memory, which a small processor inside it
 * ("SQE", with microcode of its own: a630_sqe.fw) reads up to where a register says we have
 * written. Like the GMU it reaches memory through the SMMU: stream 0, to which this gives
 * context 0, with tables of ours that hold the ring, the microcode and later what is drawn into.
 * What is written to the core's registers before its processor is started, and in what order,
 * is Linux's a6xx_gpu.c hw_init for this GPU, less what only matters once it draws (clock gating,
 * the protection of registers against user programs' commands, counters).
 */
#define CORE_STREAM             0
#define CORE_MATCH              0
#define PTE_CORE                (PTE_GMU | PTE_UNPRIVILEGED)
#define PTE_READ_ONLY           (1ull << 7)     /* Linux gives the core its microcode so (MSM_BO_GPU_READONLY) */
#define CORE_RING_ADDRESS       0x01000000u
#define CORE_RING_SIZE          0x8000u         /* Linux's; 8192 words */
#define CORE_SQE_ADDRESS        0x01100000u
#define CORE_SCRATCH_ADDRESS    0x01200000u
#define CORE_TARGET_ADDRESS     0x10000000u
#define CORE_NOWHERE            0x7f000000u     /* never given anything */

#define GBIF_QSB_SIDE0          (4 * 0x03)      /* of the bus interface (step 6) */
#define RBBM_GBIF_CLIENT_QOS_CNTL GPU_WORD(0x0011)
#define RBBM_GBIF_HALT          GPU_WORD(0x0016)
#define RBBM_GBIF_HALT_ACK      GPU_WORD(0x0017)
#define RBBM_INTERFACE_HANG_INT_CNTL GPU_WORD(0x001f)
#define RBBM_PERFCTR_CNTL       GPU_WORD(0x0500)
#define RBBM_PERFCTR_GPU_BUSY_MASKED GPU_WORD(0x050b)
#define CP_RB_CNTL              GPU_WORD(0x0802)
#define CP_RB_RPTR              GPU_WORD(0x0806)
#define CP_RB_WPTR              GPU_WORD(0x0807)
#define CP_SQE_CNTL             GPU_WORD(0x0808)
#define CP_HW_FAULT             GPU_WORD(0x0821)
#define CP_INTERRUPT_STATUS     GPU_WORD(0x0823)
#define CP_PROTECT_STATUS       GPU_WORD(0x0824)
#define CP_SQE_INSTR_BASE       GPU_WORD(0x0830)
#define CP_CHICKEN_DBG          GPU_WORD(0x0841)
#define CP_ADDR_MODE_CNTL       GPU_WORD(0x0842)
#define CP_APRIV_CNTL           GPU_WORD(0x0844)
#define CP_ROQ_THRESHOLDS_1     GPU_WORD(0x08c1)
#define CP_ROQ_THRESHOLDS_2     GPU_WORD(0x08c2)
#define CP_MEM_POOL_SIZE        GPU_WORD(0x08c3)
#define CP_AHB_CNTL             GPU_WORD(0x098d)
#define CP_LPAC_PROG_FIFO_SIZE  GPU_WORD(0x0b34)
#define UCHE_WRITE_RANGE_MAX    GPU_WORD(0x0e05)
#define UCHE_WRITE_THRU_BASE    GPU_WORD(0x0e07)
#define UCHE_TRAP_BASE          GPU_WORD(0x0e09)
#define UCHE_CACHE_WAYS         GPU_WORD(0x0e17)
#define UCHE_FILTER_CNTL        GPU_WORD(0x0e18)
#define UCHE_CLIENT_PF          GPU_WORD(0x0e19)
#define RB_DBG_ECO_CNTL         GPU_WORD(0x8e04)
#define PC_DBG_ECO_CNTL         GPU_WORD(0x9e00)
#define RBBM_SECVID_TSB_TRUSTED_BASE GPU_WORD(0xf800)
#define RBBM_SECVID_TSB_TRUSTED_SIZE GPU_WORD(0xf802)
#define RBBM_SECVID_TSB_CNTL    GPU_WORD(0xf803)
#define RBBM_SECVID_TSB_ADDR_MODE_CNTL GPU_WORD(0xf810)
#define CP_RB_CNTL_VALUE        (12u | 2u << 8 | 1u << 27)      /* 2^12 pairs of words; do not copy the read position to memory */

/* commands (Mesa's adreno_pm4.xml); a packet is a header word and so many more */
#define CP_NOP                  0x10
#define CP_WAIT_FOR_IDLE        0x26
#define CP_BLIT                 0x2c
#define CP_MEM_WRITE            0x3d
#define CP_EVENT_WRITE          0x46
#define CP_ME_INIT              0x48
#define CP_SET_MARKER           0x65
#define CP_SET_SECURE_MODE      0x66

/*
 * Linux (qcom_adreno_smmu_write_sctlr): after a fault the core's other requests are served, or it
 * hangs. And the core has a second stream, 1 (dt: gfx3d_lpac; Linux's sc7280.dtsi gives the GPU
 * both): once started, the microcode starts a second command processor ("LPAC"), which fetches
 * its own part of the microcode as that stream at once -- seen on the phone as a request for
 * 0x7e60 into the microcode, refused by an empty context given to stream 1 (adreno_stream1=2).
 * R20: stream 1 stays under entry 0's own wing by default -- raphael's dt has no separate
 * entry for it, the mask 0x401 covering streams 0 and 1 through context 0 -- and
 * adreno_stream1=1/2 is demoted to an experiment that forces sc7280's form (the pre-R20
 * port's) for comparison. The old observations (the refusal above; the reset with nothing
 * at all set up) were made before R20, when what was written for the entry was never this
 * phone's dt form in the first place.
 */
#define SMMU_SCTLR_SERVE_AFTER_FAULT (1u << 8)
static struct gpu_space core_space = { .name = "the 3D core", .context = 0, .control = SMMU_SCTLR_SERVE_AFTER_FAULT };
/* adreno_stream1=2: the second stream gets a context of its own with nothing in it, to see what it asks for */
static struct gpu_space second_space = {
        .name = "the 3D core's second stream", .context = 2, .control = SMMU_SCTLR_SERVE_AFTER_FAULT,
};
#define SECOND_MATCH            2
/* R20: the form written is the phone's own at last. R17-R19 wrote sc7280's mask (0x400) for
 * the gfx streams -- the port carried it over -- and the SMMU took none of it; the gmu's
 * landed every time because raphael's gmu form is, by coincidence, sc7280's. The bank-first
 * order R19 restored stays; what changes is the value: raphael's dt gives the gfx streams
 * 0x401 (one entry for streams 0 and 1) and the gmu streams 0x400. */
static uint32_t core_match = CORE_MATCH, second_match = SECOND_MATCH;
static bool second_space_is_on, second_stream_is_on;
static struct gpu_block core_ring, core_sqe, core_scratch, core_target;
static bool core_space_is_on, core_runs, zap_is_loaded;
#define RING_WORDS              (CORE_RING_SIZE / 4)
static uint32_t ring_at;                /* where the next word goes: the ring goes round */
static uint32_t ring_sent;              /* up to where the core has been told to read */

static uint32_t
odd_parity(uint32_t v)
{
        return 0x9669 >> (0xf & (v ^ v >> 4 ^ v >> 8 ^ v >> 12 ^ v >> 16 ^ v >> 20 ^ v >> 24 ^ v >> 28)) & 1;
}

static void
ring(uint32_t word)
{
        ((uint32_t *)core_ring.memory)[ring_at] = word;
        ring_at = (ring_at + 1) % RING_WORDS;
}

/* The header of a command with so many words after it. */
static void
ring_command(uint32_t command, uint32_t words)
{
        ring(7u << 28 | words | odd_parity(words) << 15 | command << 16 | odd_parity(command) << 23);
}

/* A register of the 3D core (by its word number) written by command, in the order of the other commands. */
static void
ring_register(uint32_t number, uint32_t value)
{
        ring(4u << 28 | 1 | odd_parity(1) << 7 | number << 8 | odd_parity(number) << 27);
        ring(value);
}

/* What has been written to the ring since the last time: out to memory, and the core told how far to read. */
static void
ring_send(void)
{
        if (ring_at >= ring_sent) {
                to_memory(core_ring.memory + 4 * ring_sent, 4 * (ring_at - ring_sent));
        } else {
                to_memory(core_ring.memory + 4 * ring_sent, 4 * (RING_WORDS - ring_sent));
                to_memory(core_ring.memory, 4 * ring_at);
        }
        ring_sent = ring_at;
        wr(gpu.base + CP_RB_WPTR, ring_at);
}

/* Let the 3D core have what is in the ring, and wait until it has taken all of it and rests. */
static bool
ring_run(const char *what)
{
        uint32_t taken = ~0u, status = ~0u;

        printf("adreno: %u: about to have the 3D core %s: the ring up to word %u\n", step, what, ring_at);
        snprintf(last_write, sizeof(last_write), "step %u had the 3D core %s", step, what);
        hold(true);
        ring_send();
        for (int i = 0; i < 20000 && (taken != ring_at || (status & ~1u)); i++) {
                sleep_us(100);
                taken = rd(gpu.base + CP_RB_RPTR);
                status = rd(gpu.base + RBBM_STATUS);
        }
        if (taken == ring_at && !(status & ~1u) && rd(gpu.base + CP_HW_FAULT) == 0 && !space_faulted(&core_space)) {
                printf("adreno: %u: the 3D core has done it (ring read up to %u, status %#x)\n", step, taken, status);
                return true;
        }
        printf("adreno: %u: the 3D core does not get through: ring read up to %u of %u, status %#x, interrupts %#x, "
            "command processor: fault %#x, interrupts %#x, protection %#x\n", step, taken, ring_at, status,
            rd(gpu.base + RBBM_INT_0_STATUS), rd(gpu.base + CP_HW_FAULT), rd(gpu.base + CP_INTERRUPT_STATUS),
            rd(gpu.base + CP_PROTECT_STATUS));
        space_faulted(&core_space);
        return false;
}

static void
put64(struct block *b, const char *name, uint32_t offset, uint64_t value)
{
        put(b, name, offset, (uint32_t)value);
        put(b, name, offset + 4, (uint32_t)(value >> 32));
}

static bool
step_core_space(void)
{
        const uint8_t *microcode;
        uint64_t size = 0;
        uint32_t stream1 = 0;    /* R20: 0 = entry 0's own wing covers stream 1 (the phone's dt form); 1/2 = the sc7280-form experiment */

        if (!cx_ready() || !core_is_on) {
                return false;
        }
        PE_parse_boot_argn("adreno_stream1", &stream1, sizeof(stream1));
        announce("make the 3D core's memory: its ring, its microcode (fw/a630_sqe.fw), a page to try writing to");
        microcode = image_file("fw", "a630_sqe.fw", &size);
        if (microcode == NULL || size < 8 || size > 0x40000 ||
            !block_new(&core_space, &core_ring, CORE_RING_ADDRESS, CORE_RING_SIZE, PTE_CORE) ||
            !block_new(&core_space, &core_sqe, CORE_SQE_ADDRESS, (uint32_t)size - 4, PTE_CORE | PTE_READ_ONLY) ||
            !block_new(&core_space, &core_scratch, CORE_SCRATCH_ADDRESS, 4096, PTE_CORE)) {
                return false;
        }
        memcpy((void *)core_sqe.memory, microcode + 4, size - 4);       /* the file's first word is not microcode */
        to_memory(core_sqe.memory, core_sqe.size);

        /* the reference's order (the phone the driver comes from, and the GMU's own step 13 here):
         * the bank is programmed COMPLETELY and switched ON first, then the stream table is told
         * where the stream goes. R20: what the stream table is TOLD is the phone's own form at
         * last -- the mask is raphael's dt 0x401 for the gfx streams, not sc7280's 0x400 the port
         * carried over; the gmu's 0x400 landed every time because raphael's gmu form is, by
         * coincidence, sc7280's. The bank-first order R19 restored stays as it was. */
        announce("give the SMMU's context 0 these tables and send the 3D core's requests (stream 0) through it: "
            "the bank is programmed and ON before the stream table is touched, and what the entry takes is "
            "the phone's own form -- raphael's dt gives the gfx streams the mask 0x401 (one entry for streams "
            "0 and 1); sc7280's 0x400, which the port carried over, is what R17, R18 and R19 kept writing, "
            "and the SMMU kept none of");
        core_match = CORE_MATCH;
        core_space_is_on = true;
        if (!space_on(&core_space, CORE_MATCH, CORE_STREAM, SMMU_MASK_GFX)) {
                return false;
        }
        if (stream1 == 2) {
                /* the experiment: an empty context of the stream's own, to see what it asks for */
                announce("the core's second stream (1) given a context of its own with nothing in it "
                    "(sc7280's form, entry 2) -- the experiment adreno_stream1=2 asks for");
                if ((second_space.root = space_new_table(&second_space)) == NULL) {
                        return false;
                }
                second_space_is_on = true;
                if (!space_on(&second_space, SECOND_MATCH, 1, SMMU_MASK_GMU)) {
                        return false;
                }
        } else if (stream1 == 1) {
                /* sc7280's form for comparison: entry 2, through the same context */
                announce("the core's second stream (1) through the same context, at the stream table's "
                    "entry 2 -- sc7280's form, the experiment adreno_stream1=1 asks for");
                second_stream_is_on = true;
                if (!space_stream(&core_space, SECOND_MATCH, 1, SMMU_MASK_GMU)) {
                        return false;
                }
        } else {
                /* the default: no entry 2 -- raphael's dt has no separate entry for the second
                 * stream; the mask 0x401 on entry 0 covers streams 0 and 1, both through context 0 */
                announce("the second stream (1) under entry 0's own wing: raphael's dt has no separate "
                    "entry for it, and the mask 0x401 covers streams 0 and 1 through context 0");
        }
        get(&gpu, "rbbm_status", RBBM_STATUS);
        return !space_faulted(&core_space);
}

/* ---- step 19: the zap shader, loaded -------------------------------------------------------------------- */

/*
 * The rest of what Linux does with the zap shader (mdt_loader.c: qcom_mdt_pas_init,
 * qcom_mdt_load; adreno_gpu.c: zap_shader_load_mdt): TrustZone is given the image's header,
 * told where the image will be, the image's one segment (the .mbn's single PT_LOAD,
 * relocatable: at the start of that memory) is put there, and TrustZone is asked to
 * authenticate it and run it. The vendor's driver does this before it starts the 3D core's
 * command processor (a6xx_rb_start: a6xx_microcode_load), Linux after; this phone's own
 * order is kept. Once that is done the memory is TrustZone's, and nothing here can see
 * whether it is so; to write there again would be the end of the run.
 *
 * R21, three things of redwood's that this phone does not have, all found in the round's
 * work:
 *   - The give-up call first (service 2 command 6). Redwood's TrustZone answers it with
 *     success whether it had a shader or not (on redwood kernels come and go without the
 *     phone restarting, so a kernel leaves one behind, and the next one has it taken away
 *     again); raphael's took the processor with it (R20: the log stops at "about to ask
 *     TrustZone to give up the zap shader" and the wrapper's answer line never comes) and
 *     never gave it back. This phone's boots go through XBL every time -- TrustZone is new
 *     every boot, no kernel before this one can have left a shader -- and this phone's own
 *     driver (dt: qcom,kgsl-hyp, compatible "qcom,pil-tz-generic", firmware a640_zap,
 *     pas-id 13) sends no give-up when it starts the GPU. So it is not sent by default any
 *     more; adreno_tzunload=1 asks for it, at the very end of the step, with the channel
 *     proven, where a wedge costs the least. What is asked first instead is what this
 *     phone's own Linux asks TrustZone before anything else: whether the calls are
 *     available (service 6 command 1), for the signature, for the place, for the
 *     authentication.
 *   - The place, 0x8b71a000. Redwood's dt reserves gpu_micro_code@8b71a000 for the zap
 *     shader; this phone's dt reserves pil_gpu_region@98f15000, 8 KB (between the IPA's
 *     and the spss's regions, the same two pages redwood's had). A refusal of the
 *     signature or of the place is a failure now -- nothing can have been left from before
 *     to keep -- and the step's diagnostics then ask which images TrustZone "supports"
 *     (the GPU's, and the audio's for comparison), and, last, the give-up call itself.
 *   - The hashes of the .mbn: phdr[1] with the type mark at 24 of p_flags, not in the low
 *     bits (zap_header()).
 *
 * R23, what the refusal is: the image's.  The R22 matrix closed the call's variables -- the
 * same -3145797 under every layout, every form, every page -- and the wild logs of this
 * platform's own Linux now show what that code means: on an sm8150-class phone, spss's
 * PAS_INIT_IMAGE dies with EXACTLY this code (func id 0x42000201, ret -3145797, then
 * "Initializing image failed(rc:-22)") while cdsp, adsp, slpi, ipa_fws, venus and modem all
 * pass the very same boot, and a640_zap itself comes up ("a640_zap: Brought out of reset")
 * through the same three calls, its place a dynamically allocated page.  The code is what
 * TrustZone answers when THE IMAGE cannot be taken -- and the file this tree has shipped since
 * R13 answers that description without any help: its hashes carry Qualcomm's SecTools TEST
 * certificates ("Generated Test Attestation CA", "General Use Test Key (for testing only)"), a
 * chain a fused phone's TrustZone refuses from anyone.  So the step now: the file's own chain
 * is read out first (zap_header()), one canonical attempt is sent -- the whole .mbn over a
 * preemptible call, both working callers' form -- and on a refusal the controls are asked: the
 * same call over a page of zeroes, for the GPU's id and the audio's.  A different code there
 * means TrustZone READ our page and refused what it parsed (the image's side, its signature
 * for one); the same code means the refusal comes before any image is read (the memory or the
 * caller -- then the file question still stands, but the environment moved up the list).
 * The place: the dt's pil_gpu_region@98f15000 is referenced by NOTHING (the qcom,kgsl-hyp node
 * carries no memory-region at all, and nothing else points at it), and the working phone's own
 * log loads its zap from a page the pil framework allocates ("loading from 0xfe500000 to
 * 0xfe501000" -- one page, dynamically placed).  So the vendor-faithful place is a fresh page
 * of ours; adreno_zapmem=<phys> forces the dt's region back, for an A/B.
 * R24: the file IS this phone's own now (the /vendor file; the same 14256 B, the same code
 * bytes, only the certificate area differs -- the swap is done inside the md1 volume, byte
 * for byte, same length, one occurrence).  What the refusal was, is what the file was: the
 * zero-page control already proved TrustZone reads our buffers (-3145738, a different code)
 * and refuses THE IMAGE (-3145797, the test chain).  With the production chain the attempt
 * is expected to be taken, and the step walks on into MEM_SETUP, the write and the
 * authentication.
 * R22, the matrix that closed the call's variables (its verdict is what stands):
 *   - The form of the call. R21 sent the PAS calls "at once" (SCM_FAST, as the probes are
 *     sent); both working forms send them as calls that MAY BE PREEMPTED -- redwood's step 12
 *     and Linux's qcom_scm.c alike (a signature is checked for longer than an "at once" call
 *     promises; TrustZone may answer such a call with SCM_INTERRUPTED and take it up where it
 *     left it, which the wrapper here handles, session and all). The probes keep their form:
 *     they are "at once" on redwood too, and R21 proved them answered here.
 *   - The layout of the metadata (zap_header()): R21 packed the hashes right after the header
 *     pair, at 0x94 -- the .mdt's shape, where that IS the hash segment's p_offset. This
 *     phone's .mbn declares its hashes at p_offset 0x1000; TrustZone read the header in the
 *     buffer, looked at 0x1000 and found the middle of the packed block instead.
 * The step now tries, in order: the whole .mbn as the metadata over a preemptible call; R21's
 * packed metadata over that call; the whole .mbn over an "at once" call. Between the attempts
 * the give-up call -- the one R21 proved answered, returns 0 -- is asked once, to take the
 * refusal back. Whichever attempt wins, the step goes on with its form for the place and the
 * authentication. Each attempt prints its own refusal code: the same code under every layout
 * and form is the code of something else (the memory, or the state), and the tree moves on.
 */
#define ZAP_MEMORY_SIZE         0x1000u         /* the grant: the image's one page -- the working
                                                 * phone's own size, "to 0xfe501000" from 0xfe500000 */
#define SCM_PAS_MEM_SETUP       SCM_CALL(2, 2)
#define SCM_THREE_VALUES        3

/* TrustZone is to give the zap shader up, and its memory with it. */
static bool
zap_unload(void)
{
        uint64_t answer = ~0ull;

        return scm("to give up the zap shader", scm_how | SCM_SIP | SCM_PAS_SHUTDOWN, SCM_ONE_VALUE, PAS_GPU, 0, 0, &answer) == 0 &&
               answer == 0;
}

static bool
step_zap_load(void)
{
        const uint8_t *segment;
        volatile uint8_t *zap_memory = NULL;
        vm_offset_t zap_page = 0;
        uint64_t zap_place = 0, size = 0, header = 0, whole = 0, answer = ~0ull;
        int32_t got;
        uint32_t tzunload = 0;
        uint32_t qfprom_on = 0;         /* R28: the fuse sweep is RETIRED from the default
                                         * run (R25b/R27b proved it the t=4s killer); it rides
                                         * only behind adreno_qfprom=1 */
        uint32_t e1_mode = 2;           /* R28: 2 = R21-R24b's shape, the size stays home -- the
                                         * DEFAULT (R27b closed the shape charge: with the size
                                         * the call dies at the SMC itself, -5, it never reaches
                                         * the verdict); 3 = Linux's shape, kept for the record,
                                         * asked for with adreno_e1_mode=3 */
        uint32_t shape_sweep = 0;       /* R28: the shape sweeps (E2..E5) are RETIRED from the
                                         * default run (R27b: all three-word forms die at the SMC
                                         * with -5, the one survivor is E1 two-param's own code --
                                         * zero information, real reset risk); they ride only
                                         * behind adreno_shape_sweep=1 */
        bool zap_ours = false;

        if (!cx_ready() || !core_is_on) {
                return false;
        }
        /*
         * R26: the calls of this step can reset the phone (R25b died in the window after
         * step 18 done, its step 19 prints only in the RAM the reset ate).  From here the
         * storage holds what was printed before each of them: this line is the first one
         * after "step 18: done" the storage can hold, so a record that ends before it
         * says the death came before any call of this step was issued.
         */
        printf("adreno: %u: step 19 entered (R26: before each call that can reset the phone, the whole log goes to the storage first)\n",
            step);
        put_the_log_away("the step's first line");
        announce("establish the channel first -- the questions this phone's own Linux asks TrustZone before anything else "
            "(R21: the give-up call is not sent by default; raphael's TrustZone took the processor with it and never gave it back)");
        if (scm("whether it can be asked", scm_how | SCM_IS_CALL_AVAILABLE, SCM_ONE_VALUE, SCM_SIP | SCM_IS_CALL_AVAILABLE, 0,
            0, &answer) != 0 || answer != 1) {
                scm_how = SCM_FAST | SCM_SIP;   /* the same with 32-bit arguments, Linux's second try */
                if (scm("the same, the 32-bit way", scm_how | SCM_IS_CALL_AVAILABLE, SCM_ONE_VALUE, SCM_SIP | SCM_IS_CALL_AVAILABLE,
                    0, 0, &answer) != 0 || answer != 1) {
                        printf("adreno: %u: TRUSTZONE DOES NOT ANSWER THE WAY IT ANSWERS LINUX: no zap shader can be asked "
                            "for (not the give-up call alone: the whole door is dark)\n", step);
                        return false;
                }
        }
        answer = ~0ull;
        scm("for the call that takes an image's signature", scm_how | SCM_IS_CALL_AVAILABLE, SCM_ONE_VALUE,
            SCM_SIP | SCM_PAS_INIT_IMAGE, 0, 0, &answer);
        answer = ~0ull;
        scm("for the call that tells where an image goes", scm_how | SCM_IS_CALL_AVAILABLE, SCM_ONE_VALUE,
            SCM_SIP | SCM_PAS_MEM_SETUP, 0, 0, &answer);
        answer = ~0ull;
        scm("for the call that authenticates and starts an image", scm_how | SCM_IS_CALL_AVAILABLE, SCM_ONE_VALUE,
            SCM_SIP | SCM_PAS_AUTH_AND_RESET, 0, 0, &answer);
        PE_parse_boot_argn("adreno_tzunload", &tzunload, sizeof(tzunload));
        PE_parse_boot_argn("adreno_qfprom", &qfprom_on, sizeof(qfprom_on));
        PE_parse_boot_argn("adreno_e1_mode", &e1_mode, sizeof(e1_mode));
        PE_parse_boot_argn("adreno_shape_sweep", &shape_sweep, sizeof(shape_sweep));
        if (e1_mode != 2 && e1_mode != 3) {
                e1_mode = 2;    /* R28: the fallback is the two-parameter control, not the three */
        }
        printf("adreno: %u: R28: the two-parameter baseline -- adreno_e1_mode=%u (%s), the shape sweeps behind "
            "adreno_shape_sweep=%u, the fuse sweep behind adreno_qfprom=%u\n", step, e1_mode, e1_mode == 3 ?
            "Linux's shape: a number, the buffer, a number -- the size goes with it (asked for by name; with the size "
            "the call died at the SMC itself, R27b's verdict)" :
            "R21-R24b's shape: a number, the buffer -- the size stays home (the default, the shape that answers)",
            shape_sweep, qfprom_on);
        if (tzunload) {
                announce("ask TrustZone to give up a zap shader that a kernel before this one may have left (adreno_tzunload: "
                    "the call that wedged R20, asked now with the channel proven)");
                printf("adreno: %u: %s\n", step, zap_unload() ? "TrustZone has given up what it had (it says so with none, too)" :
                    "TRUSTZONE DOES NOT GIVE IT UP: the next answers tell whether it keeps one");
        }
        {
                /* the one PT_LOAD segment, inside the same .mbn as the header (p_offset/p_filesz) */
                const uint8_t *file;      /* whole lives in the step's own frame now (R25): the
                                         * canonical attempt sends it as the third word */
                uint32_t segments, phoff, i;

                segment = NULL;
                size = 0;
                file = image_file("fw", "a640_zap.mbn", &whole);
                if (file != NULL && whole >= 52 + 2 * 32) {
                        memcpy(&segments, file + 44, 2);
                        memcpy(&phoff, file + 28, 4);
                        for (i = 0; i < segments && phoff + 32ull * (i + 1) <= whole; i++) {
                                uint32_t at = phoff + 32 * i, type, off, len;

                                memcpy(&type, file + at, sizeof(type));
                                memcpy(&off, file + at + 4, sizeof(off));
                                memcpy(&len, file + at + 16, sizeof(len));
                                if (type == 1 && len > 0 && len <= 0x1000 && (uint64_t)off + len <= whole) {      /* PT_LOAD */
                                        segment = file + off;
                                        size = len;
                                        break;
                                }
                        }
                }
        }
        if (segment == NULL || size == 0 || size > 0x1000) {
                return false;
        }
        announce("hand the zap shader's metadata to TrustZone, tell it where the image goes, put the image there, and ask "
            "TrustZone to authenticate and run it (R23: the file's own signature chain is read out first, then one canonical "
            "attempt -- the whole .mbn over a call that may be preempted, both working callers' form -- and on a refusal the "
            "controls: zeroes as the metadata, for the GPU's id and the audio's)");
        printf("adreno: %u: one attempt, the form both working callers use (the R22 matrix proved the code does not move "
            "with the layout, the form or the page)\n", step);
        if (!zap_header(&header, true)) {
                return false;
        }
        /*
         * R26, the empty probe: a question that hands TrustZone no memory (it asks whether
         * a call exists), sent before E1.  On the storage it is the anchor between "step 19
         * was entered" and "E1 was armed": a record that ends before it says the death came
         * before any call of this step was issued; one that holds it and then ends at E1's
         * own parameters says the death is E1's.
         */
        answer = ~0ull;
        scm("the empty probe (R26: a question that hands no memory -- what it proves is that this point was reached alive)",
            scm_how | SCM_IS_CALL_AVAILABLE, SCM_ONE_VALUE, SCM_SIP | SCM_PAS_INIT_IMAGE, 0, 0, &answer);
        printf("adreno: %u: the empty probe answered; what follows it was reached alive\n", step);
        printf("adreno: %u: E1 armed: smc %#llx, arguments word %#llx, x2 %u, x3 %#llx (the metadata page), "
            "x4 %#llx (%s)\n", step, (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_INIT_IMAGE,
            (uint64_t)(e1_mode == 3 ? SCM_VALUE_BUFFER_VALUE : SCM_VALUE_AND_BUFFER), (unsigned)PAS_GPU, header,
            e1_mode == 3 ? whole : 0,
            e1_mode == 3 ? "the whole file, the size goes with it" : "the size stays home");
        put_the_log_away("E1's parameters above -- the call that follows them may reset the phone");
        if (e1_mode == 3) {
                got = scm("to take the zap shader's signature (R26 E1, adreno_e1_mode=3: Linux's shape -- the size "
                    "goes with it: a number, the buffer, a number)", (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_INIT_IMAGE,
                    SCM_VALUE_BUFFER_VALUE, PAS_GPU, header, whole, &answer);
        } else {
                got = scm("to take the zap shader's signature (R26 E1, adreno_e1_mode=2: R21-R24b's shape -- the size "
                    "stays home: a number, the buffer)", (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_INIT_IMAGE,
                    SCM_VALUE_AND_BUFFER, PAS_GPU, header, 0, &answer);
        }
        if (got != 0 || answer != 0) {
                printf("adreno: %u: refused: the call returns %d, its answer %#llx\n", step, got, answer);
                goto refused;
        }
        {
                /* the place.  The vendor-faithful shape: a page of ours, like the working phone's "loading from
                 * 0xfe500000 to 0xfe501000" -- the dt's pil_gpu_region@98f15000 is referenced by nothing (the
                 * qcom,kgsl-hyp node has no memory-region at all), so nothing ever used it for this. */
                uint32_t forced = 0;

                PE_parse_boot_argn("adreno_zapmem", &forced, sizeof(forced));
                if (forced) {
                        zap_place = forced;
                        zap_memory = (volatile uint8_t *)ml_io_map_wcomb(forced, ZAP_MEMORY_SIZE);
                        printf("adreno: %u: the place forced by the boot arg: %#x\n", step, forced);
                } else {
                        if (kmem_alloc(kernel_map, &zap_page, PAGE_SIZE, KMA_KOBJECT | KMA_ZERO | KMA_DATA,
                            VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
                                printf("adreno: %u: no memory for the zap image\n", step);
                                return false;
                        }
                        zap_place = kvtophys(zap_page);
                        zap_memory = (volatile uint8_t *)zap_page;
                        zap_ours = true;
                        printf("adreno: %u: the place is a fresh page of ours, physical %#llx (the vendor's own shape: the "
                            "dt's pil_gpu_region is referenced by nothing)\n", step, zap_place);
                }
        }
        put_the_log_away("the place is handed over next (MEM_SETUP)");
        if (scm("where the image will be", (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_MEM_SETUP, SCM_THREE_VALUES, PAS_GPU,
            zap_place, ZAP_MEMORY_SIZE, &answer) != 0 || answer != 0) {
                printf("adreno: %u: TrustZone does not take the place %#llx (%#llx)\n", step, zap_place, answer);
                goto refused;
        }
        printf("adreno: %u: the place begins %02x %02x %02x %02x, the zap shader %02x %02x %02x %02x\n", step,
            zap_memory[0], zap_memory[1], zap_memory[2], zap_memory[3], segment[0], segment[1], segment[2], segment[3]);
        printf("adreno: %u: about to write %llu bytes at %#llx\n", step, size, zap_place);
        snprintf(last_write, sizeof(last_write), "step %u wrote the zap shader into memory at %#llx", step, zap_place);
        hold(true);
        for (uint64_t i = 0; i < ZAP_MEMORY_SIZE; i++) {
                zap_memory[i] = i < size ? segment[i] : 0;
        }
        if (zap_ours) {
                to_memory((vm_offset_t)zap_memory, ZAP_MEMORY_SIZE);
        }
        settle();
        put_the_log_away("the authentication and the start are next (AUTH_AND_RESET can reset the phone)");
        if (scm("to authenticate the zap shader and run it", (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_AUTH_AND_RESET,
            SCM_ONE_VALUE, PAS_GPU, 0, 0, &answer) != 0 || answer != 0) {
                printf("adreno: %u: TrustZone does not authenticate the zap shader (%#llx)\n", step, answer);
                goto refused;
        }
        printf("adreno: %u: TRUSTZONE HAS AUTHENTICATED AND STARTED THE ZAP SHADER\n", step);
        zap_is_loaded = true;
        return true;
refused:
        /*
         * R26: the refusal itself is the one line the whole experiment is for -- it goes
         * to the storage before the controls run on.
         */
        put_the_log_away("the refusal and its code are on the record above");
        /*
         * The channel is alive (the answers came back) but this image or this place is not
         * taken -- on this phone that cannot mean a shader from before (XBL made TrustZone
         * new this boot), so it is a failure. The diagnostics come now, at the end, where a
         * wedge costs the least: which images TrustZone "supports" (the GPU's, and the
         * audio's for comparison -- this phone starts its audio through TrustZone too), and
         * the give-up call itself, the one that wedged R20, asked once.
         */
        {
                /* the controls (R23): the same call over a page of zeroes.  A different code = TrustZone read our
                 * page and refused what it parsed (the image's side).  The same -3145797 = the refusal comes before
                 * any image is read (the memory or the caller).  The audio's id over the same zeroes tells image
                 * vs peripheral.  The page is not freed: after a PAS call it is TrustZone's to look at, like the
                 * metadata page above. */
                vm_offset_t zero_page = 0;

                if (kmem_alloc(kernel_map, &zero_page, PAGE_SIZE, KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) == KERN_SUCCESS) {
                        uint64_t zero_phys = kvtophys(zero_page), zero_answer = ~0ull;
                        int32_t zero_got;

                        to_memory(zero_page, PAGE_SIZE);
                        zero_got = scm("the control: a page of zeroes as the GPU's metadata",
                            (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_INIT_IMAGE, SCM_VALUE_AND_BUFFER, PAS_GPU, zero_phys,
                            0, &zero_answer);
                        printf("adreno: %u: control, the GPU's id over zeroes: returns %d, answer %#llx -- %s\n", step,
                            zero_got, zero_answer, zero_got == -3145797 ?
                            "THE SAME CODE: the refusal comes BEFORE the image is read -- the memory or the caller, not the file" :
                            "A DIFFERENT ANSWER: TrustZone does read the buffer -- the refusal of the real file is about the "
                            "image itself (its signature)");
                        zero_answer = ~0ull;
                        zero_got = scm("the control: the same zeroes as the audio's metadata",
                            (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_INIT_IMAGE, SCM_VALUE_AND_BUFFER, 1, zero_phys, 0,
                            &zero_answer);
                        printf("adreno: %u: control, the audio's id over the same zeroes: returns %d, answer %#llx\n", step,
                            zero_got, zero_answer);
                }
        }
        /*
         * R28, the shape sweeps (the refusal path only -- the taken path never lands here):
         * RETIRED from the default run.  R27b's battle report closed their charge: every
         * three-word form died at the SMC itself (-5, it never reached TrustZone's verdict),
         * and the one code that survived (E3's word 0x82) is the two-parameter shape's own
         * answer -- the sweeps carried zero information and real reset risk.  They ride only
         * behind adreno_shape_sweep=1, for the record; the default run goes straight from the
         * zero-page controls to the supports/give-up diagnostics.
         */
        if (shape_sweep) {
                vm_offset_t sweep_page = 0;
                uint64_t sweep_answer = ~0ull;
                int32_t sweep_got;

                announce("R28: the shape sweeps, retired behind adreno_shape_sweep=1 -- E2 the size word, "
                    "E3 the argument word, E4 the id, E5 the state; all INIT_IMAGE alone, nothing "
                    "authenticated, nothing started");
                put_the_log_away("the shape sweeps are next; every one of them is INIT_IMAGE alone");
                /* E2: the third word.  The canonical attempt took the file's whole size (E1);
                 * these are the other sizes the callers of this phone's world use. */
                {
                        static const uint64_t e2[] = { 0x1000, 0x2000, 0xffffffffull };
                        for (unsigned i = 0; i < sizeof(e2) / sizeof(e2[0]); i++) {
                                sweep_got = scm("E2: the size sweep", (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_INIT_IMAGE,
                                    SCM_VALUE_BUFFER_VALUE, PAS_GPU, header, e2[i], &sweep_answer);
                                printf("adreno: %u: E2: size %#llx: %d, answer %#llx\n", step, e2[i], sweep_got,
                                    sweep_answer);
                        }
                }
                /* E2's control: the same file with the hash table's one entry corrupted, in a copy
                 * that lives only in this memory.  Parse fields stay whole; the signature's own
                 * bytes break.  A code that moves = the content was being compared at this call;
                 * a code that stays = the refusal comes before any content is. */
                if (kmem_alloc(kernel_map, &sweep_page, PAGE_SIZE, KMA_KOBJECT | KMA_ZERO | KMA_DATA,
                    VM_KERN_MEMORY_IOKIT) == KERN_SUCCESS) {
                        uint64_t sweep_phys = kvtophys(sweep_page);
                        const uint8_t *src;
                        uint64_t src_size = 0;

                        if ((src = image_file("fw", "a640_zap.mbn", &src_size)) != NULL && src_size <= PAGE_SIZE) {
                                memcpy((void *)sweep_page, src, src_size);
                                for (uint32_t i = 0x1000 + 0xa8; i < 0x1000 + 0xe8; i++) {
                                        ((volatile uint8_t *)sweep_page)[i] ^= 0xaa;
                                }
                                to_memory(sweep_page, PAGE_SIZE);
                                put_the_log_away("the corrupted-hash-table control is next");
                                sweep_got = scm("E2 control: the hash table corrupted in a RAM copy (parse "
                                    "intact, the signature's own bytes broken)",
                                    (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_INIT_IMAGE, SCM_VALUE_BUFFER_VALUE,
                                    PAS_GPU, sweep_phys, whole, &sweep_answer);
                                printf("adreno: %u: E2 control: corrupted hash table: %d, answer %#llx -- %s\n", step,
                                    sweep_got, sweep_answer, sweep_got == -3145797 ?
                                    "THE SAME CODE: the refusal comes before the content is compared" :
                                    "A DIFFERENT ANSWER: the content was on the table");
                        }
                }
                put_the_log_away("the argument-word sweep is next");
                /* E3: the argument word.  0x83 is E1's (three: value, buffer, value); 0x82 was
                 * R21-R24b's (two: value, buffer, no size); the rest probe the word itself. */
                {
                        static const uint64_t e3[] = { 0x82, 0x43, 0x03, 0x81, 0x01 };
                        for (unsigned i = 0; i < sizeof(e3) / sizeof(e3[0]); i++) {
                                sweep_got = scm("E3: the argument word sweep", (scm_how & ~SCM_FAST) | SCM_SIP |
                                    SCM_PAS_INIT_IMAGE, e3[i], PAS_GPU, header, whole, &sweep_answer);
                                printf("adreno: %u: E3: word %#llx: %d, answer %#llx\n", step, e3[i], sweep_got,
                                    sweep_answer);
                        }
                }
                put_the_log_away("the id sweep is next");
                /* E4: the id.  The same metadata page, the same size, the other doors -- audio,
                 * modem, video, then the rest of the table.  (Nothing but INIT_IMAGE is sent.) */
                {
                        static const uint32_t e4[] = { 1, 4, 9, 2, 3, 5, 6, 7, 8, 10, 11, 12, 14, 15 };
                        for (unsigned i = 0; i < sizeof(e4) / sizeof(e4[0]); i++) {
                                sweep_got = scm("E4: the id sweep", (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_INIT_IMAGE,
                                    SCM_VALUE_BUFFER_VALUE, e4[i], header, whole, &sweep_answer);
                                printf("adreno: %u: E4: id %u: %d, answer %#llx\n", step, e4[i], sweep_got,
                                    sweep_answer);
                        }
                }
                put_the_log_away("the state test is next (the same call twice)");
                /* E5: the state.  The same call twice over; a code that moves between the takes
                 * is a state answer, not a file one.  (The half after the give-up is at the end.) */
                for (unsigned i = 0; i < 2; i++) {
                        sweep_got = scm("E5: the same call again", (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_INIT_IMAGE,
                            SCM_VALUE_BUFFER_VALUE, PAS_GPU, header, whole, &sweep_answer);
                        printf("adreno: %u: E5: take %u: %d, answer %#llx\n", step, i + 1, sweep_got, sweep_answer);
                }
        }
        answer = ~0ull;
        scm("whether it \"supports\" the GPU's image", scm_how | SCM_PAS_IS_SUPPORTED, SCM_ONE_VALUE, PAS_GPU, 0, 0, &answer);
        answer = ~0ull;
        scm("the same of the audio's, for comparison", scm_how | SCM_PAS_IS_SUPPORTED, SCM_ONE_VALUE, 1, 0, 0, &answer);
        if (!tzunload) {
                zap_unload();
        }
        if (shape_sweep) {
                put_the_log_away("the give-up's second half is next (after the give-up)");
                /* E5's other half: after the give-up.  A code that changes here answers for the
                 * state, not for the file. */
                {
                        uint64_t after_answer = ~0ull;
                        int32_t after_got = scm("E5: after the give-up", (scm_how & ~SCM_FAST) | SCM_SIP | SCM_PAS_INIT_IMAGE,
                            SCM_VALUE_BUFFER_VALUE, PAS_GPU, header, whole, &after_answer);
                        printf("adreno: %u: E5 after give-up: %d, answer %#llx\n", step, after_got, after_answer);
                }
        }
        /*
         * R28: the fuses, RETIRED from the default run.  R27b convicted them (R25's t=4s
         * death was E7's next 256-byte row; moving it to step 19's end in R25b only moved
         * the body, the trigger stayed).  They ride only behind adreno_qfprom=1: corrected
         * window first (Linux's), raw second, one line every 256 bytes so a death names its row.
         */
        if (qfprom_on) {
                announce("read the secboot and rollback fuses (R28: retired behind adreno_qfprom=1, "
                    "R27b's t=4s verdict -- corrected window first, raw second, one line every 256 bytes)");
                put_the_log_away("the fuse sweep is next (the R25 killer's family); every row it announces "
                    "is on the storage before it is read");
                for (unsigned w = 0; w < 2; w++) {
                        uint32_t base = w == 0 ? QFPROM_CORRECTED : 0;
                        printf("adreno: %u: qfprom %#x window, non-zero words:\n", step, qfprom.phys + base);
                        for (uint32_t off = 0; off < 0x400; off += 4) {
                                if ((off & 0xff) == 0) {
                                        printf("adreno: %u:   reading %#x\n", step, qfprom.phys + base + off);
                                        put_the_log_away("the fuse row above is the next one read");
                                }
                                uint32_t v = rd(qfprom.base + base + off);
                                if (v != 0) {
                                        printf("adreno: %u:   %#x = %#010x\n", step, qfprom.phys + base + off, v);
                                }
                        }
                }
        }
        return false;
}

/* ---- step 20: the 3D core's registers, its command processor started, its first command ------------------- */

static bool
step_core_start(void)
{
        static const uint32_t address_modes[] = { 0x0842, 0x0c01, 0x8601, 0x8e05, 0x9e01, 0xbe05, 0xa601, 0x9601, 0x0e00, 0xae01,
                                                  0xb601, 0xf810 };
        uint32_t blind = 0;

        if (!cx_ready() || !core_is_on || !core_space_is_on) {
                return false;
        }
        announce("write the 3D core's registers as Linux does before it starts the command processor: the first writes "
            "to the core itself");
        put(&gbif, "halt", GBIF_HALT, 0);
        put(&gpu, "rbbm_gbif_halt", RBBM_GBIF_HALT, 0);
        put(&gpu, "rbbm_secvid_tsb_cntl", RBBM_SECVID_TSB_CNTL, 0);
        put64(&gpu, "rbbm_secvid_tsb_trusted_base", RBBM_SECVID_TSB_TRUSTED_BASE, 0);
        put(&gpu, "rbbm_secvid_tsb_trusted_size", RBBM_SECVID_TSB_TRUSTED_SIZE, 0);
        for (unsigned i = 0; i < sizeof(address_modes) / sizeof(address_modes[0]); i++) {
                put(&gpu, "address mode: 64 bits", GPU_WORD(address_modes[i]), 1);
        }
        for (unsigned i = 0; i < 4; i++) {
                put(&gbif, "qsb_side", GBIF_QSB_SIDE0 + 4 * i, 0x00071620);
        }
        put(&gpu, "rbbm_gbif_client_qos_cntl", RBBM_GBIF_CLIENT_QOS_CNTL, 3);
        put(&gpu, "rbbm_perfctr_gpu_busy_masked", RBBM_PERFCTR_GPU_BUSY_MASKED, 0xffffffff);
        put64(&gpu, "uche_write_range_max", UCHE_WRITE_RANGE_MAX, 0x0001ffffffffffc0ull);
        put64(&gpu, "uche_trap_base", UCHE_TRAP_BASE, 0x0001fffffffff000ull);
        put64(&gpu, "uche_write_thru_base", UCHE_WRITE_THRU_BASE, 0x0001fffffffff000ull);
        put(&gpu, "uche_filter_cntl", UCHE_FILTER_CNTL, 0x804);
        put(&gpu, "uche_cache_ways", UCHE_CACHE_WAYS, 0x4);
        put(&gpu, "cp_roq_thresholds_2", CP_ROQ_THRESHOLDS_2, 0x02000140);
        put(&gpu, "cp_roq_thresholds_1", CP_ROQ_THRESHOLDS_1, 0x8040362c);
        put(&gpu, "cp_lpac_prog_fifo_size", CP_LPAC_PROG_FIFO_SIZE, 0x20);
        put(&gpu, "cp_mem_pool_size", CP_MEM_POOL_SIZE, 128);
        put(&gpu, "pc_dbg_eco_cntl", PC_DBG_ECO_CNTL, 0x00200200);
        put(&gpu, "cp_ahb_cntl", CP_AHB_CNTL, 1);
        /*
         * How textures in the GPU's own tiled and compressed layouts are laid out in memory (Linux's
         * a6xx_calc_ubwc_config, a6xx_set_ubwc_config for this GPU: highest bank bit 14, and the
         * rest as below); Mesa is told the same (redwood_gpu_parameter()).
         */
        put(&gpu, "rb_nc_mode_cntl", GPU_WORD(0x8e08), 1u << 11 | 1u << 4 | 1u << 1);
        put(&gpu, "tpl1_nc_mode_cntl", GPU_WORD(0xb604), 1u << 1);
        put(&gpu, "sp_nc_mode_cntl", GPU_WORD(0xae02), 2u << 4 | 1u << 1);
        put(&gpu, "uche_mode_cntl", GPU_WORD(0x0e01), 1u << 21);
        put(&gpu, "rbbm_nc_mode_cntl", GPU_WORD(0x0534), 1);
        put(&gpu, "rbbm_perfctr_cntl", RBBM_PERFCTR_CNTL, 1);
        put(&gpu, "rbbm_interface_hang_int_cntl", RBBM_INTERFACE_HANG_INT_CNTL, 1u << 30 | 0x1fffff);
        put(&gpu, "uche_client_pf", UCHE_CLIENT_PF, 1u << 7 | 1);
        put(&gpu, "cp_chicken_dbg", CP_CHICKEN_DBG, 1);
        put(&gpu, "rbbm_gbif_client_qos_cntl", RBBM_GBIF_CLIENT_QOS_CNTL, 0);
        put(&gpu, "cp_apriv_cntl", CP_APRIV_CNTL, 1u << 6 | 1u << 5 | 1u << 3 | 1u << 2 | 1u << 1);
        put(&gpu, "rb_dbg_eco_cntl", RB_DBG_ECO_CNTL, 0x04100000);     /* Mesa's value for this GPU */
        /*
         * adreno_blind=1: the command processor is started with its microcode at an address where
         * nothing is. All it can do then is ask the SMMU for it, and the SMMU's record of the refusal
         * shows that its requests come through context 0 as meant, and what they look like.
         */
        PE_parse_boot_argn("adreno_blind", &blind, sizeof(blind));
        put64(&gpu, "cp_sqe_instr_base", CP_SQE_INSTR_BASE, blind ? CORE_NOWHERE : CORE_SQE_ADDRESS);
        put64(&gpu, "cp_rb_base", CP_RB_BASE, CORE_RING_ADDRESS);
        put(&gpu, "cp_rb_cntl", CP_RB_CNTL, CP_RB_CNTL_VALUE);
        get(&gpu, "cp_sqe_instr_base", CP_SQE_INSTR_BASE);
        get(&gpu, "cp_rb_base", CP_RB_BASE);
        get(&gpu, "cp_rb_cntl", CP_RB_CNTL);
        get(&gpu, "cp_apriv_cntl", CP_APRIV_CNTL);
        get(&gpu, "rbbm_status", RBBM_STATUS);

        announce(blind ? "start the 3D core's command processor with its microcode nowhere" :
            "start the 3D core's command processor");
        core_runs = true;
        put(&gpu, "cp_sqe_cntl", CP_SQE_CNTL, 1);
        sleep_ms(20);
        printf("adreno: %u: alive after it; status %#x, interrupts %#x, command processor: fault %#x, interrupts %#x, "
            "protection %#x, ring read up to %u\n", step, rd(gpu.base + RBBM_STATUS), rd(gpu.base + RBBM_INT_0_STATUS),
            rd(gpu.base + CP_HW_FAULT), rd(gpu.base + CP_INTERRUPT_STATUS), rd(gpu.base + CP_PROTECT_STATUS),
            rd(gpu.base + CP_RB_RPTR));
        get(&smmu, "sgfsr", SMMU_SGFSR);
        get(&smmu, "sgfsynr0", SMMU_SGFSR + 8);
        get(&smmu, "sgfsynr1", SMMU_SGFSR + 12);
        if (blind) {
                return space_faulted(&core_space);
        }
        if (second_space_is_on) {
                sleep_ms(200);
                return space_faulted(&second_space) | !space_faulted(&core_space);
        }
        if (space_faulted(&core_space)) {
                return false;
        }

        announce("give the 3D core its first command, \"initialize\"");
        ring_command(CP_ME_INIT, 8);            /* a6xx_cp_init */
        ring(0x0000002f);
        ring(0x00000003);                       /* several hardware contexts */
        ring(0x20000000);                       /* error detection */
        ring(0);
        ring(0);
        ring(0);
        ring(0);
        ring(0);
        if (!ring_run("initialize its command processor")) {
                return false;
        }
        printf("adreno: %u: THE 3D CORE TAKES COMMANDS\n", step);
        return true;
}

/* ---- step 21: out of secure mode; a word written on command ------------------------------------------------ */

static bool
step_core_commands(void)
{
        volatile uint32_t *scratch = (volatile uint32_t *)core_scratch.memory;

        if (!cx_ready() || !core_runs) {
                return false;
        }
        announce("command the 3D core out of secure mode (it asks the zap shader), then to write a word into a page of ours");
        ring_command(CP_SET_SECURE_MODE, 1);
        ring(0);
        if (!ring_run("leave secure mode")) {
                return false;
        }
        ring_command(CP_MEM_WRITE, 3);
        ring(CORE_SCRATCH_ADDRESS);
        ring(0);
        ring(0x600dc0de);
        if (!ring_run("write a word to memory")) {
                return false;
        }
        to_memory(core_scratch.memory, 64);
        printf("adreno: %u: the page reads %#x: %s\n", step, scratch[0], scratch[0] == 0x600dc0de ?
            "THE 3D CORE WRITES OUR MEMORY ON COMMAND" : "not what it was told to write");
        return scratch[0] == 0x600dc0de;
}

/* ---- step 22: a frame filled by the GPU, on the panel ------------------------------------------------------ */

/*
 * The simplest thing the 3D core draws: its 2D engine filling a rectangle with one colour (Mesa's
 * fd6_blitter.cc: fd6_clear_surface; the registers under their older names, as in Linux's
 * a6xx.xml), three times for three bands. The picture is 32-bit pixels in rows of a multiple of
 * 64 bytes, in memory of ours; the framebuffer driver copies it to the panel.
 */
#define RB_CCU_CNTL             0x8e07          /* register numbers: these are written by command */
#define GRAS_2D_BLIT_CNTL       0x8400
#define GRAS_2D_DST_TL          0x8405
#define GRAS_2D_DST_BR          0x8406
#define RB_2D_BLIT_CNTL         0x8c00
#define RB_2D_PIXEL_CNTL        0x8c01
#define RB_2D_DST_INFO          0x8c17
#define RB_2D_DST               0x8c18
#define RB_2D_DST_PITCH         0x8c1a
#define RB_2D_SRC_SOLID_C0      0x8c2c
#define SP_2D_DST_FORMAT        0xacc0
#define FORMAT_8888             0x30
#define BLIT_SOLID_8888         (1u << 7 | FORMAT_8888 << 8 | 0xfu << 20 | 0x10u << 24) /* one colour; all channels; 8-bit */
#define DST_BGRA_LINEAR         (FORMAT_8888 | 1u << 10)        /* rows of pixels, blue first in memory */
#define EVENT_FLUSH_COLOR       0x1d
#define EVENT_CACHE_FLUSH       0x04
#define TARGET_WIDTH            1080
#define TARGET_HEIGHT           2400
#define TARGET_ROW              4352            /* 1088 pixels: the next multiple of 64 bytes */

extern void MiniIOMobileFramebufferShow(const uint8_t *pixels, uint32_t width, uint32_t height, uint32_t row_bytes);

static void
ring_fill(uint32_t top, uint32_t bottom, uint32_t red, uint32_t green, uint32_t blue)
{
        ring_register(GRAS_2D_DST_TL, top << 16);
        ring_register(GRAS_2D_DST_BR, (TARGET_WIDTH - 1) | (bottom - 1) << 16);
        ring_register(RB_2D_SRC_SOLID_C0, red);         /* a register for each part of the colour, 0 to 255 */
        ring_register(RB_2D_SRC_SOLID_C0 + 1, green);
        ring_register(RB_2D_SRC_SOLID_C0 + 2, blue);
        ring_register(RB_2D_SRC_SOLID_C0 + 3, 255);
        ring_register(RB_2D_BLIT_CNTL, BLIT_SOLID_8888);
        ring_register(GRAS_2D_BLIT_CNTL, BLIT_SOLID_8888);
        ring_register(SP_2D_DST_FORMAT, 1 | FORMAT_8888 << 3 | 0xfu << 12);
        ring_register(RB_2D_PIXEL_CNTL, 0);
        ring_register(RB_2D_DST_INFO, DST_BGRA_LINEAR);
        ring_register(RB_2D_DST, CORE_TARGET_ADDRESS);
        ring_register(RB_2D_DST + 1, 0);
        ring_register(RB_2D_DST_PITCH, TARGET_ROW >> 6);
        ring_command(CP_WAIT_FOR_IDLE, 0);
        ring_command(CP_BLIT, 1);
        ring(3);                                /* "scale": the 2D engine's one operation */
}

static bool
step_frame(void)
{
        const volatile uint8_t *pixel;

        if (!cx_ready() || !core_runs) {
                return false;
        }
        announce("make a picture's worth of memory for the 3D core (10 MB) and have it fill three bands of colour");
        if (!block_new(&core_space, &core_target, CORE_TARGET_ADDRESS, TARGET_ROW * TARGET_HEIGHT, PTE_CORE)) {
                return false;
        }
        /* tables that the SMMU has in use have grown: it has not seen these addresses, so nothing stale to forget */
        for (uint32_t i = 0; i < core_space.table_count; i++) {
                to_memory((vm_offset_t)core_space.tables[i].table, 4096);
        }
        ring_command(CP_SET_MARKER, 1);
        ring(12);                               /* "2D blits follow" */
        ring_register(RB_CCU_CNTL, 0x10000004); /* Mesa's for this GPU when it draws straight into memory */
        ring_fill(0, 800, 255, 128, 0);         /* orange */
        ring_fill(800, 1600, 255, 255, 255);
        ring_fill(1600, TARGET_HEIGHT, 0, 128, 255);
        /*
         * What it has drawn out of its caches into memory. Each of the two events is followed by an
         * address and a word that the core writes there when it is done (Linux's a6xx_submit; Mesa's
         * tu6_emit_event_write: the events named ..._TS): the core takes those three words whether
         * they are there or not.
         */
        ring_command(CP_EVENT_WRITE, 4);
        ring(EVENT_FLUSH_COLOR);
        ring(CORE_SCRATCH_ADDRESS + 32);
        ring(0);
        ring(0x0c0c0c0c);
        ring_command(CP_EVENT_WRITE, 4);
        ring(EVENT_CACHE_FLUSH);
        ring(CORE_SCRATCH_ADDRESS + 16);
        ring(0);
        ring(0x0d0e0d0e);
        ring_command(CP_WAIT_FOR_IDLE, 0);
        if (!ring_run("fill a picture")) {
                return false;
        }
        to_memory(core_scratch.memory, 64);
        printf("adreno: %u: the words it writes when its caches are in memory: %#x, %#x (told: 0xc0c0c0c, 0xd0e0d0e)\n", step,
            ((volatile uint32_t *)core_scratch.memory)[8], ((volatile uint32_t *)core_scratch.memory)[4]);
        to_memory(core_target.memory, core_target.size);
        pixel = (const volatile uint8_t *)core_target.memory;
        printf("adreno: %u: the picture's first pixel is %02x %02x %02x %02x (blue green red alpha; told: 00 80 ff ff), one in "
            "the middle %02x %02x %02x %02x, one in the last row %02x %02x %02x %02x\n", step, pixel[0], pixel[1], pixel[2],
            pixel[3], pixel[1200 * TARGET_ROW + 2160], pixel[1200 * TARGET_ROW + 2161], pixel[1200 * TARGET_ROW + 2162],
            pixel[1200 * TARGET_ROW + 2163], pixel[2399 * TARGET_ROW + 4316], pixel[2399 * TARGET_ROW + 4317],
            pixel[2399 * TARGET_ROW + 4318], pixel[2399 * TARGET_ROW + 4319]);
        if ((pixel[0] | pixel[1] | pixel[2]) == 0 || pixel[1200 * TARGET_ROW + 2160] == 0 || (pixel[2399 * TARGET_ROW + 4316] |
            pixel[2399 * TARGET_ROW + 4317] | pixel[2399 * TARGET_ROW + 4318]) == 0) {
                printf("adreno: %u: the picture is not what the 3D core was told to draw\n", step);
                return false;
        }
        MiniIOMobileFramebufferShow((const uint8_t *)core_target.memory, TARGET_WIDTH, TARGET_HEIGHT, TARGET_ROW);
        printf("adreno: %u: A PICTURE DRAWN BY THE GPU IS ON THE PANEL: orange, white, blue from the top\n", step);
        return true;
}

/* ---- the way back ------------------------------------------------------------------------------------- */

/*
 * Nothing of the GPU block may outlive the kernel that started it: the memory its SMMU context
 * points into is the next kernel's to use. In the order of Linux's a6xx_gmu_shutdown: the 3D
 * core given back, the GMU told to drop its votes ("slumber") and stopped, its RPMh channel put
 * to sleep; then the processor into reset and its stream sent nowhere.
 */
/*
 * The 3D core's own power switch is the GMU's to operate. Where the GMU is gone and has left it
 * on (it is in reset; it did not answer when told to slumber), the switch is turned off from
 * here (vendor's a6xx_gmu_suspend): a core that keeps its power while the rest of the block
 * loses it is found by the next start as the last one left it, in the middle of things.
 */
static void
gx_off(void)
{
        uint32_t gx = get(&gpucc, "gx_gdscr", GPUCC_GX_GDSCR);

        if (gx & GDSC_POWER_ON) {
                put(&gpucc, "gx_gdscr", GPUCC_GX_GDSCR, gx | GDSC_SW_COLLAPSE);
                wait_for(&gpucc, GPUCC_GX_GDSCR, GDSC_POWER_ON, 0, 100);
                get(&gpucc, "gx_gdscr", GPUCC_GX_GDSCR);
        }
}

/* What step 4 does when it finds the GPU block running: see there. Nothing here has been set up by this kernel. */
static void
stale_stop(void)
{
        need(&gmu);
        need(&rscc);
        put(&gmu, "cm3_sysreset", GMU_CM3_SYSRESET, 1);
        gx_off();
        if (rd(rscc.base + RSCC_SEQ_MEM_0_DRV0) == 0xeaaae5a0 && !(rd(rscc.base + RSCC_RSC_STATUS0_DRV0) & RSCC_ASLEEP)) {
                put(&gmu, "rscc_control_req", GMU_RSCC_CONTROL_REQ, 1);
                wait_for(&rscc, RSCC_RSC_STATUS0_DRV0, RSCC_ASLEEP, RSCC_ASLEEP, 100);
                get(&rscc, "rsc_status0_drv0", RSCC_RSC_STATUS0_DRV0);
                put(&gmu, "rscc_control_req", GMU_RSCC_CONTROL_REQ, 0);
        }
        if (smmu_layout()) {
                for (uint32_t match = 0; match < 3; match++) {
                        put(&smmu, "smr", SMMU_SMR(match), 0);
                        put(&smmu, "s2cr", SMMU_S2CR(match), SMMU_S2CR_FAULT);
                        put(&smmu, "cb sctlr", (smmu_pages + match) * smmu_page + SMMU_CB_SCTLR, 0);
                }
        }
        put(&gpucc, "cx_gdscr", GPUCC_CX_GDSCR, rd(gpucc.base + GPUCC_CX_GDSCR) | GDSC_SW_COLLAPSE);
        for (int i = 0; i < 1000 && cx_is_on(); i++) {
                sleep_us(100);
        }
        get(&gpucc, "cx_gdscr", GPUCC_CX_GDSCR);
        get(&gpucc, "cx_hw_ctrl", GPUCC_CX_HW_CTRL);
}

/*
 * The GPU block's bus interface is told to let what is under way finish and to start nothing
 * more (Linux's a6xx_bus_clear_pending_transactions). (Without it, a core whose SMMU context is
 * taken away under a draw leaves the SMMU waiting for good, and the CX half cannot be switched
 * off any more: only a restart of the phone gets the GPU back.)
 * `for_good`: the 3D core may be in the middle of a frame and nobody will tell it to stop (a
 * panic; a core that does not come to rest): its own side is halted too, and all of it stays
 * halted, so that nothing of it reaches memory after this, whatever becomes of its tables.
 * Otherwise the halt is lifted again at once, as Linux does: the GMU reaches its memory through
 * the same interface, and one that cannot read its messages does not answer "slumber".
 */
static void
core_bus_halt(bool for_good)
{
        if (for_good) {
                wr(gpu.base + RBBM_GBIF_HALT, 1);
                for (int i = 0; i < 100000 && !(rd(gpu.base + RBBM_GBIF_HALT_ACK) & 1); i++) {
                }
        }
        wr(gbif.base + GBIF_HALT, 1);
        for (int i = 0; i < 100000 && !(rd(gbif.base + GBIF_HALT_ACK) & 1); i++) {
        }
        wr(gbif.base + GBIF_HALT, 2);
        for (int i = 0; i < 100000 && !(rd(gbif.base + GBIF_HALT_ACK) & 2); i++) {
        }
        if (!for_good) {
                wr(gbif.base + GBIF_HALT, 0);
        }
}

static bool core_stuck;                 /* the 3D core has not finished what it was given, and is not waited for */

static void
gpu_block_stop(void)
{
        uint32_t slumber[3] = { 0, 0, 0 };
        bool power_off = false;

        step = 0;
        newest_step = ~0u;
        if (core_runs && core_is_on) {
                /* (what the core looks like when it is given back: a GMU that then does not answer may have its reasons here) */
                printf("adreno: %u: the 3D core before it is stopped: status %#x, interrupts %#x, ring read up to %u of %u; the GMU "
                    "sees it busy: %#x\n", step, rd(gpu.base + RBBM_STATUS), rd(gpu.base + RBBM_INT_0_STATUS),
                    rd(gpu.base + CP_RB_RPTR), ring_at, rd(gmu.base + GMU_AO_GPU_CX_BUSY_STATUS));
                core_bus_halt(core_stuck);
                printf("adreno: %u: the GPU block's bus has been halted%s (rbbm_gbif_halt_ack %#x, gbif halt_ack %#x)\n", step,
                    core_stuck ? ", the 3D core's side too, and stays so" : " and let go again", rd(gpu.base + RBBM_GBIF_HALT_ACK),
                    rd(gbif.base + GBIF_HALT_ACK));
        }
        if (core_is_on) {
                put(&gmu, "host2gmu_intr_set", GMU_HOST2GMU_INTR_SET, 1u << 31);
                core_is_on = false;
        }
        if (gmu_talks) {
                hfi_send("\"slumber\"", HFI_PREPARE_SLUMBER, slumber, 3);
                if (!wait_for(&gmu, GMU_AO_GPU_CX_BUSY_STATUS, 1u << 23, 0, 100)) {
                        get(&gmu, "ao_gpu_cx_busy_status", GMU_AO_GPU_CX_BUSY_STATUS);
                }
                get(&gmu, "sptprac_pwr_clk_status", GMU_SPTPRAC_PWR_CLK_STATUS);
                get(&gpucc, "gx_gdscr", GPUCC_GX_GDSCR);
                gmu_talks = false;
        }
        if (gmu_runs) {
                put(&gmu, "cm3_sysreset", GMU_CM3_SYSRESET, 1);
                gmu_runs = false;
                gx_off();                               /* (a GMU that has slumbered has done it itself) */
        }
        if (zap_is_loaded) {
                /* the next kernel loads its own: see step_zap_load() */
                printf("adreno: %u: the zap shader: %s\n", step, zap_unload() ? "TrustZone has given it up" :
                    "TRUSTZONE DOES NOT GIVE IT UP; the next kernel will find it there");
                zap_is_loaded = false;
        }
        /* vendor's a6xx_rscc_sleep_sequence: the GMU's votes in RPMh are dropped, by the always-on sequencer */
        if (rsc_awake && rd(rscc.base + RSCC_SEQ_MEM_0_DRV0) == 0xeaaae5a0) {
                put(&gmu, "rscc_control_req", GMU_RSCC_CONTROL_REQ, 1);
                if (!wait_for(&rscc, RSCC_RSC_STATUS0_DRV0, RSCC_ASLEEP, RSCC_ASLEEP, 100)) {
                        printf("adreno: %u: the GMU's RPMh channel does not go to sleep\n", step);
                }
                get(&rscc, "rsc_status0_drv0", RSCC_RSC_STATUS0_DRV0);
                put(&gmu, "rscc_control_req", GMU_RSCC_CONTROL_REQ, 0);
        }
        rsc_awake = false;
        if (gmu_space_is_on) {
                space_faulted(&gmu_space);
                space_off(&gmu_space, GMU_MATCH);
                gmu_space_is_on = false;
                power_off = true;
        }
        if (second_space_is_on) {
                space_faulted(&second_space);
                space_off(&second_space, second_match);
                second_space_is_on = false;
        }
        if (second_stream_is_on) {
                put(&smmu, "smr", SMMU_SMR(second_match), 0);
                put(&smmu, "s2cr", SMMU_S2CR(second_match), SMMU_S2CR_FAULT);
                second_stream_is_on = false;
        }
        if (core_space_is_on) {
                space_faulted(&core_space);
                space_off(&core_space, core_match);
                core_space_is_on = core_runs = false;
        }
        /*
         * And the CX half off again, as it was when the phone started: the GMU with all it remembers
         * and the SMMU lose their power, and the next time starts from nothing (vendor's
         * a6xx_gmu_suspend). The switch takes votes; ours is all this withdraws.
         */
        if (power_off && gpucc_answers()) {
                put(&gpucc, "cx_gdscr", GPUCC_CX_GDSCR, rd(gpucc.base + GPUCC_CX_GDSCR) | GDSC_SW_COLLAPSE);
                for (int i = 0; i < 1000 && cx_is_on(); i++) {
                        sleep_us(100);
                }
                get(&gpucc, "cx_gdscr", GPUCC_CX_GDSCR);
                get(&gpucc, "cx_hw_ctrl", GPUCC_CX_HW_CTRL);
                cx_off = !cx_is_on();
        }
}

/*
 * The same when there is no time for asking: the kernel is on its way back to the loader (the
 * end of a run, a panic). Only the two writes that keep the GMU out of memory that is no longer
 * its to use.
 */
void
redwood_adreno_quiesce(void)
{
        if (cx_off) {
                return;
        }
        if (gmu_runs) {
                wr(gmu.base + GMU_CM3_SYSRESET, 1);
                gmu_runs = false;
        }
        if (core_runs) {
                core_bus_halt(true);
                core_runs = false;
        }
        if (gmu_space_is_on) {
                wr(smmu.base + smmu_context(&gmu_space) + SMMU_CB_SCTLR, 0);
                gmu_space_is_on = false;
        }
        if (core_space_is_on) {
                wr(smmu.base + smmu_context(&core_space) + SMMU_CB_SCTLR, 0);
                core_space_is_on = false;
        }
        if (second_space_is_on) {
                wr(smmu.base + smmu_context(&second_space) + SMMU_CB_SCTLR, 0);
                second_space_is_on = false;
        }
}

/* ---- the GPU kept on, for the system to draw with --------------------------------------------------- */

/*
 * With boot-arg adreno_keep=1 the steps are not undone: the GPU block stays as step 21 leaves it,
 * the 3D core powered and taking commands, and the service RedwoodGPU (iokit/Kernel/RedwoodGPU.cpp)
 * hands it to programs through the interface of Linux's msm driver, which is what Mesa's
 * freedreno speaks. That file keeps the buffers and the clients; what is the GPU's is here:
 * addresses in the 3D core's SMMU context, the ring, and the word by which the core says how
 * far it has got (a "fence": a submission's number, written by the core when it is through
 * with it). The caller holds one lock around all of these.
 *
 * Boot-arg gpu_recover=1: a submission the core never finishes does not cost the rest of the
 * run. The core's own half is reset and started again, and what was outstanding is given up
 * (redwood_gpu_recover() below, which says what is done and which source says so). Without it
 * such a submission is only reported, and the GPU stays stopped.
 */
#define GPU_CHIP_ID             0x06040001ull   /* dt: "qcom,adreno-640.1" */
#define GPU_ADDRESSES_START     0x20000000ull   /* above everything the steps use; below 4 GB, as Linux keeps them */
#define GPU_ADDRESSES_SIZE      0xe0000000ull
#define GPU_FENCE_AT            0x40            /* in the scratch page */
#define GPU_TIMES_AT            0x400           /* ... and when the core began and ended each of its last submissions */
#define GPU_TIMES               64
#define CP_ALWAYS_ON_COUNTER    0x0980          /* ticks of the 19.2 MHz crystal, 64 bits: for the core to read itself */
#define CP_REG_TO_MEM           0x3e
#define CP_SCRATCH_REG2         0x0885
#define CP_IB1_BASE             GPU_WORD(0x0928)
#define CP_IB1_REM_SIZE         GPU_WORD(0x092a)
#define CP_IB2_BASE             GPU_WORD(0x092b)
#define CP_IB2_REM_SIZE         GPU_WORD(0x092d)
#define CP_INDIRECT_BUFFER      0x3f
#define EVENT_INVALIDATE_DEPTH  0x18
#define EVENT_INVALIDATE_COLOR  0x19
/* and, for a core that has stopped answering (gpu_recover=1: redwood_gpu_recover() below) */
#define RBBM_SW_RESET_CMD       GPU_WORD(0x0043)        /* a6xx.xml; 1 holds the 3D core's own half (GX) in reset */
#define RBBM_INT_CLEAR_CMD      GPU_WORD(0x0037)        /* the bits of rbbm_int_0_status written here are forgotten */
#define GMU_PWR_COL_KEEPALIVE   GMU_WORD(0x50c3)        /* a6xx_gmu.xml; 1: the GMU keeps the core powered, whatever it makes of it */
#define GMU_AHB_FENCE_STATUS_CLR GMU_WORD(0x9314)       /* the bits of ahb_fence_status written here are forgotten */
#define GPU_RECOVERIES          64                      /* at most in one run: a program that hangs every submission fills no log with this */

static bool gpu_kept;
static uint32_t gpu_fence;              /* the number of the last submission */
static uint32_t gpu_timed;              /* ... of the last whose times have been added up */
static uint64_t gpu_ticks;              /* how long the core has worked on the submissions added up */
static uint32_t gpu_timed_count;
static uint32_t gpu_timed_by[7];        /* ... how many of them under 0.1, 0.25, 0.5, 1, 2, 4 ms, and longer */
static uint32_t gpu_exact = 1;          /* boot-arg gpu_exact=0: the core does not wait to be idle before it notes the time */
static uint32_t gpu_recover;            /* boot-arg gpu_recover=1: a core that has stopped answering is reset and given work again */
static uint32_t gpu_recoveries;         /* ... and how many times that has been done in this run */
uint32_t gpu_log;                       /* boot-arg gpu_log=1: a line for every submission and the time the core spent on it */
extern void redwood_tl(const char *format, ...) __printflike(1, 2);
static struct {
        uint64_t start, size;
} gpu_free[1024];                       /* the addresses not in use, in order */
static unsigned gpu_free_count;

/*
 * ---- the 3D core's addresses, and who owns them -------------------------------------------
 *
 * There is one address space for everything the 3D core reads and writes: context 0 of the
 * GPU's SMMU, 39 bits of it, three levels of 4K pages, made by step 18 (core_space above).
 * Below GPU_ADDRESSES_START the steps put their own blocks at constants of their own -- the
 * ring at 0x01000000, the microcode at 0x01100000, the scratch page at 0x01200000, the test's
 * target at 0x10000000 -- and those are constants and not allocations: there is one of each
 * for the whole run. From GPU_ADDRESSES_START up the addresses are handed out a buffer at a
 * time by redwood_gpu_address() out of gpu_free, which is this driver's whole allocator: a
 * list of what is not in use, lowest first, first fit, coalesced when given back.
 *
 * That division of labour is Linux's msm, not an invention here. There the kernel picks an
 * address out of a drm_mm when a buffer is first asked about and keeps it in the object until
 * the object goes (msm_gem_get_iova -> get_vma_locked), and userspace only asks:
 * MSM_INFO_GET_IOVA. Mesa's freedreno does exactly that and nothing else -- fd_bo_init_common
 * sets bo->iova from msm_bo_iova(), one GEM_INFO call per buffer -- so the kernel is the
 * allocator for both of this port's consumers and neither of them fights it.
 *
 * One thing to know before anything is built on these addresses: there is one space, so an
 * address is the same memory in every program. That is what makes an argument buffer possible
 * at all -- a buffer of addresses means something wherever it is read -- and it is also why
 * one program's shader can reach another's buffers by naming an address it was never given.
 * Nothing here can check an address a shader computes; only page tables of the client's own
 * could, and there is one context for everybody (space_on() above, context 0).
 *
 * boot-arg gpu_va:
 *   0 (the default)  as it has always been. Addresses packed one allocation after another,
 *                    and VA_START/VA_SIZE not answered.
 *   1                VA_START and VA_SIZE answered, and the space's state printed with the
 *                    other GPU numbers. Not one address changes.
 *   2               ... and a granule of addresses left unmapped after every buffer, so that a
 *                    read or a write that runs off the end of one buffer falls into nothing and
 *                    the SMMU says so with the address (space_faulted()) instead of landing
 *                    silently in whatever buffer was made next. This is for argument buffers
 *                    above all: there the GPU dereferences an address that a program wrote into
 *                    memory, and a wrong address is otherwise indistinguishable from wrong data.
 *                    Every address moves, so it is not the default.
 *
 * Why VA_START and VA_SIZE are behind the arg rather than always answered: in msm they mean
 * "this space is yours to place buffers in", and msm refuses them outright to a client that
 * does not have page tables of its own (adreno_gpu.c, adreno_get_param: -EINVAL when
 * ctx->aspace == gpu->aspace). Here every client is in the one context 0, so the honest
 * default is msm's refusal; with the arg they are answered as what they are here, which is
 * the range the kernel allocates out of -- a number to size a heap or maxBufferLength against,
 * not a licence to place anything. Nothing in Mesa is affected either way: freedreno asks
 * VA_SIZE only at drm version 9 and up (FD_VERSION_VA_SIZE), and tools/mesa/redwood-drm says 1.7.
 */
uint32_t gpu_va;                        /* boot-arg gpu_va: 0 as before, 1 describe, 2 also guard */
#define GPU_ADDRESS_GRANULE     0x10000ull      /* what one allocation is rounded up to */
static uint64_t gpu_va_used;            /* addresses handed out and not given back */
static uint32_t gpu_va_live;            /* ... to how many buffers */
static unsigned gpu_va_holes;           /* the most entries of gpu_free ever in use at once */
static uint64_t gpu_va_lost;            /* given back, and gpu_free had no room to remember it */
static uint32_t gpu_va_refused;         /* allocations that found no run long enough */

/*
 * How many addresses a buffer of so many bytes takes. Both ends of the allocator use this, so
 * the guard is part of the allocation and not a hole left between two: a buffer given back
 * returns its guard with it and the free list coalesces exactly as it did without the arg.
 * A hole per live buffer would instead fill the 1024 entries and start losing addresses.
 */
static uint64_t
gpu_address_span(uint64_t size)
{
        uint64_t span = (size + (GPU_ADDRESS_GRANULE - 1)) & ~(GPU_ADDRESS_GRANULE - 1);

        return gpu_va >= 2 ? span + GPU_ADDRESS_GRANULE : span;
}

extern void RedwoodGPUStart(void);
extern void RedwoodGPUStop(void);       /* takes the clients' lock, then calls redwood_gpu_stop() */
static void gpu_block_stop(void);

/*
 * A run that ends in order -- its time is up, the host asks for the end, five fingers on the
 * screen, a halt: whoever is about to call loader_return() for one of these calls
 * redwood_adreno_stop() first, from a thread that may wait -- gives the GPU back as the steps'
 * own ending does, and the next kernel finds the GPU block as the phone's start leaves it. A
 * panic cannot: it leaves the GPU block running but quiet (redwood_adreno_quiesce()), and the
 * next kernel's step 4 takes it down (stale_stop()). Boot-arg adreno_dirty=1 makes every ending
 * a panic's in this respect, to see that work.
 */
void
redwood_gpu_stop(void)
{
        if (gpu_kept) {
                uint32_t waited = 0;

                gpu_kept = false;                       /* (nothing more is taken) */
                quiet = false;
                /*
                 * What the core has been given, it finishes first: told to slumber in the middle of a
                 * frame, the GMU may not answer. Half a second at the most; then its bus is halted
                 * for good, and its power switched off from here.
                 */
                while (!redwood_gpu_done(gpu_fence) && waited < 5000) {
                        sleep_us(100);
                        waited++;
                }
                core_stuck = !redwood_gpu_done(gpu_fence);
                printf("adreno: the run is about to end: the GPU is given back (%s after %u us)\n",
                    core_stuck ? "STILL AT WORK" : "it had finished what it was given", 100 * waited);
                gpu_block_stop();
        }
}

void
redwood_adreno_stop(void)
{
        uint32_t dirty = 0;

        if (gpu_kept && !(PE_parse_boot_argn("adreno_dirty", &dirty, sizeof(dirty)) && dirty)) {
                RedwoodGPUStop();
        }
}

bool
redwood_gpu_ready(void)
{
        return gpu_kept;
}

/* The answers to msm's GET_PARAM (Linux's adreno_get_param for this GPU; the numbers are msm_drm.h's). */
bool
redwood_gpu_parameter(uint32_t which, uint64_t *value)
{
        switch (which) {
        case 0x01:                              /* GPU_ID: none, the chip ID says it */
        case 0x06:                              /* GMEM_BASE */
        case 0x08:                              /* PP_PGTABLE */
        case 0x09:                              /* FAULTS */
        case 0x0a:                              /* SUSPENDS */
        case 0x11:                              /* RAYTRACING */
                *value = 0;
                return true;
        case 0x02:                              /* GMEM_SIZE */
                *value = 512 * 1024;
                return true;
        case 0x03:                              /* CHIP_ID, with the speed bin above it */
                *value = GPU_CHIP_ID | (uint64_t)speed_bin << 32;
                return true;
        case 0x04:                              /* MAX_FREQ */
                *value = core_khz * 1000ull;
                return true;
        case 0x05:                              /* TIMESTAMP */
                *value = mach_absolute_time();
                return true;
        case 0x07:                              /* PRIORITIES */
                *value = 1;
                return true;
        case 0x10:                              /* HIGHEST_BANK_BIT */
                *value = 14;
                return true;
        case 0x12:                              /* UBWC_SWIZZLE */
                *value = 6;
                return true;
        case 0x13:                              /* MACROTILE_MODE */
                *value = 1;
                return true;
        case 0x0e:                              /* VA_START */
        case 0x0f:                              /* VA_SIZE */
                /*
                 * Addresses are ours to give, so without gpu_va these are refused, which is what
                 * Linux's msm does for a client that shares the GPU's own page tables. With the
                 * arg they say what the range the kernel allocates out of is -- see gpu_va above.
                 */
                if (gpu_va == 0) {
                        return false;
                }
                *value = which == 0x0e ? GPU_ADDRESSES_START : GPU_ADDRESSES_SIZE;
                return true;
        default:
                return false;
        }
}

/*
 * Addresses for a buffer of so many bytes; 0: none left. The address is the buffer's from here
 * until redwood_gpu_address_free(): nothing moves a live buffer, which is what userspace is
 * promised when it asks MSM_INFO_GET_IOVA once and keeps the answer (as freedreno does).
 */
uint64_t
redwood_gpu_address(uint64_t size)
{
        size = gpu_address_span(size);
        for (unsigned i = 0; i < gpu_free_count; i++) {
                if (gpu_free[i].size >= size) {
                        uint64_t address = gpu_free[i].start;

                        gpu_free[i].start += size;
                        gpu_free[i].size -= size;
                        if (gpu_free[i].size == 0) {
                                memmove(&gpu_free[i], &gpu_free[i + 1], (--gpu_free_count - i) * sizeof(gpu_free[0]));
                        }
                        gpu_va_used += size;
                        gpu_va_live++;
                        return address;
                }
        }
        gpu_va_refused++;
        return 0;
}

void
redwood_gpu_address_free(uint64_t address, uint64_t size)
{
        unsigned at;

        size = gpu_address_span(size);
        gpu_va_used -= size;
        gpu_va_live--;
        for (at = 0; at < gpu_free_count && gpu_free[at].start < address; at++) {
        }
        if (at > 0 && gpu_free[at - 1].start + gpu_free[at - 1].size == address) {
                gpu_free[at - 1].size += size;
                if (at < gpu_free_count && address + size == gpu_free[at].start) {
                        gpu_free[at - 1].size += gpu_free[at].size;
                        memmove(&gpu_free[at], &gpu_free[at + 1], (--gpu_free_count - at) * sizeof(gpu_free[0]));
                }
        } else if (at < gpu_free_count && address + size == gpu_free[at].start) {
                gpu_free[at].start = address;
                gpu_free[at].size += size;
        } else if (gpu_free_count < sizeof(gpu_free) / sizeof(gpu_free[0])) {
                memmove(&gpu_free[at + 1], &gpu_free[at], (gpu_free_count++ - at) * sizeof(gpu_free[0]));
                gpu_free[at].start = address;
                gpu_free[at].size = size;
        } else {
                /*
                 * These addresses are lost: there is no room to remember the hole, and handing
                 * them out again without remembering would hand out somebody's live buffer. It
                 * is counted rather than passed over in silence, because a program that makes
                 * and frees buffers in an order that leaves 1024 holes loses address space
                 * steadily and nothing else would say so.
                 */
                gpu_va_lost += size;
        }
        if (gpu_free_count > gpu_va_holes) {
                gpu_va_holes = gpu_free_count;
        }
}

/*
 * What the 3D core's address space holds, for whoever prints the GPU's numbers (gpu_va=1).
 * `largest` is the longest run of addresses still free -- what the biggest buffer that can
 * still be made is bounded by -- and `most` is the most holes there have ever been at once,
 * which is the distance to the one way this allocator fails: at 1024 it starts losing them.
 */
void
redwood_gpu_space(uint64_t *used, uint32_t *live, uint64_t *largest, unsigned *holes, unsigned *most, uint64_t *lost,
    uint32_t *refused)
{
        *largest = 0;
        for (unsigned i = 0; i < gpu_free_count; i++) {
                if (gpu_free[i].size > *largest) {
                        *largest = gpu_free[i].size;
                }
        }
        *used = gpu_va_used;
        *live = gpu_va_live;
        *holes = gpu_free_count;
        *most = gpu_va_holes;
        *lost = gpu_va_lost;
        *refused = gpu_va_refused;
}

/* One 4K page of ours at an address of the 3D core's; after the last of a buffer's, redwood_gpu_mapped(). */
bool
redwood_gpu_map(uint64_t address, uint64_t phys, bool read_only)
{
        return space_map(&core_space, address, phys, PTE_CORE | (read_only ? PTE_READ_ONLY : 0));
}

void
redwood_gpu_mapped(void)
{
        space_commit(&core_space);
}

void
redwood_gpu_unmap(uint64_t address, uint64_t size)
{
        uint32_t context = smmu_context(&core_space);

        for (uint64_t offset = 0; offset < size; offset += 4096) {
                space_map(&core_space, address + offset, 0, 0);
        }
        space_commit(&core_space);
        wr(smmu.base + context + SMMU_CB_TLBIASID, core_space.context);
        wr(smmu.base + context + SMMU_CB_TLBSYNC, 1);
        for (int i = 0; i < 1000 && (rd(smmu.base + context + SMMU_CB_TLBSTATUS) & 1); i++) {
        }
}

/* Memory that the 3D core and a program see without this processor's cache in between: out of that cache. */
void
redwood_gpu_to_memory(void *start, uint64_t size)
{
        to_memory((vm_offset_t)start, size);
}

/* How far the 3D core has got. */
bool
redwood_gpu_done(uint32_t fence)
{
        volatile uint32_t *word = (volatile uint32_t *)(core_scratch.memory + GPU_FENCE_AT);

        to_memory((vm_offset_t)word, 4);
        return (int32_t)(*word - fence) >= 0;
}

uint32_t
redwood_gpu_last(void)
{
        return gpu_fence;
}

/* The core is to write down what time it is: at one end of submission `fence`. */
static void
ring_time(uint32_t fence, uint32_t end)
{
        ring_command(CP_REG_TO_MEM, 3);
        ring(CP_ALWAYS_ON_COUNTER | 2u << 18 | 1u << 30);       /* two words, a 64-bit register */
        ring(CORE_SCRATCH_ADDRESS + GPU_TIMES_AT + 16 * (fence % GPU_TIMES) + 8 * end);
        ring(0);
}

/* Add up the times of the submissions the core has finished since this was last done. */
static void
gpu_times(void)
{
        volatile uint64_t *times = (volatile uint64_t *)(core_scratch.memory + GPU_TIMES_AT);
        uint32_t done;

        to_memory(core_scratch.memory, 4096);
        done = *(volatile uint32_t *)(core_scratch.memory + GPU_FENCE_AT);
        if (done - gpu_timed > GPU_TIMES) {
                gpu_timed = done - GPU_TIMES;           /* (the older ones have been written over) */
        }
        while (gpu_timed != done) {
                uint32_t slot = ++gpu_timed % GPU_TIMES;

                if (gpu_timed != 0 && times[2 * slot + 1] > times[2 * slot]) {
                        static const uint32_t under[6] = { 1920, 4800, 9600, 19200, 38400, 76800 };     /* (19.2 ticks a microsecond) */
                        uint64_t ticks = times[2 * slot + 1] - times[2 * slot];
                        unsigned by = 0;

                        if (gpu_log) {
                                /* "tl: q FENCE took US": what the core spent on that submission (iokit/Kernel/RedwoodGPU.cpp: tl: Q) */
                                redwood_tl("tl: q %u took %llu\n", gpu_timed, ticks * 10 / 192);
                        }

                        while (by < 6 && ticks >= under[by]) {
                                by++;
                        }
                        gpu_timed_by[by]++;
                        gpu_ticks += ticks;
                        gpu_timed_count++;
                }
        }
}

/*
 * How long the 3D core has worked since this was last asked, on how many submissions, and how
 * many of those took it under 0.1, 0.25, 0.5, 1, 2, 4 milliseconds and longer.
 */
void
redwood_gpu_worked(uint64_t *microseconds, uint32_t *submissions, uint32_t by[7])
{
        gpu_times();
        *microseconds = gpu_ticks * 10 / 192;
        *submissions = gpu_timed_count;
        memcpy(by, gpu_timed_by, sizeof(gpu_timed_by));
        bzero(gpu_timed_by, sizeof(gpu_timed_by));
        gpu_ticks = 0;
        gpu_timed_count = 0;
}

/*
 * Command buffers for the 3D core to run, in order: each an address and a count of words. As
 * Linux's a6xx_submit puts them in the ring. Returns the submission's number; 0: the ring is
 * full and the core is not reading it.
 */
uint32_t
redwood_gpu_submit(const uint64_t *addresses, const uint32_t *words, uint32_t count)
{
        uint32_t wanted = 26 + 4 * count, room = 0;

        for (int i = 0; i < 20000; i++) {
                room = (rd(gpu.base + CP_RB_RPTR) + RING_WORDS - ring_at - 1) % RING_WORDS;
                if (room >= wanted) {
                        break;
                }
                sleep_us(100);
        }
        if (room < wanted) {
                return 0;
        }
        gpu_times();
        if (++gpu_fence == 0) {
                gpu_fence = 1;
        }
        ring_time(gpu_fence, 0);
        ring_command(CP_EVENT_WRITE, 1);
        ring(EVENT_INVALIDATE_DEPTH);
        ring_command(CP_EVENT_WRITE, 1);
        ring(EVENT_INVALIDATE_COLOR);
        for (uint32_t i = 0; i < count; i++) {
                ring_command(CP_INDIRECT_BUFFER, 3);
                ring((uint32_t)addresses[i]);
                ring((uint32_t)(addresses[i] >> 32));
                ring(words[i]);
        }
        if (gpu_exact) {
                ring_command(CP_WAIT_FOR_IDLE, 0);      /* the time is the pipeline's, not the command processor's, which is ahead of it */
        }
        ring_time(gpu_fence, 1);
        ring_register(CP_SCRATCH_REG2, gpu_fence);
        ring_command(CP_EVENT_WRITE, 4);
        ring(EVENT_CACHE_FLUSH);
        ring(CORE_SCRATCH_ADDRESS + GPU_FENCE_AT);
        ring(0);
        ring(gpu_fence);
        ring_send();
        return gpu_fence;
}

/* A submission does not come back: what the 3D core and its SMMU context say. */
void
redwood_gpu_report(const char *why)
{
        uint32_t context = smmu_context(&core_space);

        printf("adreno: gpu: %s: submission %u is the last, the core has done %u; ring: read up to %u, written up to %u; status "
            "%#x, interrupts %#x; command processor: fault %#x, interrupts %#x, protection %#x\n", why, gpu_fence,
            *(volatile uint32_t *)(core_scratch.memory + GPU_FENCE_AT), rd(gpu.base + CP_RB_RPTR), ring_at,
            rd(gpu.base + RBBM_STATUS), rd(gpu.base + RBBM_INT_0_STATUS), rd(gpu.base + CP_HW_FAULT),
            rd(gpu.base + CP_INTERRUPT_STATUS), rd(gpu.base + CP_PROTECT_STATUS));
        printf("adreno: gpu:   in the first command buffer at %#llx, %u words to go; in the second at %#llx, %u to go\n",
            (uint64_t)rd(gpu.base + CP_IB1_BASE + 4) << 32 | rd(gpu.base + CP_IB1_BASE), rd(gpu.base + CP_IB1_REM_SIZE),
            (uint64_t)rd(gpu.base + CP_IB2_BASE + 4) << 32 | rd(gpu.base + CP_IB2_BASE), rd(gpu.base + CP_IB2_REM_SIZE));
        step = 0;
        if (space_faulted(&core_space)) {
                wr(smmu.base + context + SMMU_CB_FSR, SMMU_FSR_ALL);    /* so that the next one shows */
        }
}

/*
 * boot-arg gpu_recover=1: the 3D core is put back where step 20 and step 21 left it, and
 * everything it had been given is given up, so that the next submission is its own again.
 * Returns whether that worked; without the boot-arg, false at once and nothing is touched.
 *
 * Why it is worth doing: one submission the core never finishes otherwise costs the rest of the
 * run. gpuWait() (iokit/Kernel/RedwoodGPU.cpp) stops waiting for good after the first such
 * submission, and from then on every wait says at once that the GPU is not done -- so every
 * later submission "succeeds" and writes nothing, which reads like a measurement and is not
 * one. With this, a core that hangs costs one submission.
 *
 * What is done, and who says so (Linux, drivers/gpu/drm/msm/adreno/a6xx_gpu.c unless said
 * otherwise):
 *
 *   - the command processor is halted first, so that nothing more of the ring is fetched while
 *     the rest of this happens (a6xx_recover: "Halt SQE first", cp_sqe_cntl = 3; step 20 starts
 *     it with 1, and bit 0 is what runs it);
 *   - the GMU is told to keep the core powered whatever it makes of what follows (Linux sets
 *     the same word when its hang interrupt is about to look at a core that has stopped:
 *     a6xx_fault_detect_irq. Nothing has set it here, there being no interrupt of ours);
 *   - what the core has under way on the bus is let finish and nothing new is taken
 *     (a6xx_bus_clear_pending_transactions with gx_off, which a6xx_recover calls before it
 *     pulls the reset line: core_bus_halt(true) here, which also halts the core's own side);
 *   - the reset line of the core's own half (GX) is pulled and let go (a6xx_gpu_sw_reset, whose
 *     timings and read in between these are: the line is held for 100 us). Everything that was
 *     wedged is in that half -- the shader cores, the depth and colour pipeline, the caches,
 *     the command processor. The CX half is not reset, so the SMMU keeps its tables and the
 *     core's memory stays where it was mapped; nothing of ours has to be made again;
 *   - the ring is put back to where the core says it reads from, with nothing to read;
 *   - and the core is started again exactly as the steps start it: step 20's registers, its
 *     command processor, "initialize"; then step 21, out of secure mode and a word written to
 *     memory on command, which is that step's own proof that the core does as it is told. The
 *     zap shader stays where TrustZone put it -- Linux does not load it again either
 *     (a6xx_zap_shader_init keeps a flag) and asks the core to leave secure mode after every
 *     reset just the same.
 *
 * Not Linux's, and the reason this is behind a boot-arg: Linux never lets go of that reset line
 * on a part whose GMU runs firmware of its own. a6xx_recover() does the reset only for the a6xx
 * parts whose "GMU" is a wrapper with no firmware; on a part like this one it takes the whole
 * CX half's power away instead and starts from nothing, because its GMU is set up afresh every
 * time the GPU is woken. The one place Linux resets GX on a part with a real GMU is
 * a6xx_gmu_force_off() (a6xx_gmu.c), and there it pulls the line and leaves it pulled -- the CX
 * half is about to lose power anyway. Here the GMU is set up once, by steps 15 to 17, and never
 * spoken to again, so there is nothing of its state that a submission depends on; but whether a
 * GMU that was told none of this goes on believing it owns a core that has just been reset is a
 * question the phone answers, not the sources. So: with the boot-arg left out, none of this
 * runs and a wedge costs the run, exactly as before.
 *
 * Steps 20 and 21 are called as they are rather than copied, so that there is one list of the
 * core's registers and not two. That costs their two cx_ready() calls, which measure the
 * register bus's clock (some 28 ms each) and leave `quiet` false, so the recovery says every
 * register it writes. Once per hang, that is evidence rather than noise.
 */
bool
redwood_gpu_recover(void)
{
        uint32_t fence_status;

        if (!gpu_kept || !gpu_recover) {
                return false;
        }
        if (gpu_recoveries >= GPU_RECOVERIES) {
                printf("adreno: gpu: the 3D core has been recovered %u times in this run already: no more\n", gpu_recoveries);
                return false;
        }
        gpu_recoveries++;
        step = 0;                               /* (the steps are over: no line of theirs waits for the loader any more) */
        newest_step = ~0u;

        wr(gpu.base + CP_SQE_CNTL, 3);
        wr(gmu.base + GMU_PWR_COL_KEEPALIVE, 1);
        core_bus_halt(true);
        wr(gpu.base + RBBM_SW_RESET_CMD, 1);
        rd(gpu.base + RBBM_SW_RESET_CMD);       /* (Linux reads it back and waits, to be sure of the order) */
        sleep_us(101);
        wr(gpu.base + RBBM_SW_RESET_CMD, 0);
        rd(gpu.base + RBBM_SW_RESET_CMD);
        sleep_us(1);
        /*
         * Whatever the GMU dropped of writes of ours while the core was wedged is forgotten, and its
         * fence is put back where it lets them through -- Linux does both before it resets a core
         * (a6xx_gmu_force_off, a6xx_gmu.c; nothing here has ever written the fence's control word, so
         * zero is what it already holds). A fence gone to dropping writes would swallow every
         * register step 20 writes below, silently, and the failure would then read as a core that
         * does not start rather than as writes that never arrived. Hence also the status word in the
         * line below: its bits are exactly those writes.
         */
        fence_status = rd(gmu.base + GMU_AHB_FENCE_STATUS);
        wr(gmu.base + GMU_AHB_FENCE_STATUS_CLR, 7);
        wr(gmu.base + GMU_AO_AHB_FENCE_CTRL, 0);
        /*
         * Where the ring stands is the core's word for it: a position left over from before would
         * have it fetch words it has run already, or words of a submission that is being given up.
         * The two positions are made equal, whatever it says, so there is nothing to fetch until
         * something is submitted; step 20 then writes the ring's address and size again itself.
         */
        ring_at = ring_sent = rd(gpu.base + CP_RB_RPTR) % RING_WORDS;
        wr(gpu.base + CP_RB_WPTR, ring_at);
        wr(gpu.base + RBBM_INT_CLEAR_CMD, rd(gpu.base + RBBM_INT_0_STATUS));    /* so that what is reported next is this core's */
        printf("adreno: gpu: the 3D core's half has been reset: status %#x, interrupts %#x, ring at %u; the GMU sees it busy: "
            "%#x, and powered: %#x (bits 6 and 7 clear); it had dropped writes of ours: %#x\n", rd(gpu.base + RBBM_STATUS),
            rd(gpu.base + RBBM_INT_0_STATUS), ring_at, rd(gmu.base + GMU_AO_GPU_CX_BUSY_STATUS),
            rd(gmu.base + GMU_SPTPRAC_PWR_CLK_STATUS), fence_status);

        if (!step_core_start() || !step_core_commands()) {
                printf("adreno: gpu: THE 3D CORE DOES NOT TAKE COMMANDS AGAIN after the reset: it is given up for this run, as it "
                    "would have been without gpu_recover=1\n");
                wr(gmu.base + GMU_PWR_COL_KEEPALIVE, 0);
                quiet = true;
                return false;
        }
        wr(gmu.base + GMU_PWR_COL_KEEPALIVE, 0);
        quiet = true;                           /* (cx_ready() measures a clock, and measure_quietly() ends by clearing this) */

        /*
         * And everything the core had been given is given up: the word by which it says how far it
         * has got is set to the last submission's number, so that every wait outstanding ends. The
         * submission that hung, and whatever was behind it in the ring, did not run and now never
         * will; calling them done is what Linux does with the ring's fence when it recovers
         * (msm_gpu.c, recover_worker: "advance the fence by one more to clear the faulting
         * submit"), and it is the only answer that lets the waiters go. Their times go with them:
         * a submission that did not run is not counted among those the core worked on.
         */
        bzero((void *)core_scratch.memory, 4096);
        *(volatile uint32_t *)(core_scratch.memory + GPU_FENCE_AT) = gpu_fence;
        to_memory(core_scratch.memory, 4096);
        gpu_timed = gpu_fence;
        printf("adreno: gpu: THE 3D CORE IS RECOVERED (%u time%s in this run); submissions up to %u are given up as done\n",
            gpu_recoveries, gpu_recoveries == 1 ? "" : "s", gpu_fence);
        return true;
}

/* ---- all of it together ---------------------------------------------------------------------------- */

static bool(*const steps[])(void) = {
        step_outside, step_fuses, step_gpucc, step_cx_power, step_clocks, step_first_words, step_rscc, step_smmu,
        step_smmu_context, step_gmu_memory, step_trustzone, step_zap_signature, step_gmu_space, step_gmu_firmware,
        step_gmu_start, step_gmu_queues, step_core_power, step_core_space, step_zap_load, step_core_start, step_core_commands,
        step_frame,
};
#define STEPS                   ((uint32_t)(sizeof(steps) / sizeof(steps[0])))

/* ---- beside the steps: how warm the GPU is ------------------------------------------------------------------ */

/*
 * The SoC's temperature sensors report by themselves, in a block that is always on (dt: tsens1,
 * whose readings are at 0xc265000; Linux's tsens-v2.c): a status word a sensor, the last reading
 * in its low 12 bits, signed, in tenths of a degree, and bit 21 when there is one. Sensors 1 and
 * 2 sit at the GPU (dt: gpuss-0, gpuss-1). Read, never written: once a minute while the GPU is
 * kept, for the line beside the kernel's line about memory (osfmk/arm64/loader_return.c). A
 * sensor caught between two readings has none for a moment and is asked again; one that still
 * has none is put down as -273.0.
 */
static struct block tsens = { "tsens1", 0x0c265000, 0x200 };    /* sm8150's tsens1 (GPU), as the redwood's was: a read, for the once-a-minute line */
#define TSENS_STATUS(sensor)    (0xa0 + 4 * (sensor))
#define TSENS_VALID             (1u << 21)

void
redwood_adreno_minute(void)
{
        int32_t tenths[2];

        if (!gpu_kept) {
                return;
        }
        need(&tsens);
        for (unsigned i = 0; i < 2; i++) {
                tenths[i] = -2730;
                for (unsigned again = 0; again < 16 && tenths[i] == -2730; again++) {
                        uint32_t status = rd(tsens.base + TSENS_STATUS(1 + i));

                        if (status & TSENS_VALID) {
                                tenths[i] = (int32_t)(status << 20) >> 20;
                        }
                }
        }
        printf("adreno: the GPU's sensors read %d.%d and %d.%d degrees; its clock is set to %u MHz\n", tenths[0] / 10,
            tenths[0] % 10 < 0 ? -(tenths[0] % 10) : tenths[0] % 10, tenths[1] / 10,
            tenths[1] % 10 < 0 ? -(tenths[1] % 10) : tenths[1] % 10, core_khz / 1000);
}

/* ---- beside the steps: what the firmware left the display doing (boot-arg display_probe=1) ---------------- */

/*
 * The panel is a DSI video panel (dt: samsung,ams639rq08 (raphael's AMOLED): 1080 x 2340 at 60
 * frames a second, 2432 lines with the porches; or at 60 with a longer vertical front porch):
 * the display controller sends it the framebuffer over and over at the rate of its timing
 * registers, which nobody has touched since the firmware. This reads them. Each read is behind
 * a check that what it reads has its clock: the bus clock of the display's clock controller (by
 * GCC, which answers always; the firmware leaves it off, Linux keeps it on always, and so it is
 * turned on here: one write), in that controller the display block's power switch and the bus
 * clock of its registers (the firmware set the panel going and took that clock away again; it
 * is turned on here: the other write, and the last), then the timing of the interface the panel
 * hangs on (Linux's dpu_hw_intf.c has the registers' meaning) and its two counters, read twice a
 * known time apart: how many frames a second the panel is really sent. Neither clock is turned
 * off again. Every access is announced and waited for (adreno_pause), as in the newest step.
 *
 * Seen: 476 clocks a line (360 of them picture: the panel takes it compressed three to one),
 * 2432 lines a frame of which 2400 are shown, and 119.93 frames a second: the panel runs in
 * its 120 Hz mode, whatever is put into the framebuffer 60 times a second. The two counters
 * count although the register that enables them reads 0; the line counter goes from 0 to 2431,
 * 429 lines in 1.47 ms. (The first clock is found off again by the next kernel.)
 */
static struct block dispcc = { "dispcc", 0x0af00000, 0x2000 };          /* dt: clock-controller@af00000 */
static struct block disp_intf = { "intf1", 0x0ae36000, 0x300 };         /* dt: mdss_dsi0's mdp_intf_base */
#define GCC_DISP_AHB_CBCR       0x27004
#define DISPCC_MDSS_CORE_GDSCR  0x1004          /* dt: disp_cc_mdss_core_gdsc */
#define DISPCC_MDSS_PCLK0_CBCR  0x1010
#define DISPCC_MDSS_MDP_CBCR    0x1014
#define DISPCC_MDSS_AHB_CBCR    0x1050
#define INTF_TIMING_ENGINE_EN   0x000
#define INTF_HSYNC_CTL          0x008           /* clocks a line << 16 | of which the pulse */
#define INTF_VSYNC_PERIOD       0x00c           /* clocks a frame */
#define INTF_DISPLAY_V_START    0x01c           /* clocks before the first line shown, and */
#define INTF_DISPLAY_V_END      0x024           /* ... up to the last */
#define INTF_DISPLAY_HCTL       0x03c
#define INTF_FRAME_LINE_COUNT_EN 0x0a8
#define INTF_FRAME_COUNT        0x0ac
#define INTF_LINE_COUNT         0x0b0

static void
display_probe(void)
{
        uint32_t saved_fine = fine, line, frame, shown, counting, frames[2], lines[2], bus;
        uint64_t times[2];

        step = newest_step = 0;
        fine = 1;
        announce("the display: has its clock controller its bus clock (gcc 0x127004; GCC answers always)");
        if (get(&gcc, "disp_ahb_cbcr", GCC_DISP_AHB_CBCR) & CBCR_OFF) {
                announce("turn the bus clock of the display's clock controller on (gcc 0x127004 bit 0: THE ONE WRITE)");
                put(&gcc, "disp_ahb_cbcr", GCC_DISP_AHB_CBCR, rd(gcc.base + GCC_DISP_AHB_CBCR) | CBCR_ENABLE);
                sleep_ms(10);
        }
        /*
         * Turned on, the register still says the clock is off (seen: 0x8000000b). Its bit 1 is
         * set as the firmware left it: the clock is gated by the hardware itself while nobody
         * talks to the block, and that is what "off" then means. Linux turns the clock on the same
         * way, never looks at this bit and reads the clock controller next; so does this, if the
         * clock is at least asked for.
         */
        if (!(get(&gcc, "disp_ahb_cbcr", GCC_DISP_AHB_CBCR) & CBCR_ENABLE)) {
                printf("adreno: 0: the display's clock controller has no bus clock, and does not get one: nothing more is read\n");
                goto out;
        }
        announce("the display block's power switch and clocks, in its clock controller (0xaf00000: NOT READ BEFORE)");
        get(&dispcc, "mdss_mdp_cbcr", DISPCC_MDSS_MDP_CBCR);
        get(&dispcc, "mdss_pclk0_cbcr", DISPCC_MDSS_PCLK0_CBCR);
        if (!(get(&dispcc, "mdss_core_gdscr", DISPCC_MDSS_CORE_GDSCR) & GDSC_POWER_ON)) {
                printf("adreno: 0: the display block is without power: nothing more is read\n");
                goto out;
        }
        bus = get(&dispcc, "mdss_ahb_cbcr", DISPCC_MDSS_AHB_CBCR);
        if (bus & CBCR_OFF) {
                /* (seen: the power switch on, the display's own clock and the pixel clock running, this one off: 0x80000000) */
                announce("turn the bus clock of the display block's registers on (dispcc 0xaf01050 bit 0: THE ONE WRITE THERE)");
                put(&dispcc, "mdss_ahb_cbcr", DISPCC_MDSS_AHB_CBCR, bus | CBCR_ENABLE);
                sleep_ms(10);
                bus = get(&dispcc, "mdss_ahb_cbcr", DISPCC_MDSS_AHB_CBCR);
        }
        if (bus & CBCR_OFF) {
                /* (Linux waits for this bit to go, and gives up if it does not: there is no clock to read the block with) */
                printf("adreno: 0: the bus clock of the display block's registers does not run (%#x): nothing more is read\n", bus);
                goto out;
        }
        announce("the timing of the interface the panel hangs on (0xae36000: NOT READ BEFORE)");
        get(&disp_intf, "timing_engine_en", INTF_TIMING_ENGINE_EN);
        line = get(&disp_intf, "hsync_ctl", INTF_HSYNC_CTL) >> 16;
        frame = get(&disp_intf, "vsync_period", INTF_VSYNC_PERIOD);
        shown = get(&disp_intf, "display_v_end", INTF_DISPLAY_V_END) - get(&disp_intf, "display_v_start", INTF_DISPLAY_V_START);
        get(&disp_intf, "display_hctl", INTF_DISPLAY_HCTL);
        counting = get(&disp_intf, "frame_line_count_en", INTF_FRAME_LINE_COUNT_EN);
        frames[0] = get(&disp_intf, "frame_count", INTF_FRAME_COUNT);
        lines[0] = get(&disp_intf, "line_count", INTF_LINE_COUNT);
        /* the two counters together, twice, a second apart by our own clock */
        quiet = true;
        for (int i = 0; i < 2; i++) {
                frames[i] = rd(disp_intf.base + INTF_FRAME_COUNT);
                lines[i] = rd(disp_intf.base + INTF_LINE_COUNT);
                absolutetime_to_nanoseconds(mach_absolute_time(), &times[i]);
                if (rd(disp_intf.base + INTF_FRAME_COUNT) != frames[i]) {
                        i--;                            /* (a frame began between the two reads: once more) */
                        continue;
                }
                if (i == 0) {
                        sleep_ms(1000);
                }
        }
        quiet = false;
        if (line != 0) {
                printf("adreno: 0: THE DISPLAY: %u lines a frame, of which %u shown, %u clocks a line (the device tree's timing "
                    "for 120 frames a second has 2432 lines; for 60, about twice that)\n", frame / line, (shown + 1) / line, line);
        }
        printf("adreno: 0: the interface's counters (frame_line_count_en %#x): frame %u line %u, then frame %u line %u %llu us "
            "later\n", counting, frames[0], lines[0], frames[1], lines[1], (times[1] - times[0]) / 1000);
        if (line != 0 && frame >= line && frames[1] != frames[0] && times[1] > times[0]) {
                /* in thousandths of a frame a second: the frames gone by, and the part of one that the lines say */
                uint64_t per_frame = frame / line;
                uint64_t gone = (uint64_t)(frames[1] - frames[0]) * per_frame + lines[1] - lines[0];
                uint64_t rate = gone * 1000000000ull / per_frame * 1000 / (times[1] - times[0]);

                printf("adreno: 0: THE PANEL IS SENT %llu.%03llu FRAMES A SECOND (%llu lines in that time)\n", rate / 1000, rate % 1000,
                    gone);
        }
        /* where the scan is, read eight times a millisecond and a bit apart: how fast it goes down the panel */
        for (int i = 0; i < 8; i++) {
                uint64_t nanos;

                absolutetime_to_nanoseconds(mach_absolute_time(), &nanos);
                printf("adreno: 0: the scan: at %llu us line %u of frame %u\n", nanos / 1000 % 1000000, rd(disp_intf.base + INTF_LINE_COUNT),
                    rd(disp_intf.base + INTF_FRAME_COUNT));
                sleep_us(1300);
        }
out:
        fine = saved_fine;
        newest_step = 0;
}

/*
 * Boot-arg gpu=1: is this the phone, and is its GPU's firmware in the small image? Then the
 * GPU is started and kept for the system to draw with, which is what
 * "adreno=21 adreno_skip=0x1c00 adreno_pause=0 adreno_keep=1" asks for at length: the steps
 * that only look (10 to 12) left out, no pauses, no watch. Anywhere else -- QEMU, whose machine
 * has its peripherals elsewhere -- not one register of the above is touched.
 */
#define REDWOOD_SOC_BASE        0x00400000ul    /* where the loader says the phone's peripherals begin (its "redwood") */

extern vm_offset_t pe_arm_get_soc_base_phys(void);

static bool
start_by_itself(void)
{
        static const char *const files[] = { "a640_gmu.bin", "a630_sqe.fw", "a640_zap.mbn" };
        const char *missing = NULL;
        uint64_t size;

        if (pe_arm_get_soc_base_phys() != REDWOOD_SOC_BASE) {
                printf("adreno: THE GPU IS NOT STARTED: gpu=1 is for the phone, and this is not it\n");
                return false;
        }
        image_asked_quietly = true;
        for (unsigned i = 0; i < sizeof(files) / sizeof(files[0]) && missing == NULL; i++) {
                if (image_file("fw", files[i], &size) == NULL) {
                        missing = files[i];
                }
        }
        image_asked_quietly = false;
        if (missing != NULL) {
                printf("adreno: THE GPU IS NOT STARTED: the small image has no fw/%s (the phone's /vendor/firmware/%s goes into "
                    "fw-gpu/); the system draws with its software renderer\n", missing, missing);
                return false;
        }
        return true;
}

/*
 * Called while the system starts, before there is a root file system, and the start waits: for
 * as long as the steps and the watch after them last, nothing else writes to the log, so that
 * its end -- all that the loader's notes keep -- is about what is being done here.
 */
void
redwood_adreno_init(void)
{
        static const char *const what[] = {
                "", "the clocks outside", "the fuses", "the clock controller", "the CX half's power", "the clocks", "the first words",
                "the GMU's RPMh sequencer", "the SMMU", "an SMMU context", "the GMU's memory", "TrustZone", "the zap shader's header",
                "the GMU's address space", "the GMU's firmware", "the GMU's start", "the GMU's message queues", "the 3D core's power",
                "the 3D core's address space", "the zap shader", "the 3D core's start", "the 3D core's first commands", "a frame"
        };
        uint32_t only = 0, done = 0, failed = 0, keep = 0;
        bool by_itself = false;

        if (PE_parse_boot_argn("display_probe", &only, sizeof(only)) && only) {
                display_probe();
        }
        if (!PE_parse_boot_argn("adreno", &last_step, sizeof(last_step))) {
                if (!(PE_parse_boot_argn("gpu", &only, sizeof(only)) && only) || !start_by_itself()) {
                        return;
                }
                by_itself = true;
                last_step = 21;
                skip_steps = 1u << 10 | 1u << 11 | 1u << 12;
                pause_ms = 0;
                watch_seconds = 0;
                keep = 1;
        }
        PE_parse_boot_argn("adreno_skip", &skip_steps, sizeof(skip_steps));
        if (PE_parse_boot_argn("adreno_only", &only, sizeof(only)) && only > 0 && only <= STEPS) {
                last_step = only;
                skip_steps = ~(1u << only);
        }
        PE_parse_boot_argn("adreno_pause", &pause_ms, sizeof(pause_ms));
        PE_parse_boot_argn("adreno_fine", &fine, sizeof(fine));
        if (last_step == 0) {
                return;                                 /* (adreno=0: the GPU is left alone) */
        }
        if (last_step > STEPS) {
                last_step = STEPS;
        }
        PE_parse_boot_argn("adreno_watch", &watch_seconds, sizeof(watch_seconds));
        for (newest_step = last_step; newest_step > 0 && (skip_steps & 1u << newest_step); newest_step--) {
        }

        printf("adreno: steps up to %u, without those in mask %#x; step %u is the new one and waits %u ms %s\n", last_step,
            skip_steps, newest_step, pause_ms, fine ? "before every access" : "after each announcement");
        /*
         * R27: the flush channel's own test, before any step runs.  The loader writes the
         * whole log (its side and this ring) to the logdump partition and answers with the
         * ack; the run that follows leaves this line as the partition's FIRST kernel-side
         * record.  A fetched partition that holds it says the channel works end to end;
         * one that does not (and the "did not put the log away in time" line below it is
         * absent too, meaning the flush was not even asked for) says the channel itself
         * is what R26 never proved.
         */
        put_the_log_away("step 1's first line: the flush channel's own test (R27)");
        for (step = 1; step <= last_step; step++) {
                if (skip_steps & 1u << step) {
                        continue;
                }
                /*
                 * R27: the steps past 19 had no guard of their own -- a death in them
                 * (the two-parameter run died at 26 s, with step 19 refused and the run
                 * gone on) left the partition holding only the 2-second notes' 960-byte
                 * tail.  One flush before the three steps that write to the 3D core
                 * itself: its registers (20), its command processor (21), the frame (22).
                 */
                if (step == 20) {
                        put_the_log_away("step 20 entered: the 3D core's registers are next (R27)");
                } else if (step == 21) {
                        put_the_log_away("step 21 entered: out of secure mode and the first command are next (R27)");
                } else if (step == 22) {
                        put_the_log_away("step 22 entered: the first frame is next (R27)");
                }
                if (!steps[step - 1]()) {
                        printf("adreno: step %u: FAILED; the steps after it are not attempted\n", step);
                        failed = step;
                        break;
                }
                printf("adreno: step %u: done\n", step);
                done = step;
        }
        /*
         * R27: the steps are over -- whatever the system does from here (the 26-second
         * death of the two-parameter run sat on this side of the refusal, with the run
         * gone on past step 19), the storage holds the whole record up to this point
         * before the next thing happens.  (The loader's 2-second notes keep refreshing
         * it for as long as the run lasts.)
         */
        put_the_log_away("the steps are over (R27): what follows is the system's own doing");
        PE_parse_boot_argn("adreno_keep", &keep, sizeof(keep));
        if (keep) {
                if (done >= 21 && core_runs) {
                        gpu_free[0].start = GPU_ADDRESSES_START;
                        gpu_free[0].size = GPU_ADDRESSES_SIZE;
                        gpu_free_count = 1;
                        bzero((void *)core_scratch.memory, 4096);
                        to_memory(core_scratch.memory, 4096);
                        PE_parse_boot_argn("gpu_exact", &gpu_exact, sizeof(gpu_exact));
                        PE_parse_boot_argn("gpu_log", &gpu_log, sizeof(gpu_log));
                        PE_parse_boot_argn("gpu_recover", &gpu_recover, sizeof(gpu_recover));
                        /* before any client exists: gpu_address_span() must not change under a live buffer */
                        PE_parse_boot_argn("gpu_va", &gpu_va, sizeof(gpu_va));
                        gpu_kept = true;
                        quiet = true;
                        printf("adreno: THE GPU STAYS ON for the system to draw with: the service RedwoodGPU\n");
                        if (gpu_va != 0) {
                                printf("adreno: the 3D core's addresses: %#llx..%#llx are the kernel's to give, "
                                    "%llu KB a time%s (gpu_va=%u)\n", GPU_ADDRESSES_START,
                                    GPU_ADDRESSES_START + GPU_ADDRESSES_SIZE - 1, GPU_ADDRESS_GRANULE >> 10,
                                    gpu_va >= 2 ? ", with that much again left unmapped after every buffer" : "",
                                    gpu_va);
                        }
                        RedwoodGPUStart();
                        return;
                }
                if (!by_itself) {
                        printf("adreno: not kept: the steps did not get as far as a 3D core that takes commands\n");
                }
        }
        if (by_itself) {
                /* (one line for whoever wonders why the picture is drawn slowly; the steps' own lines are above it) */
                printf("adreno: THE GPU IS NOT STARTED: step %u (%s) failed; the system draws with its software renderer\n",
                    failed != 0 ? failed : 21, what[failed != 0 && failed <= STEPS ? failed : 21]);
        }
        /*
         * Punishment for a write may come late (a reset some 17 seconds after it has been seen):
         * keep the end of the log saying where things stand.
         */
        for (uint32_t second = 1; second <= watch_seconds; second++) {
                sleep_ms(1000);
                printf("adreno: alive %u s after step %u; %s\n", second, done, last_write);
        }
        gpu_block_stop();
        if (PE_parse_boot_argn("adreno_return", &only, sizeof(only)) && only) {
                printf("adreno: over; back to the loader (adreno_return)\n");
                loader_return();
        }
        printf("adreno: over; the system goes on starting\n");
}
