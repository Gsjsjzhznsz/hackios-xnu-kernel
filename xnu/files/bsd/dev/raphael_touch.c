/*
 * The touch screen of raphael (Mi 9 / K20 Pro, SM8150): a Goodix GT9886 ("normandy") at
 * 0x5d on the I2C engine SE19 (qup i2c@c88000 = QUPV3_WRAP2_S2), as a source of touch
 * records for /dev/redwood-touch (sys/redwood_input.h). Only with boot-arg touchscreen=1.
 *
 * This is a port of the SWEET track's driver (ios-raphael, bsd/dev/sweet_touch.c), whose
 * bring-up sequence was verified on this very phone: session t13 powered the IC up over
 * SE19 and read reports from it for thousands of log lines. The hardware sequence below
 * is that sequence, unchanged; what differs is where the reports go (redwood_touch_post
 * instead of the SWEET HID hook) and the log lines' prefix.
 *
 * There is no clock, pin or regulator framework in this kernel, so the driver does all of
 * it itself, in the order the vendor's driver does (goodix_ts_i2c.c):
 *   power    the IC's 3v3 and 1v8 rails are TLMM GPIOs of the SoC (98 and 100), switched
 *            directly -- no RPMh vote, per raphael-sm8150.dtsi (TP_3P3_EN / TP_1P8_EN)
 *   pins     reset GPIO54, IRQ input GPIO122, SDA/SCL muxed to qup19 on GPIO57/58
 *            (all South-tile TLMM; the window starts at 0x03000000 + 0x00D00000)
 *   clocks   the QUP wrap-2 AHB and the SE itself, voted in the global clock controller
 *            (GCC_QUPV3_WRAP_2_S_?HB_CLK and _S2_CLK), the RCG set to XO/1 (19.2 MHz)
 *   engine   the serial engine ("GENI") in FIFO I2C mode, polled -- the firmware ABL
 *            left in it is checked (SE_FW protocol 3) and the packing registers are
 *            programmed as Linux's geni_serial does for 8-bit I2C
 *   chip      identify ("9886" at 0x4535), pick the panel config by the sensor id
 *            (0x4541), keep the IC out of doze (0x30f0/0x3100), download the 784-byte
 *            panel config (the 0x80/0x82/0x83 command handshake at 0x6f68/0x6f78 -- the
 *            vendor's goodix_gt9886_cfg_f11.bin, verbatim in gt9886_cfg.h), then poll
 *            the frame register 0x4100 ten times a second (boot-arg touch_poll_us=N:
 *            every N microseconds instead)
 * The frame at 0x4100: b[0] event flags (bit7 touch, bit6 request), b[1] bit4 have_key +
 * bits3:0 count, then 8 bytes per point (id, x lo, x hi, y lo, y hi, w, rsvd, area),
 * little-endian; the whole 4+8n frame sums to zero mod 256. Ack by writing one zero byte
 * to 0x4100.
 *
 * Boot-args: touchscreen=1 to enable (this driver; the redwood Goodix SPI driver it
 * replaces took the same argument); touchdelay=S to wait S seconds before the first
 * hardware access (default 5; the sequence is proven, but the wait lets launchd get
 * going first); touchflip=1 to rotate both axes 180 degrees on top of the per-sensor
 * flip the panel already selects (raphael-sm8150.dtsi: sensor0 x2x/y2y); touch_poll_us.
 *
 * Register layouts: Linux drivers/i2c/busses/i2c-geni-qcom.c, drivers/soc/qcom/qcom-geni-se.c,
 * drivers/clk/qcom/gcc-sm8150.c, drivers/pinctrl/qcom/pinctrl-sm8150.c,
 * drivers/input/touchscreen/goodix_gt9886 (vendor), and the phone's own device tree.
 */
#include <kern/clock.h>
#include <kern/sched_prim.h>
#include <kern/thread.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>

/*
 * Every line goes to the loader's log directly as well: with the USB console gone the
 * kernel's own messages reach that log only at the end of a run, and a line that says
 * which step the bring-up died on is the whole point of the steps.
 */
extern void redwood_tl(const char *format, ...) __printflike(1, 2);

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
                redwood_tl("%c", *c);
        }
        return printf("%s", line);
}
#define printf say

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern kern_return_t thread_terminate(thread_t thread);

/* Panel configs extracted from the raphael vendor blob (goodix_gt9886_cfg_f11.bin,
 * ihex): two 784-byte packages for the two panel suppliers, selected by the IC's
 * sensor-id register 0x4541 (mask 0x0f). */
#include "gt9886_cfg.h"

/* SM8150: TLMM base 0x03000000 is split into per-tile blocks; a GPIO register address =
 * tile base + 0x1000 * global GPIO id (pinctrl-sm8150.c: SOUTH 0x00D00000, NORTH
 * 0x00900000, EAST 0x00500000, WEST 0x00100000). Every touch GPIO (54/57/58/98/100/122)
 * is South-tile, so the GPIO window starts at 0x03000000 + 0x00D00000 = 0x03D00000.
 * Touch is SE19 @ 0xc88000 (GCC_QUPV3_WRAP2_S2: vote BIT(6)@0x52014, RCG 0x1e3a8).
 * Power via TLMM GPIOs per raphael-sm8150.dtsi: vdd 3v3 = GPIO98, avdd 1v8 = GPIO100,
 * reset = GPIO54, IRQ = GPIO122, SDA/SCL = GPIO57/58 (qup19). */
#define TOUCH_SE_PA      0x00c88000u
#define TOUCH_GCC_PA     0x00100000u
/* 0x80000 covers global ids 0..127: gpio122 IN_OUT ends at 0x7a004. */
#define TOUCH_GPIO_PA    0x03D00000u
#define TOUCH_GCC_VOTE   0x52014u
#define TOUCH_RCG       0x1e3a8u    /* wrap2_s2 cmd_rcgr (SE19) */
#define TOUCH_VOTE_MASK ((1u << 1) | (1u << 2) | (1u << 6)) /* s_ahb|m_ahb|s2 */
#define TOUCH_GPIO_RESET 54u
#define TOUCH_GPIO_IRQ   122u
#define TOUCH_GPIO_VDD   98u         /* 3v3 rail enable, output high */
#define TOUCH_GPIO_AVDD  100u        /* 1v8 rail enable, output high */
#define TOUCH_GPIO_SDA   57u
#define TOUCH_GPIO_SCL   58u
#define TOUCH_MAX_Y      2339u
#define TOUCH_GPIO_CFG(n) ((uint32_t)(n) * 0x1000u)
#define TOUCH_GPIO_IO(n)  (TOUCH_GPIO_CFG(n) + 4u)
#define TOUCH_MAX        10u

/* GT9886 "normandy" (raphael GT9886/9889 family, goodix_driver_gt9886). The register map
 * is NOT the classic GT9xx 0x81xx map: every address below is taken from the raphael
 * cfg-blob reg table (goodix_gt9886_cfg_f11.bin) which the vendor driver programs
 * verbatim (goodix_ts_i2c.c). The IC also requires its 784-byte panel config download
 * on every boot -- without it the firmware never posts touch frames. Frame at 0x4100:
 * b[0] event flags (bit7 touch, bit6 request, bit5 gesture), b[1] bit4 have_key +
 * bits3:0 count, then 8 bytes per point (id, x lo, x hi, y lo, y hi, w, rsvd, area),
 * little-endian; the whole 4+8n frame sums to zero mod 256. Ack by writing one zero
 * byte to 0x4100. */
#define GT9886_PID_REG     0x4535u  /* 4-byte ASCII product id, expect "9886" */
#define GT9886_SENSOR_REG  0x4541u  /* panel supplier id, mask 0x0f */
#define GT9886_VER_BASE    0x452cu  /* 72-byte version block, sums to zero */
#define GT9886_VER_LEN     72u
#define GT9886_CMD_REG     0x6f68u  /* 3-byte command channel */
#define GT9886_CFG_REG     0x6f78u  /* config window */
#define GT9886_COOR_REG    0x4100u  /* touch frames */
#define GT9886_POINT_LEN   8u
#define GT9886_TOUCH_EV    0x80u
#define GT9886_REQUEST_EV  0x40u
#define GT9886_DOZE_CTRL   0x30f0u  /* 0xAA = keep awake, 0xCC = may doze */
#define GT9886_DOZE_STAT   0x3100u  /* 0xBB = confirmed awake */
#define GT9886_CMD_READY   0xffu
#define GT9886_CMD_START   0x80u
#define GT9886_CMD_PREPARE 0x82u
#define GT9886_CMD_END     0x83u

/* the serial engine (GENI SE19), FIFO I2C */
#define SE_STATUS       0x040u
#define SE_MCLK         0x048u
#define SE_FW           0x068u
#define SE_CLK_SEL      0x07cu
#define SE_DMA          0x258u
#define SE_TX_LEN       0x26cu
#define SE_RX_LEN       0x270u
#define SE_SCL          0x278u
#define SE_CMD          0x600u
#define SE_CTRL         0x604u
#define SE_IRQ          0x610u
#define SE_IRQ_EN       0x614u
#define SE_IRQ_CLR      0x618u
#define SE_TX           0x700u
#define SE_RX           0x780u
#define SE_TX_COUNT     0x800u
#define SE_RX_COUNT     0x804u
#define SE_HW_TX        0xe24u
#define SE_HW_RX        0xe28u
#define SE_TX_WM        0x80cu
#define SE_RX_WM_IRQ    (1u << 26)
#define SE_RX_LAST      (1u << 27)
#define SE_TX_WM_IRQ    (1u << 30)
#define SE_DONE         (1u << 0)
#define SE_NACK         (1u << 10)
#define SE_ERRORS       (0x3eu | (1u << 9) | (1u << 10) | (1u << 12) | \
                         (1u << 13) | (3u << 24) | (3u << 28))
#define SE_EVENTS       (SE_DONE | SE_ERRORS | SE_RX_WM_IRQ | SE_RX_LAST | SE_TX_WM_IRQ)
/* geni_se_config_packing(8, 4, true, true, true): start bits 7/15/23/31,
 * MSB-to-LSB, length 8, STOP only on vector 3. FIFO bytes remain LSB first. */
#define SE_PACK0        0x0007f8feu
#define SE_PACK1        0x000ffefeu

static struct {
        vm_offset_t se, gcc, gpio;
        bool started, dead, probing, flip;
        int kind, irq_hint;
        uint32_t irq_status, irq_last, active;
        unsigned sent, got;
        int x[TOUCH_MAX], y[TOUCH_MAX];
} st;
static int touch_sleep_chan;

static uint32_t
touch_rd(vm_offset_t base, uint32_t off)
{
        uint32_t v = *(volatile uint32_t *)(base + off);
        __asm__ volatile("dmb oshld" ::: "memory");
        return v;
}

static void
touch_wr(vm_offset_t base, uint32_t off, uint32_t val)
{
        __asm__ volatile("dmb oshst" ::: "memory");
        *(volatile uint32_t *)(base + off) = val;
        __asm__ volatile("dsb osh" ::: "memory");
}

static void
touch_wait(unsigned ms)
{
        uint64_t deadline;

        /* a real sleep, not delay(): with cpus=1 a busy wait would starve launchd */
        clock_interval_to_deadline(ms, NSEC_PER_MSEC, &deadline);
        (void)assert_wait_deadline(&touch_sleep_chan, THREAD_UNINT, deadline);
        thread_block(THREAD_CONTINUE_NULL);
}

static bool
touch_wait_clear(vm_offset_t base, uint32_t off, uint32_t mask)
{
        for (unsigned i = 0; i < 5000; ++i) {
                if (!(touch_rd(base, off) & mask)) {
                        return true;
                }
                delay_for_interval(1, NSEC_PER_USEC);
        }
        return false;
}

static void
touch_gpio(unsigned pin, unsigned mux, unsigned pull, unsigned drive, bool out, bool high)
{
        uint32_t off = TOUCH_GPIO_CFG(pin);
        uint32_t cfg = touch_rd(st.gpio, off);

        if (out) {
                /* Set the desired latch before enabling output (reset is active low). */
                touch_wr(st.gpio, TOUCH_GPIO_IO(pin),
                    (touch_rd(st.gpio, TOUCH_GPIO_IO(pin)) & ~2u) | (high ? 2u : 0u));
        }
        cfg &= ~0x3ffu;
        cfg |= (mux << 2) | pull | (drive << 6) | (out ? 0x200u : 0u);
        touch_wr(st.gpio, off, cfg);
}

static bool
touch_clocks(void)
{
        /* Vote on only the QUP wrapper and the touch SE, preserving other users. */
        uint32_t mask = TOUCH_VOTE_MASK;

        touch_wr(st.gcc, TOUCH_GCC_VOTE, touch_rd(st.gcc, TOUCH_GCC_VOTE) | mask);
        {
                uint32_t dfs = touch_rd(st.gcc, TOUCH_RCG + 0x14u);
                uint32_t cfg = touch_rd(st.gcc, TOUCH_RCG + 4u);

                printf("RAPHAEL TOUCH: gcc vote=%#x rcg=%#x cfg=%#x dfs=%#x\n",
                    touch_rd(st.gcc, TOUCH_GCC_VOTE), touch_rd(st.gcc, TOUCH_RCG), cfg, dfs);
                if (dfs & 1u) {
                        /* SE CLK_SEL=0 selects DFS performance level 0. Do not alter tables. */
                        cfg = touch_rd(st.gcc, TOUCH_RCG + 0x1cu);
                        if ((cfg & 0x371fu) != 1u) {
                                printf("RAPHAEL TOUCH: DFS level0=%#x is not XO/1; need clock integration\n", cfg);
                                return false;
                        }
                } else {
                        /* XO parent 0, HID divider 1 => 19.2 MHz. No PLL or voltage change. */
                        cfg = (cfg & ~(0x371fu | (1u << 20))) | 1u;
                        touch_wr(st.gcc, TOUCH_RCG + 4u, cfg);
                        touch_wr(st.gcc, TOUCH_RCG, touch_rd(st.gcc, TOUCH_RCG) | 1u);
                        if (!touch_wait_clear(st.gcc, TOUCH_RCG, 1u)) {
                                printf("RAPHAEL TOUCH: RCG update timeout\n");
                                return false;
                        }
                }
        }
        /* wrap2 m_ahb / s_ahb / s2 (SE19) CBCRs */
        {
                const uint32_t branches[] = {0x1e004, 0x1e008, 0x1e3a4};

                for (unsigned i = 0; i < sizeof(branches) / sizeof(branches[0]); ++i) {
                        uint32_t val = touch_rd(st.gcc, branches[i]);
                        if (!(val & (1u << 31))) {
                                continue; /* running */
                        }
                        /* Linux clk_branch_wait() skips the halt check when the branch is
                         * in hardware clock-gating mode: with the HWCG bit set the clock
                         * auto-gates while idle and CLK_OFF reads 1 even though the branch
                         * is enabled and usable. wrap2 s_ahb (0x1e008) is such a clock --
                         * its healthy 0x80000002 used to abort bring-up silently (the
                         * SWEET track's t10 lesson, kept). */
                        if (val & (1u << 1)) { /* HWCG mode */
                                printf("RAPHAEL TOUCH: GCC branch %#x HW-gated (%#x); ok\n",
                                    branches[i], val);
                                continue;
                        }
                        if (!touch_wait_clear(st.gcc, branches[i], 1u << 31)) {
                                /* Never a hard stop: the SE firmware print below is the real
                                 * verdict on whether the engine is alive. */
                                printf("RAPHAEL TOUCH: GCC branch %#x still halted (%#x); continuing\n",
                                    branches[i], touch_rd(st.gcc, branches[i]));
                        }
                }
        }
        return true;
}

/* Called only for a transfer started by this driver, including a stretched TX. */
static void
touch_abort(void)
{
        touch_wr(st.se, SE_CTRL, 2); /* abort M command, release held bus */
        {
                bool done = false;
                for (unsigned i = 0; i < 5000; ++i) {
                        if (touch_rd(st.se, SE_IRQ) & (1u << 5)) {
                                done = true;
                                break;
                        }
                        delay_for_interval(1, NSEC_PER_USEC);
                }
                if (!done || !touch_wait_clear(st.se, SE_STATUS, 1u)) {
                        st.dead = true;
                        printf("RAPHAEL TOUCH: abort failed status=%#x irq=%#x; stopping\n",
                            touch_rd(st.se, SE_STATUS), touch_rd(st.se, SE_IRQ));
                }
        }
        /* Bound drain even if the hardware reports an impossible FIFO count. */
        for (unsigned i = 0; i < 64 && (touch_rd(st.se, SE_RX_COUNT) & 0x1ffffffu); ++i) {
                (void)touch_rd(st.se, SE_RX);
        }
        touch_wr(st.se, SE_IRQ_CLR, 0xffffffffu);
        if (touch_rd(st.se, SE_TX_COUNT) & 0xfffffffu) {
                st.dead = true;
                printf("RAPHAEL TOUCH: TX FIFO not empty after abort; stopping\n");
        }
}

/* Keep all observed bits for diagnostics, even if a watermark was cleared. */
static uint32_t
touch_irq(void)
{
        st.irq_last = touch_rd(st.se, SE_IRQ);
        st.irq_status |= st.irq_last;
        return st.irq_last;
}

/* One Linux-style FIFO message. TX up to 2+784 bytes (the GT9886 config download): the
 * FIFO is fed one word per TX-watermark hit, so any length streams correctly regardless
 * of FIFO depth. RX at most 90. */
static int
touch_msg_run(uint8_t addr, const uint8_t *tx, unsigned nt, uint8_t *rx,
    unsigned nr, bool stretch)
{
        if (st.dead || nt > 800 || nr > 90 || (nt && nr) || (!nt && !nr)) {
                return EIO;
        }
        if (!touch_wait_clear(st.se, SE_STATUS, 1u)) {
                st.dead = true; /* no other SE19 owner is supported */
                return EBUSY;
        }
        touch_wr(st.se, SE_IRQ_CLR, 0xffffffffu);
        touch_wr(st.se, SE_TX_WM, nt ? 1u : 0u);
        touch_wr(st.se, nr ? SE_RX_LEN : SE_TX_LEN, nr ? nr : nt);
        touch_wr(st.se, SE_CMD, ((nr ? 2u : 1u) << 27) |
            ((uint32_t)addr << 9) | (stretch ? 4u : 0u));
        {
                int err = ETIMEDOUT;
                for (unsigned i = 0; i < 20000; ++i) {
                        uint32_t irq = touch_irq();
                        if (irq & SE_ERRORS) {
                                err = (irq & SE_NACK) ? ENXIO : EIO;
                                /* A completed NACK is normal during address probing. Aborting an
                                 * idle sequencer may never produce ABORT_DONE. Wait for completion
                                 * and leave a clean idle engine available. */
                                for (unsigned j = 0; j < 5000 && !(irq & SE_DONE); ++j) {
                                        delay_for_interval(1, NSEC_PER_USEC);
                                        irq = touch_irq();
                                }
                                if ((irq & SE_DONE) && !(touch_rd(st.se, SE_STATUS) & 1u) &&
                                    !(touch_rd(st.se, SE_TX_COUNT) & 0xfffffffu) &&
                                    !(touch_rd(st.se, SE_RX_COUNT) & 0x1ffffffu)) {
                                        touch_wr(st.se, SE_IRQ_CLR, irq);
                                        return err;
                                }
                                break;
                        }
                        if (nt && st.sent < nt && (irq & SE_TX_WM_IRQ)) {
                                /* One word per watermark hit (threshold 1: at least one slot
                                 * free). Identical to the old single-word path for short
                                 * commands, and a paced stream for the 786-byte config write. */
                                uint32_t word = 0;
                                for (unsigned j = 0; j < 4u && st.sent + j < nt; ++j) {
                                        word |= (uint32_t)tx[st.sent + j] << (8u * j);
                                }
                                touch_wr(st.se, SE_TX, word);
                                st.sent += 4u;
                                if (st.sent >= nt) {
                                        st.sent = nt;
                                        touch_wr(st.se, SE_TX_WM, 0);
                                }
                        }
                        {
                                uint32_t count = touch_rd(st.se, SE_RX_COUNT) & 0x1ffffffu;
                                if (count > 64 || count > (nr - st.got + 3u) / 4u) {
                                        err = EIO;
                                        break;
                                }
                                while (count--) {
                                        uint32_t word = touch_rd(st.se, SE_RX);
                                        for (unsigned j = 0; j < 4 && st.got < nr; ++j) {
                                                rx[st.got++] = (uint8_t)(word >> (8u * j));
                                        }
                                }
                        }
                        /* DONE is checked after draining RX, so final short words aren't lost. */
                        if (irq & SE_DONE) {
                                /* Re-sample after the final drain; a late error must beat DONE. */
                                irq = touch_irq();
                                if (irq & SE_ERRORS) {
                                        continue;
                                }
                                touch_wr(st.se, SE_IRQ_CLR, irq);
                                if (st.got == nr && st.sent == nt) {
                                        return 0;
                                }
                                err = EIO;
                                break;
                        }
                        /* Preserve DONE/errors if they arrive during this iteration. */
                        touch_wr(st.se, SE_IRQ_CLR, irq & (SE_RX_WM_IRQ | SE_RX_LAST | SE_TX_WM_IRQ));
                        delay_for_interval(1, NSEC_PER_USEC);
                }
                touch_abort();
                return err;
        }
}

static int
touch_msg(uint8_t addr, const uint8_t *tx, unsigned nt, uint8_t *rx,
    unsigned nr, bool stretch)
{
        st.irq_status = st.irq_last = 0;
        st.sent = st.got = 0;
        {
                int err = touch_msg_run(addr, tx, nt, rx, nr, stretch);
                if (st.probing) {
                        printf("RAPHAEL TOUCH: probe xfer addr=%#x %s len=%u stretch=%u err=%d irq=%#x seen=%#x tx=%u rx=%u status=%#x\n",
                            addr, nr ? "read" : "write", nr ? nr : nt, stretch ? 1u : 0u,
                            err, st.irq_last, st.irq_status, st.sent, st.got,
                            touch_rd(st.se, SE_STATUS));
                }
                return err;
        }
}

static int
touch_read(uint8_t addr, uint16_t reg, bool wide, uint8_t *buf, unsigned len)
{
        uint8_t ptr[2] = {(uint8_t)(reg >> 8), (uint8_t)reg};
        int err = touch_msg(addr, wide ? ptr : ptr + 1, wide ? 2u : 1u,
            NULL, 0, true);

        if (err) {
                return err;
        }
        /* STOP_STRETCH on the pointer write retains the bus for a repeated START. */
        return touch_msg(addr, NULL, 0, buf, len, false);
}

static int
gt9886_clear(void)
{
        /* One zero byte to the frame register: clears the event the IC just posted
         * (vendor goodix_ts_threadirq_func "clean irq flag"). */
        const uint8_t ack[3] = {GT9886_COOR_REG >> 8, GT9886_COOR_REG & 0xffu, 0};

        return touch_msg(0x5d, ack, sizeof(ack), NULL, 0, false);
}

static int
gt9886_write_reg(uint16_t reg, const uint8_t *data, unsigned len)
{
        /* Register pointer + payload as one write; the IC's data pointer
         * auto-increments within the message. */
        uint8_t buf[2 + 784];

        if (len > sizeof(buf) - 2u) {
                return EIO;
        }
        buf[0] = (uint8_t)(reg >> 8);
        buf[1] = (uint8_t)reg;
        memcpy(buf + 2, data, len);
        return touch_msg(0x5d, buf, 2u + len, NULL, 0, false);
}

static int
gt9886_cmd(uint8_t op, uint8_t data)
{
        /* 3-byte command: {op, data, checksum} to 0x6f68 (vendor
         * goodix_cmds_init: cmds[2] = 0 - op - data). */
        const uint8_t cmd[3] = {op, data, (uint8_t)(0u - op - data)};

        return gt9886_write_reg(GT9886_CMD_REG, cmd, sizeof(cmd));
}

static int
gt9886_doze_disable(void)
{
        /* Vendor goodix_set_i2c_doze_mode(dev, false): write 0xAA to 0x30F0, then
         * poll 0x3100 for 0xBB (rewrite 0xAA whenever it reads neither 0xAA nor
         * 0xBB). Kept disabled afterwards: this driver polls on its own schedule,
         * not from an interrupt the IC raises while awake. */
        static const uint8_t aa[1] = {0xaa};

        for (unsigned i = 0; i < 10u; ++i) {
                int err = gt9886_write_reg(GT9886_DOZE_CTRL, aa, sizeof(aa));

                if (err) {
                        return err;
                }
                touch_wait(1);
                for (unsigned j = 0; j < 10u; ++j) {
                        uint8_t stat = 0;

                        err = touch_read(0x5d, GT9886_DOZE_STAT, true, &stat, 1);
                        if (!err && stat == 0xbbu) {
                                return 0;
                        }
                        if (!err && stat != 0xaau) {
                                break; /* rewrite 0xAA and retry */
                        }
                        touch_wait(10);
                }
        }
        return ETIMEDOUT;
}

static int
gt9886_send_config(const uint8_t *cfg, unsigned len)
{
        uint8_t rdy = 0;
        bool ok = false;
        int err;

        /* 1. command register free? */
        for (unsigned i = 0; i < 10u; ++i) {
                err = touch_read(0x5d, GT9886_CMD_REG, true, &rdy, 1);
                if (!err && rdy == GT9886_CMD_READY) {
                        ok = true;
                        break;
                }
                touch_wait(10);
        }
        if (!ok) {
                printf("RAPHAEL TOUCH: cfg send: cmd reg not ready (%#x)\n", rdy);
                return EIO;
        }
        /* 2. START_SEND_CFG (0x80) */
        err = gt9886_cmd(GT9886_CMD_START, 0);
        if (err) {
                printf("RAPHAEL TOUCH: cfg send: start cmd err=%d\n", err);
                return err;
        }
        /* 3. wait for PREPARE_OK (0x82), resending on abnormal reads */
        ok = false;
        for (unsigned i = 0; i < 10u; ++i) {
                uint8_t cmd_buf[3] = {0};

                err = touch_read(0x5d, GT9886_CMD_REG, true, cmd_buf, 3);
                if (!err && cmd_buf[0] == GT9886_CMD_PREPARE) {
                        ok = true;
                        break;
                }
                if (!err && cmd_buf[0] != GT9886_CMD_START) {
                        (void)gt9886_cmd(GT9886_CMD_START, 0);
                }
                touch_wait(10);
        }
        if (!ok) {
                printf("RAPHAEL TOUCH: cfg send: no 0x82 prepare\n");
                return EIO;
        }
        /* 4. the 784-byte config itself */
        err = gt9886_write_reg(GT9886_CFG_REG, cfg, len);
        if (err) {
                printf("RAPHAEL TOUCH: cfg send: write %uB err=%d\n", len, err);
                return err;
        }
        /* 5. END_SEND_CFG (0x83), then wait for the register to free */
        err = gt9886_cmd(GT9886_CMD_END, 0);
        if (err) {
                printf("RAPHAEL TOUCH: cfg send: end cmd err=%d\n", err);
                return err;
        }
        ok = false;
        for (unsigned i = 0; i < 10u; ++i) {
                err = touch_read(0x5d, GT9886_CMD_REG, true, &rdy, 1);
                if (!err && rdy == GT9886_CMD_READY) {
                        ok = true;
                        break;
                }
                touch_wait(10);
        }
        if (!ok) {
                printf("RAPHAEL TOUCH: cfg send: cmd reg stuck after send (%#x)\n", rdy);
                return EIO;
        }
        return 0;
}

/* 1080x2340 panel coordinates -> the 0..65535 of /dev/redwood-touch. */
#define NORM_X(v) ((uint32_t)(((uint64_t)(v) * 65535u + 539u) / 1079u))
#define NORM_Y(v) ((uint32_t)(((uint64_t)(v) * 65535u + 1169u) / 2339u))

static void
touch_report(uint32_t mask, const int *x, const int *y)
{
        for (unsigned id = 0; id < TOUCH_MAX; ++id) {
                uint32_t bit = 1u << id;
                if (mask & bit) {
                        if (!(st.active & bit)) {
                                redwood_touch_post(REDWOOD_TOUCH_DOWN, id,
                                    NORM_X(x[id]), NORM_Y(y[id]));
                        } else if (x[id] != st.x[id] || y[id] != st.y[id]) {
                                redwood_touch_post(REDWOOD_TOUCH_MOVE, id,
                                    NORM_X(x[id]), NORM_Y(y[id]));
                        }
                        st.x[id] = x[id];
                        st.y[id] = y[id];
                } else if (st.active & bit) {
                        redwood_touch_post(REDWOOD_TOUCH_UP, id,
                            NORM_X(st.x[id]), NORM_Y(st.y[id]));
                }
        }
        st.active = mask;
}

static int
touch_goodix_9886(void)
{
        uint8_t buf[4 + TOUCH_MAX * GT9886_POINT_LEN];
        int x[TOUCH_MAX] = {0}, y[TOUCH_MAX] = {0};
        uint32_t mask = 0;
        int err;

        /* Frame at 0x4100: read event + count + the first point (12 bytes), then the
         * remaining points in place. The whole 4+8n frame must sum to zero mod 256
         * (vendor checksum_u8 over touch_num*8+4 bytes). */
        err = touch_read(0x5d, GT9886_COOR_REG, true, buf, 4 + GT9886_POINT_LEN);
        if (err) {
                return err;
        }
        if (!(buf[0] & GT9886_TOUCH_EV)) {
                if (buf[0] & GT9886_REQUEST_EV) {
                        /* Firmware request (e.g. re-send config): log once, clear, and
                         * continue -- we cannot service requests we have not identified. */
                        static bool logged = false;

                        if (!logged) {
                                printf("RAPHAEL TOUCH: request event %#x; clearing\n", buf[0]);
                                logged = true;
                        }
                        (void)gt9886_clear();
                }
                return 0; /* no touch frame: retain current contacts */
        }
        {
                unsigned n = buf[1] & 15u;

                if (n > TOUCH_MAX) {
                        (void)gt9886_clear();
                        return EIO;
                }
                if (n > 1) {
                        err = touch_read(0x5d, GT9886_COOR_REG + 4u + GT9886_POINT_LEN, true,
                            buf + 4 + GT9886_POINT_LEN, (n - 1) * GT9886_POINT_LEN);
                        if (err) {
                                return err;
                        }
                }
                {
                        unsigned sum = 0;
                        for (unsigned i = 0; i < 4u + n * GT9886_POINT_LEN; ++i) {
                                sum += buf[i];
                        }
                        if ((sum & 0xffu) != 0u) {
                                (void)gt9886_clear();
                                return EIO;
                        }
                }
                for (unsigned i = 0; i < n; ++i) {
                        const uint8_t *p = buf + 2 + i * GT9886_POINT_LEN;
                        unsigned id = p[0] & 15u;
                        unsigned px = p[1] | ((unsigned)p[2] << 8);
                        unsigned py = p[3] | ((unsigned)p[4] << 8);
                        /* High bit of the id byte marks pen data; vendor drops what it
                         * cannot parse, so drop silently instead of failing. */
                        if ((p[0] & 0x80u) || id >= TOUCH_MAX || (mask & (1u << id)) ||
                            px > 1079u || py > TOUCH_MAX_Y) {
                                continue;
                        }
                        if (st.flip) {
                                px = 1079u - px;
                                py = TOUCH_MAX_Y - py;
                        }
                        mask |= 1u << id;
                        x[id] = (int)px;
                        y[id] = (int)py;
                }
        }
        err = gt9886_clear();
        if (!err) {
                touch_report(mask, x, y);
        }
        return err;
}

static bool
touch_se_init(void)
{
        uint32_t fw = touch_rd(st.se, SE_FW);
        uint32_t tx = touch_rd(st.se, SE_HW_TX), rx = touch_rd(st.se, SE_HW_RX);

        printf("RAPHAEL TOUCH: se19 fw=%#x proto=%u status=%#x tx=%#x rx=%#x ios=%#x\n",
            fw, (fw >> 8) & 0xffu, touch_rd(st.se, SE_STATUS), tx, rx,
            touch_rd(st.se, 0x908));
        if (((fw >> 8) & 0xffu) != 3u || (touch_rd(st.se, 0x64) & 1u) ||
            ((tx >> 24) & 0x3fu) != 32u || ((rx >> 24) & 0x3fu) != 32u ||
            ((tx >> 16) & 0x3fu) < 2u || ((rx >> 16) & 0x3fu) < 2u) {
                printf("RAPHAEL TOUCH: need ABL-loaded I2C firmware and 32-bit FIFOs; probes skipped\n");
                return false;
        }
        if (touch_rd(st.se, SE_STATUS) & 0x1001u) {
                printf("RAPHAEL TOUCH: SE19 already busy; probes skipped\n");
                return false;
        }
        touch_wr(st.se, 0xe18, 0); /* no GSI events */
        touch_wr(st.se, 0xe1c, 0); /* mask external IRQ routing; poll local status */
        touch_wr(st.se, 0x644, 0);
        touch_wr(st.se, SE_IRQ_CLR, 0xffffffffu);
        touch_wr(st.se, 0x648, 0xffffffffu);
        touch_wr(st.se, SE_DMA, 0);
        touch_wr(st.se, 0x28, touch_rd(st.se, 0x28) | 0x7fu);
        touch_wr(st.se, 0xe30, touch_rd(st.se, 0xe30) | 0x0fu);
        touch_wr(st.se, 0x24, 0x7f);
        touch_wr(st.se, 0x20, 1);
        touch_wr(st.se, SE_CLK_SEL, 0);
        touch_wr(st.se, SE_MCLK, (7u << 4) | 1u);
        touch_wr(st.se, SE_SCL, (10u << 20) | (11u << 10) | 26u); /* 100 kHz */
        /* Linux 8-bit I2C packing: MSB on the wire, LSB byte first in FIFO. */
        touch_wr(st.se, 0x254, 0);
        touch_wr(st.se, 0x260, SE_PACK0);
        touch_wr(st.se, 0x264, SE_PACK1);
        touch_wr(st.se, 0x284, SE_PACK0);
        touch_wr(st.se, 0x288, SE_PACK1);
        touch_wr(st.se, SE_TX_WM, 0);
        touch_wr(st.se, 0x810, 0);
        touch_wr(st.se, 0x814, ((rx >> 16) & 0x3fu) - 1u);
        touch_wr(st.se, SE_IRQ_EN, SE_EVENTS);
        printf("RAPHAEL TOUCH: packing tx=%#x/%#x rx=%#x/%#x gran=%#x\n",
            touch_rd(st.se, 0x260), touch_rd(st.se, 0x264),
            touch_rd(st.se, 0x284), touch_rd(st.se, 0x288), touch_rd(st.se, 0x254));
        if (touch_rd(st.se, 0x260) != SE_PACK0 || touch_rd(st.se, 0x264) != SE_PACK1 ||
            touch_rd(st.se, 0x284) != SE_PACK0 || touch_rd(st.se, 0x288) != SE_PACK1 ||
            touch_rd(st.se, 0x254) != 0) {
                printf("RAPHAEL TOUCH: packing readback mismatch; probes skipped\n");
                return false;
        }
        if ((touch_rd(st.se, SE_TX_COUNT) & 0xfffffffu) ||
            (touch_rd(st.se, SE_RX_COUNT) & 0x1ffffffu)) {
                printf("RAPHAEL TOUCH: inherited FIFO data; probes skipped\n");
                return false;
        }
        return true;
}

static void
touch_thread(void *arg, wait_result_t result)
{
        unsigned delay_s = 5;
        static int flip_arg = -1;

        (void)arg;
        (void)result;
        /*
         * Start late (boot-arg touchdelay, seconds; the SWEET track used 45 while the
         * sequence was still a suspect): the bring-up is proven on this phone, but a
         * short wait still lets launchd get going first, and a boot that dies at the
         * first hardware access leaves the log lines and the GUI a clear ordering.
         */
        (void)PE_parse_boot_argn("touchdelay", &delay_s, sizeof(delay_s));
        printf("RAPHAEL TOUCH: starting in %u s (SE19 @ %#x, GT9886 @ 0x5d)\n", delay_s, TOUCH_SE_PA);
        touch_wait(delay_s * 1000u);

        st.gcc = ml_io_map(TOUCH_GCC_PA, 0x60000);
        st.gpio = ml_io_map(TOUCH_GPIO_PA, 0x80000);
        st.se = ml_io_map(TOUCH_SE_PA, 0x4000);
        if (!st.gcc || !st.gpio || !st.se) {
                printf("RAPHAEL TOUCH: MMIO mapping failed\n");
                goto out;
        }
        printf("RAPHAEL TOUCH: mapping GCC/TLMM-south/SE19\n");
        if (!touch_clocks() || !touch_se_init()) {
                goto out;
        }
        printf("RAPHAEL TOUCH: TLMM reset(gpio%u)=%#x irq(gpio%u)=%#x; GPIO rail power\n",
            TOUCH_GPIO_RESET, touch_rd(st.gpio, TOUCH_GPIO_IO(TOUCH_GPIO_RESET)),
            TOUCH_GPIO_IRQ, touch_rd(st.gpio, TOUCH_GPIO_IO(TOUCH_GPIO_IRQ)));
        /* IRQ stays an input throughout (vendor tp_active: pull-up, 16 mA). */
        touch_gpio(TOUCH_GPIO_IRQ, 0, 3, 3, false, false); /* IRQ input, pull-up */
        /* Cold power cycle: the previous OS may have left the IC mid-transaction or
         * its rails already sequenced. Hold reset while both GPIO rails go through a
         * full off/on edge, so every bring-up starts from t0. */
        touch_gpio(TOUCH_GPIO_RESET, 0, 3, 3, true, false); /* reset asserted */
        touch_gpio(TOUCH_GPIO_VDD, 0, 0, 0, true, false);   /* 3v3 off */
        touch_gpio(TOUCH_GPIO_AVDD, 0, 0, 0, true, false);  /* 1v8 off */
        touch_wait(100); /* vendor power-off-delay is 5 ms; rails and decays */
        touch_gpio(TOUCH_GPIO_VDD, 0, 0, 0, true, true);    /* 3v3 on */
        touch_gpio(TOUCH_GPIO_AVDD, 0, 0, 0, true, true);   /* 1v8 on */
        touch_wait(20); /* conservative rail settling; not a DT timing value */
        touch_gpio(TOUCH_GPIO_SDA, 2, 0, 2, false, false); /* qup19, 2 mA */
        touch_gpio(TOUCH_GPIO_SCL, 2, 0, 2, false, false);
        /* Bus verdict while the IC still holds reset: idle SDA/SCL must read high
         * through the board pull-ups (powered from the avdd rail) if the GPIO rail
         * power is real. ios mirrors the same pads from the serial engine side.
         * bit0 of each TLMM IN_OUT register is the pad input. */
        printf("RAPHAEL TOUCH: bus idle vdd=%#x avdd=%#x sda=%#x scl=%#x reset=%#x ios=%#x\n",
            touch_rd(st.gpio, TOUCH_GPIO_IO(TOUCH_GPIO_VDD)),
            touch_rd(st.gpio, TOUCH_GPIO_IO(TOUCH_GPIO_AVDD)),
            touch_rd(st.gpio, TOUCH_GPIO_IO(TOUCH_GPIO_SDA)),
            touch_rd(st.gpio, TOUCH_GPIO_IO(TOUCH_GPIO_SCL)),
            touch_rd(st.gpio, TOUCH_GPIO_IO(TOUCH_GPIO_RESET)),
            touch_rd(st.se, 0x908));
        /* Vendor GT9889 dev_confirm: reset low 2 ms, then high. */
        touch_wait(2);
        touch_gpio(TOUCH_GPIO_RESET, 0, 3, 3, true, true);
        touch_wait(300); /* exceeds vendor 5 ms test + 95 ms post-test wait */
        printf("RAPHAEL TOUCH: bus live sda=%#x scl=%#x irq=%#x ios=%#x\n",
            touch_rd(st.gpio, TOUCH_GPIO_IO(TOUCH_GPIO_SDA)),
            touch_rd(st.gpio, TOUCH_GPIO_IO(TOUCH_GPIO_SCL)),
            touch_rd(st.gpio, TOUCH_GPIO_IO(TOUCH_GPIO_IRQ)),
            touch_rd(st.se, 0x908));
        /* GT9886 "normandy" bring-up per the vendor driver: identify, pick the
         * matching panel config, keep the IC awake, download the config, then poll
         * 0x4100. (The SWEET track's lesson: probe nothing else -- a 0x49 NACK left
         * bytes in the TX FIFO that poisoned every following transfer.) */
        {
                uint8_t pid[4] = {0}, sensor = 0, ver[GT9886_VER_LEN] = {0};
                unsigned vsum = 0;
                int pe, se, ve;
                bool id_ok;

                st.probing = true;
                pe = touch_read(0x5d, GT9886_PID_REG, true, pid, 4);
                se = touch_read(0x5d, GT9886_SENSOR_REG, true, &sensor, 1);
                ve = touch_read(0x5d, GT9886_VER_BASE, true, ver, GT9886_VER_LEN);
                st.probing = false;
                for (unsigned i = 0; i < GT9886_VER_LEN; ++i) {
                        vsum += ver[i];
                }
                printf("RAPHAEL TOUCH: GT9886 pid err=%d [%02x %02x %02x %02x] sensor err=%d "
                    "id=%#x ver err=%d sum=%#x\n",
                    pe, pid[0], pid[1], pid[2], pid[3], se, sensor & 0x0fu, ve, vsum & 0xffu);
                if (st.dead) {
                        printf("RAPHAEL TOUCH: sequencer dead; stopping\n");
                        goto out;
                }
                id_ok = !pe && pid[0] == '9' && pid[1] == '8' && pid[2] == '8' && pid[3] == '6';
                if (!id_ok) {
                        /* The vendor rejects the cfg package and walks a firmware update
                         * when the pid mismatches. We have no update path: stop with the
                         * evidence in the log. */
                        printf("RAPHAEL TOUCH: pid is not 9886 (fw update path not built); stopping\n");
                        goto out;
                }
                /* Panel supplier id selects the config package; the DT flips both axes
                 * for sensor 0 only (raphael-sm8150.dtsi sensor0: x2x, y2y). */
                {
                        unsigned sid = se ? 0u : (sensor & 0x0fu);
                        const uint8_t *cfg = gt9886_cfg_f11_sensor0;
                        unsigned cfg_len = sizeof(gt9886_cfg_f11_sensor0);
                        int dz, ce;

                        st.flip = true;
                        if (sid == 1u) {
                                cfg = gt9886_cfg_f11_sensor1;
                                cfg_len = sizeof(gt9886_cfg_f11_sensor1);
                                st.flip = false;
                        }
                        /* touchflip=1: one more 180-degree turn on top of the panel's own
                         * (a held-upside-down test knob, as the SWEET track's was). */
                        if (flip_arg < 0) {
                                flip_arg = 0;
                                (void)PE_parse_boot_argn("touchflip", &flip_arg, sizeof(flip_arg));
                        }
                        if (flip_arg) {
                                st.flip = !st.flip;
                        }
                        dz = gt9886_doze_disable();
                        printf("RAPHAEL TOUCH: doze disable err=%d (staying awake for polling)\n", dz);
                        ce = gt9886_send_config(cfg, cfg_len);
                        printf("RAPHAEL TOUCH: cfg send sensor%u %uB err=%d flip=%d\n",
                            sid, cfg_len, ce, st.flip);
                        if (ce) {
                                printf("RAPHAEL TOUCH: config download failed; polling anyway\n");
                        }
                }
        }
        st.kind = 1; /* the GT9886, the only IC this driver knows */
        printf("RAPHAEL TOUCH: selected GT9886 normandy, 1080x%u, poll=10ms irq-hint=%d\n",
            TOUCH_MAX_Y + 1u, st.irq_hint);
        {
                unsigned errors = 0, ticks = 0;

                while (!st.dead) {
                        /* Optional IRQ hint still reads every 100ms, and always while down. */
                        if (!st.irq_hint || st.active || !(touch_rd(st.gpio, TOUCH_GPIO_IO(TOUCH_GPIO_IRQ)) & 1u) ||
                            (++ticks % 10u) == 0) {
                                int err = touch_goodix_9886();
                                if (err) {
                                        if ((errors++ % 100u) == 0) {
                                                printf("RAPHAEL TOUCH: report err=%d irq=%#x\n", err, st.irq_status);
                                        }
                                        if (errors == 10) {
                                                touch_report(0, st.x, st.y); /* release after persistent bad reads */
                                        }
                                } else {
                                        errors = 0;
                                }
                        }
                        touch_wait(10);
                }
                touch_report(0, st.x, st.y);
        }
out:
        (void)thread_terminate(current_thread());
        thread_block(THREAD_CONTINUE_NULL);
}

void
raphael_touch_init(void)
{
        int enabled = 1;
        thread_t thread;

        if (st.started) {
                return;
        }
        st.started = true;
        (void)PE_parse_boot_argn("touchscreen", &enabled, sizeof(enabled));
        (void)PE_parse_boot_argn("touchirq", &st.irq_hint, sizeof(st.irq_hint));
        if (!enabled) {
                printf("RAPHAEL TOUCH: disabled (touchscreen=0)\n");
                return;
        }
        if (kernel_thread_start(touch_thread, NULL, &thread) == KERN_SUCCESS) {
                thread_deallocate(thread);
        } else {
                printf("RAPHAEL TOUCH: kernel_thread_start failed\n");
        }
}
