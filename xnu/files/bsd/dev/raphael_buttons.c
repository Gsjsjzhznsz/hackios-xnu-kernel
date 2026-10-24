/*
 * The buttons of raphael (Mi 9 / K20 Pro, SM8150) -- power, volume up, volume down -- as a
 * source of button records for /dev/redwood-touch (sys/redwood_input.h). Only with boot-arg
 * buttons=1. The architecture is redwood_buttons.c's (the POCO X5 Pro driver this tree ships:
 * PMIC-arbiter observer reads, a poll thread, the "not believed until seen released" rule);
 * every address and both peripherals below are raphael's own.
 *
 * None of the three is a pin of the SoC. They are inputs of the phone's power management
 * chips, on the SPMI bus behind the SoC's "PMIC arbiter" (the same version-5 arbiter at the
 * same addresses as redwood's -- SM8150 and its PMICs keep that geometry; mainline
 * sm8150.dtsi and this phone's own tree say so):
 *   power        KPDPWR_N of the power-on block ("PON", peripheral 0x800) of the PM8150
 *                (slave 0): PON_RT_STS bit 0 (bit 7 on the "generation 3" split PONs --
 *                read below, the subtype says which)
 *   volume down  RESIN_N of the same block: PON_RT_STS bit 1 (bit 6 on gen3) -- the
 *                vendor's kernel and mainline both map RESIN to VOLUMEDOWN on raphael
 *   volume up    GPIO6 of the same PM8150 (peripheral 0xc5), pulled up, the key pulls it
 *                low (gpio-keys, gpios = <&pm8150_gpios 6 GPIO_ACTIVE_LOW> in the phone's
 *                own device tree): the GPIO's real-time status bit 0 is the pin level
 *
 * THIS DRIVER ONLY READS. Nothing is written to any PMIC and nothing is reconfigured: a
 * wrong write to the PMIC that makes the phone's supplies can switch one off. The firmware
 * (which reads these same keys for its fastboot/recovery dance) has left all three inputs
 * as inputs with pulls; step 4 checks that before believing each key, and where it finds
 * something else it says so and leaves that key alone.
 *
 * What a press is: the PON's "real time status" has a bit per key, set while the key is
 * held (after the block's own debouncing); the GPIO's has the level of the pin. A thread
 * reads the two registers 60 times a second, and a key has changed when two looks in a row
 * say so. A key that reads as held before it has ever been seen released is not believed.
 * No interrupt is used (what one would take is said at the end of redwood_buttons.c, and
 * the reasoning carries over word for word).
 *
 * The mapping: the phone has what the iPhone X has, whose user space runs here -- a side
 * button and two volume keys. Power is the side button (REDWOOD_BUTTON_LOCK).
 *
 * The bring-up is made in steps, each said in the log before it is made (boot-arg
 * buttons_steps=N stops after the first N):
 *   1  map the arbiter and read its version             (the first access to that block)
 *   2  find the channels of the two peripherals         (reads of the arbiter's tables)
 *   3  ask each peripheral what it is, and which PON generation the PON is
 *   4  read how the keys' inputs are set up, and what they say now
 *   5  watch them                                       (the default)
 *
 * Register layouts: Linux drivers/spmi/spmi-pmic-arb.c (arbiter version 5),
 * drivers/input/misc/pm8941-pwrkey.c (PON_RT_STS and its per-generation bits),
 * drivers/pinctrl/qcom/pinctrl-spmi-gpio.c (GPIO peripherals), and the phone's own device
 * tree (pm8150@0: pon@800 with pwrkey and resin, gpio6 as volume up).
 */
#include <kern/clock.h>
#include <kern/locks.h>
#include <kern/thread.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern kern_return_t thread_terminate(thread_t thread);

/*
 * Every line goes to the loader's log directly as well: without -v the kernel's own
 * messages reach that log only at the end of a run, and "which step died" is the point
 * of the steps.
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

static inline uint32_t
rd(vm_offset_t address)
{
        return *(volatile uint32_t *)address;
}

static inline void
wr(vm_offset_t address, uint32_t value)
{
        *(volatile uint32_t *)address = value;
}

/* ---- the PMIC arbiter ---------------------------------------------------------------------------- */

#define ARB_CORE_PHYS           0x0c440000UL    /* "core": the version, and which peripheral each channel is */
#define ARB_CORE_SIZE           0x1100UL
#define ARB_CNFG_PHYS           0x0c40a000UL    /* "cnfg": which system owns each channel */
#define ARB_CNFG_SIZE           0x1000UL
#define ARB_OBSERVER_PHYS       0x0e600000UL    /* "obsrvr": 0x10000 per system, 0x80 per channel in it */
#define ARB_OBSERVER_SIZE       0x10000UL       /* ours is the first */
#define ARB_SYSTEM              0               /* who we are to the arbiter: the device tree's "qcom,ee" */

#define ARB_VERSION             0x000
#define ARB_VERSION_5           0x50000000u     /* what follows is version 5's; 7 moved things again */
#define ARB_VERSION_7           0x70000000u
#define ARB_FEATURES            0x004           /* [10:0] the number of channels */
#define ARB_CHANNEL(n)          (0x900 + 4 * (n))       /* core: [19:8] slave and peripheral, [24] interrupts go to the owner */
#define ARB_CHANNEL_INTERRUPTS  (1u << 24)
#define ARB_OWNER(n)            (0x700 + 4 * (n))       /* cnfg: [2:0] the system that may write through channel n */
#define ARB_MAX_CHANNELS        512

#define OBSERVER_COMMAND        0x00
#define OBSERVER_STATUS         0x08
#define OBSERVER_DATA0          0x18            /* the bytes read, the first in the lowest byte */
#define OBSERVER_DATA1          0x1c
#define STATUS_DONE             0x1
#define STATUS_FAILURE          0x2
#define STATUS_DENIED           0x4
#define STATUS_DROPPED          0x8
#define COMMAND_READ            (1u << 27)      /* "extended register read long"; [11:4] register, [2:0] bytes - 1 */
#define READ_TIMEOUT_US         2000            /* a read takes some tens of microseconds */

static vm_offset_t arb_core, arb_cnfg, arb_observer;

/*
 * One transfer at a time through the arbiter for this system, whoever asks (the flashlight's
 * driver, bsd/dev/redwood_torch.c, as well): with two at once the arbiter drops one, as
 * Linux never lets happen (one lock for the bus).
 */
static LCK_GRP_DECLARE(spmi_lck_grp, "raphael-spmi");
static LCK_SPIN_DECLARE(spmi_lock, &spmi_lck_grp);

void
redwood_spmi_lock(void)
{
        lck_spin_lock(&spmi_lock);
}

void
redwood_spmi_unlock(void)
{
        lck_spin_unlock(&spmi_lock);
}

struct peripheral {
        const char *    name;
        uint16_t        id;             /* slave << 8 | peripheral: the high byte of its register addresses */
        uint16_t        channel;
        bool            found;
};
static struct peripheral pon = { .name = "the PM8150's power-on block", .id = 0x008 };       /* registers 0x800 of slave 0 */
static struct peripheral pin = { .name = "GPIO6 of the PM8150", .id = 0x0c5 };              /* registers 0xc500 of slave 0 */

/* How the reads have gone: said once a minute. */
static struct {
        uint64_t reads, failed, total_ns, longest_ns, last_ns;
} bus;

/*
 * Read `count` (1 to 8) registers of a peripheral, from register `reg` on. Returns NULL, or what
 * went wrong.
 */
static const char *
spmi_read(const struct peripheral *p, uint8_t reg, uint8_t *bytes, uint32_t count)
{
        vm_offset_t channel = arb_observer + 0x10000UL * ARB_SYSTEM + 0x80UL * p->channel;
        uint64_t start = mach_absolute_time(), now, interval, taken;
        uint32_t status, data[2];
        const char *problem = NULL;

        nanoseconds_to_absolutetime(READ_TIMEOUT_US * (uint64_t)NSEC_PER_USEC, &interval);
        redwood_spmi_lock();
        wr(channel + OBSERVER_COMMAND, COMMAND_READ | (uint32_t)reg << 4 | (count - 1));
        do {
                status = rd(channel + OBSERVER_STATUS);
                now = mach_absolute_time();
        } while (!(status & STATUS_DONE) && now - start < interval);
        data[0] = rd(channel + OBSERVER_DATA0);
        data[1] = count > 4 ? rd(channel + OBSERVER_DATA1) : 0;
        redwood_spmi_unlock();

        if (!(status & STATUS_DONE)) {
                problem = "no answer from the arbiter";
        } else if (status & STATUS_DENIED) {
                problem = "the arbiter refuses";
        } else if (status & STATUS_FAILURE) {
                problem = "the transfer failed (nobody at that address?)";
        } else if (status & STATUS_DROPPED) {
                problem = "the transfer was dropped";
        } else {
                for (uint32_t i = 0; i < count; i++) {
                        bytes[i] = (uint8_t)(data[i / 4] >> (8 * (i % 4)));
                }
        }
        absolutetime_to_nanoseconds(now - start, &taken);
        bus.reads++;
        bus.total_ns += taken;
        bus.last_ns = taken;
        if (taken > bus.longest_ns) {
                bus.longest_ns = taken;
        }
        if (problem != NULL) {
                bus.failed++;
        }
        return problem;
}

/*
 * Which channel is that peripheral's. Several can be: one per system that may write to it. As
 * Linux chooses: the first, unless a later one is ours to write through.
 */
static void
find_channel(struct peripheral *p, uint32_t channels)
{
        for (uint32_t n = 0; n < channels; n++) {
                uint32_t map = rd(arb_core + ARB_CHANNEL(n)), owner;

                if (map == 0 || ((map >> 8) & 0xfff) != p->id) {
                        continue;
                }
                owner = rd(arb_cnfg + ARB_OWNER(n)) & 7;
                printf("buttons: channel %u is %s (%#x); written by system %u%s\n", n, p->name, map, owner,
                    (map & ARB_CHANNEL_INTERRUPTS) ? ", which also gets its interrupts" : "");
                if (!p->found || owner == ARB_SYSTEM) {
                        p->found = true;
                        p->channel = (uint16_t)n;
                }
        }
        if (!p->found) {
                printf("buttons: no channel of the arbiter is %s (slave %u, peripheral %#x)\n", p->name, p->id >> 8, p->id & 0xff);
        }
}

/* ---- the two peripherals ------------------------------------------------------------------------- */

/* every peripheral of these PMICs */
#define REG_TYPE                0x04
#define REG_SUBTYPE             0x05
#define REG_RT_STATUS           0x10            /* the inputs as they are now */
#define REG_INT_SET_TYPE        0x11            /* interrupts: a bit per input in each of these */
#define REG_INT_POLARITY_HIGH   0x12
#define REG_INT_POLARITY_LOW    0x13
#define REG_INT_ENABLED         0x15
#define REG_INT_LATCHED         0x18
#define REG_INT_PENDING         0x19

/* the power-on block's key inputs have pull-ups of their own, and a debouncer; both decide
 * whether a press can be seen at all (pm8941-pwrkey.c: PON_PULL_CTL, PON_DBC_CTL -- where
 * Linux *writes* these, we only look: a wrong write to the phone's supplies is not ours to
 * risk, but a resin whose pull-up the boot chain left off is exactly what a dead volume
 * down without a dead power key looks like) */
#define PON_REG_PULL_CTL        0x70            /* [0]: resin's pull-up, [1]: the power key's */
#define PON_REG_DBC_CTL         0x71            /* the debouncer's time, as a code */

/* the power-on block: which generation tells which bits of the status are the keys
 * (drivers/input/misc/pm8941-pwrkey.c) */
#define PON_TYPE                0x01
#define PON_SUBTYPE_PRIMARY     0x01            /* the classic layout: KPDPWR bit 0, RESIN bit 1 */
#define PON_SUBTYPE_GEN3_HLOS   0x08            /* the split generation 3: KPDPWR bit 7, RESIN bit 6 */

#define PON_KPDPWR_CLASSIC      0x01
#define PON_RESIN_CLASSIC       0x02
#define PON_KPDPWR_GEN3         0x80
#define PON_RESIN_GEN3          0x40

/* a GPIO of the low or medium voltage kind (pinctrl-spmi-gpio.c) */
#define GPIO_TYPE               0x10
#define GPIO_SUBTYPE_LV         0x10
#define GPIO_SUBTYPE_MV_VIN3_CLK 0x15           /* 0x10 to 0x15: one layout */
#define GPIO_LEVEL              0x01            /* in the status */
#define GPIO_REG_MODE           0x40            /* [1:0]: 0 input, 1 output, 2 both, 3 analog */
#define GPIO_REG_SUPPLY         0x41
#define GPIO_REG_PULL           0x42            /* [2:0]: 0 to 3 up, 4 down, 5 none */
#define GPIO_REG_ENABLE         0x46            /* [7] */

#define POLL_MS                 16
#define MINUTE_TICKS            (60 * 1000 / POLL_MS)

static struct button {
        const char *    name;
        uint32_t        record;         /* REDWOOD_BUTTON_* */
        bool            usable;         /* its input is what it was taken for */
        bool            live;           /* it has been seen released */
        bool            last;           /* the look before this one */
        bool            down;           /* as reported */
        uint64_t        since;          /* when it went down */
        uint32_t        presses;
} power = { .name = "power", .record = REDWOOD_BUTTON_LOCK },
    volume_up = { .name = "volume up", .record = REDWOOD_BUTTON_VOLUME_UP },
    volume_down = { .name = "volume down", .record = REDWOOD_BUTTON_VOLUME_DOWN };

/* Which status bits the PON's keys are, set by step 3 (the subtype says the generation). */
static uint8_t pon_kpdpwr_bit = PON_KPDPWR_CLASSIC, pon_resin_bit = PON_RESIN_CLASSIC;

/* The raw status bytes the key events are built from, and what they did.  A key that fires
 * nothing is judged by whether its byte ever moved: the witness lines below make "the
 * hardware (or its pull-up, or its debouncer) never saw the press" tellable from "the driver
 * dropped what the hardware saw", which the R14 volume-key question needs. */
static struct {
        uint8_t pon, pin;
        bool pon_seen, pin_seen;
        uint32_t pon_moves, pin_moves, said;
} raw;

static void
witness(uint8_t *was, bool *seen, uint32_t *moves, const char *what, uint8_t now)
{
        if (*seen && now != *was) {
                (*moves)++;
                if (raw.said < 40) {
                        raw.said++;
                        printf("buttons: %s's status byte changed to %#x (was %#x)\n", what, now, *was);
                }
        }
        *was = now;
        *seen = true;
}

/* "12.345 s (08:13:05.918 UTC)": since boot as in the log's own stamps, and as the daemons' lines have it */
static void
now_text(char *text, size_t size, uint64_t now)
{
        clock_sec_t seconds;
        clock_usec_t micro;
        uint64_t up;

        absolutetime_to_nanoseconds(now, &up);
        clock_get_calendar_microtime(&seconds, &micro);
        snprintf(text, size, "%llu.%03llu s (%02u:%02u:%02u.%03u UTC)", up / NSEC_PER_SEC, up / NSEC_PER_MSEC % 1000,
            (unsigned)(seconds / 3600 % 24), (unsigned)(seconds / 60 % 60), (unsigned)(seconds % 60), (unsigned)(micro / 1000));
}

static void
button_look(struct button *b, bool pressed)
{
        uint64_t now = mach_absolute_time(), held;
        bool steady = pressed == b->last;
        char when[64];

        b->last = pressed;
        if (!b->usable || !steady) {
                return;
        }
        if (!b->live) {
                if (!pressed) {
                        b->live = true;
                        printf("buttons: %s is released now: believed from here on\n", b->name);
                }
                return;
        }
        if (pressed == b->down) {
                return;
        }
        b->down = pressed;
        now_text(when, sizeof(when), now);
        if (pressed) {
                b->since = now;
                b->presses++;
                printf("buttons: %s PRESSED at %s\n", b->name, when);
        } else {
                absolutetime_to_nanoseconds(now - b->since, &held);
                printf("buttons: %s released at %s, after %llu ms\n", b->name, when, held / NSEC_PER_MSEC);
        }
        redwood_touch_post(pressed ? REDWOOD_BUTTON_DOWN : REDWOOD_BUTTON_UP, b->record, 0, 0);
}

/* One look at both peripherals. */
static void
buttons_poll(void)
{
        static unsigned said;
        const char *problem;
        uint8_t status;

        if (power.usable || volume_down.usable) {
                problem = spmi_read(&pon, REG_RT_STATUS, &status, 1);
                if (problem == NULL) {
                        witness(&raw.pon, &raw.pon_seen, &raw.pon_moves, "the power-on block", status);
                        button_look(&power, status & pon_kpdpwr_bit);
                        button_look(&volume_down, status & pon_resin_bit);
                } else if (said < 8) {
                        said++;
                        printf("buttons: reading %s: %s\n", pon.name, problem);
                }
        }
        if (volume_up.usable) {
                problem = spmi_read(&pin, REG_RT_STATUS, &status, 1);
                if (problem == NULL) {
                        witness(&raw.pin, &raw.pin_seen, &raw.pin_moves, "volume up's pin", status);
                        button_look(&volume_up, !(status & GPIO_LEVEL));
                } else if (said < 8) {
                        said++;
                        printf("buttons: reading %s: %s\n", pin.name, problem);
                }
        }
}

/* Step 3: is each peripheral what the registers above are those of? */
static bool
identify(const struct peripheral *p, uint8_t type, uint8_t subtype_from, uint8_t subtype_to)
{
        const char *problem;
        uint8_t is[2];

        if (!p->found) {
                return false;
        }
        printf("buttons: next: read the type of %s through channel %u (2 registers, 0x%02x04)\n", p->name, p->channel, p->id & 0xff);
        problem = spmi_read(p, REG_TYPE, is, 2);
        if (problem != NULL) {
                printf("buttons: %s: %s\n", p->name, problem);
                return false;
        }
        printf("buttons: %s says type %#x, subtype %#x (the read took %llu us)\n", p->name, is[0], is[1], bus.last_ns / 1000);
        if (is[0] != type || is[1] < subtype_from || is[1] > subtype_to) {
                printf("buttons: ... which is not what this driver knows (type %#x, subtype %#x to %#x): left alone\n", type,
                    subtype_from, subtype_to);
                return false;
        }
        return true;
}

/* Step 4 for the power-on block: which generation it is decides the status bits; what it says
 * now goes to the log, together with its interrupt setup. */
static void
pon_inputs(uint8_t subtype)
{
        const char *problem;
        uint8_t r[8], more[2], pull[2] = { 0, 0 }, pulled = 0;

        printf("buttons: next: read the status and the interrupt setup of %s (registers 0x810 to 0x819)\n", pon.name);
        problem = spmi_read(&pon, REG_RT_STATUS, r, 8);
        if (problem == NULL) {
                problem = spmi_read(&pon, REG_INT_LATCHED, more, 2);
        }
        if (problem == NULL) {
                pulled = spmi_read(&pon, PON_REG_PULL_CTL, pull, 2) == NULL;    /* a look, not a gate */
        }
        if (problem != NULL) {
                printf("buttons: %s: %s\n", pon.name, problem);
                return;
        }
        if (subtype >= PON_SUBTYPE_GEN3_HLOS) {
                pon_kpdpwr_bit = PON_KPDPWR_GEN3;
                pon_resin_bit = PON_RESIN_GEN3;
        }
        printf("buttons: power-on block (subtype %#x, %s generation): status %#x (power %s, volume down %s); interrupts: edge %#x, "
            "high %#x, low %#x, enabled %#x, latched %#x, pending %#x\n", subtype,
            subtype >= PON_SUBTYPE_GEN3_HLOS ? "third" : "classic", r[0],
            (r[0] & pon_kpdpwr_bit) ? "HELD" : "released", (r[0] & pon_resin_bit) ? "HELD" : "released",
            r[REG_INT_SET_TYPE - REG_RT_STATUS], r[REG_INT_POLARITY_HIGH - REG_RT_STATUS],
            r[REG_INT_POLARITY_LOW - REG_RT_STATUS], r[REG_INT_ENABLED - REG_RT_STATUS], more[0], more[1]);
        if (pulled) {
                printf("buttons: the power-on block's pull-ups: resin's bit is %s, the power key's %s; the debouncer says %#x\n",
                    (pull[0] & 1) ? "on" : "OFF (a volume down would read as never pressed)",
                    (pull[0] & 2) ? "on" : "OFF", pull[1]);
        }
        power.usable = volume_down.usable = true;
        power.last = r[0] & pon_kpdpwr_bit;
        volume_down.last = r[0] & pon_resin_bit;
}

/* Step 4 for the pin: it is volume up only if it is an input with a pull-up, and switched on. */
static void
pin_input(void)
{
        const char *problem;
        uint8_t c[8], status = 0;
        unsigned mode, pull;

        printf("buttons: next: read the setup and the level of %s (registers 0xc540 to 0xc547, 0xc510)\n", pin.name);
        problem = spmi_read(&pin, GPIO_REG_MODE, c, 8);
        if (problem == NULL) {
                problem = spmi_read(&pin, REG_RT_STATUS, &status, 1);
        }
        if (problem != NULL) {
                printf("buttons: %s: %s\n", pin.name, problem);
                return;
        }
        mode = c[0] & 3;
        pull = c[GPIO_REG_PULL - GPIO_REG_MODE] & 7;
        printf("buttons: volume up's pin: mode %u (%s), supply %u, pull %u (%s), %s; registers 0x40.. = %02x %02x %02x %02x %02x %02x "
            "%02x %02x; level %s\n", mode, mode == 0 ? "input" : mode == 1 ? "output" : mode == 2 ? "input and output" : "analog",
            c[GPIO_REG_SUPPLY - GPIO_REG_MODE] & 7, pull, pull < 4 ? "up" : pull == 4 ? "down" : "none",
            (c[GPIO_REG_ENABLE - GPIO_REG_MODE] & 0x80) ? "enabled" : "DISABLED", c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7],
            (status & GPIO_LEVEL) ? "high (released)" : "LOW (held)");
        if (mode != 0 || pull >= 4 || !(c[GPIO_REG_ENABLE - GPIO_REG_MODE] & 0x80)) {
                printf("buttons: volume up's pin is not an enabled input with a pull-up. Making it one is a write to the PMIC, "
                    "which this driver does not do: volume up is left alone\n");
                return;
        }
        volume_up.usable = true;
        volume_up.last = !(status & GPIO_LEVEL);
}

static void
say_summary(void)
{
        printf("buttons: so far %u presses of power, %u of volume up, %u of volume down; now%s%s%s%s; %llu reads of the bus, "
            "%llu us each (the longest %llu), %llu failed\n", power.presses, volume_up.presses, volume_down.presses,
            power.down ? " power" : "", volume_up.down ? " volume-up" : "", volume_down.down ? " volume-down" : "",
            (power.down || volume_up.down || volume_down.down) ? " held" : " none held", bus.reads,
            bus.reads ? bus.total_ns / bus.reads / 1000 : 0, bus.longest_ns / 1000, bus.failed);
        printf("buttons: the raw bytes: power-on block %#x (moved %u times%s), volume up's pin %#x (moved %u times%s)\n",
            raw.pon, raw.pon_moves, raw.pon_seen ? "" : ", not yet read", raw.pin, raw.pin_moves,
            raw.pin_seen ? "" : ", not yet read");
        if (raw.said > 40) {
                printf("buttons: ... and %u more changes of a raw byte, no longer said one by one\n", raw.said - 40);
        }
}

static void
buttons_thread(void *parameter, __unused wait_result_t result)
{
        uint32_t steps = (uint32_t)(uintptr_t)parameter, version, channels;
        bool pon_known, pin_known;
        uint8_t pon_subtype = PON_SUBTYPE_PRIMARY;

        printf("buttons: step 1: map the PMIC arbiter (%#lx, %#lx, %#lx) and read its version\n", ARB_CORE_PHYS, ARB_CNFG_PHYS,
            ARB_OBSERVER_PHYS);
        arb_core = ml_io_map(ARB_CORE_PHYS, ARB_CORE_SIZE);
        arb_cnfg = ml_io_map(ARB_CNFG_PHYS, ARB_CNFG_SIZE);
        arb_observer = ml_io_map(ARB_OBSERVER_PHYS, ARB_OBSERVER_SIZE);
        version = rd(arb_core + ARB_VERSION);
        channels = rd(arb_core + ARB_FEATURES) & 0x7ff;
        printf("buttons: PMIC arbiter version %#x, %u channels\n", version, channels);
        if (version < ARB_VERSION_5 || version >= ARB_VERSION_7) {
                printf("buttons: this driver knows version 5 of the arbiter only: no buttons\n");
                goto out;
        }
        if (channels == 0 || channels > ARB_MAX_CHANNELS) {
                channels = ARB_MAX_CHANNELS;
        }
        if (steps < 2) {
                goto out;
        }

        printf("buttons: step 2: look through the arbiter's table for the channels of the two peripherals\n");
        find_channel(&pon, channels);
        find_channel(&pin, channels);
        if (steps < 3) {
                goto out;
        }

        printf("buttons: step 3: the first transfers on the SPMI bus\n");
        pon_known = identify(&pon, PON_TYPE, PON_SUBTYPE_PRIMARY, PON_SUBTYPE_GEN3_HLOS + 1);
        if (pon_known) {
                /* remember the subtype for the generation's status bits */
                uint8_t is[2];
                if (spmi_read(&pon, REG_TYPE, is, 2) == NULL) {
                        pon_subtype = is[1];
                }
        }
        pin_known = identify(&pin, GPIO_TYPE, GPIO_SUBTYPE_LV, GPIO_SUBTYPE_MV_VIN3_CLK);
        if (steps < 4) {
                goto out;
        }

        printf("buttons: step 4: how the keys' inputs are set up\n");
        if (pon_known) {
                pon_inputs(pon_subtype);
        }
        if (pin_known) {
                pin_input();
        }
        if (!power.usable && !volume_up.usable) {
                printf("buttons: nothing that can be read: no buttons\n");
                goto out;
        }
        if (power.last || volume_down.last || volume_up.last) {
                printf("buttons: a key reads as held before anything was watched:%s%s%s. It is not reported until it has been "
                    "seen released\n", power.last ? " power" : "", volume_up.last ? " volume-up" : "",
                    volume_down.last ? " volume-down" : "");
        }
        /* (a key that is released now is believed at once) */
        power.live = power.usable && !power.last;
        volume_down.live = volume_down.usable && !volume_down.last;
        volume_up.live = volume_up.usable && !volume_up.last;
        if (steps < 5) {
                goto out;
        }

        printf("buttons: step 5: watching%s%s%s, every %u ms: power is the side button\n", power.usable ? " power" : "",
            volume_up.usable ? " volume-up" : "", volume_down.usable ? " volume-down" : "", POLL_MS);
        for (unsigned tick = 1;; tick++) {
                buttons_poll();
                /* once early on as well: a short run is to say what the reads cost too */
                if (tick % MINUTE_TICKS == 0 || tick == 10 * 1000 / POLL_MS) {
                        say_summary();
                }
                if (bus.failed >= 1000 && bus.failed * 2 > bus.reads) {
                        printf("buttons: more than half of %llu reads have failed: giving up\n", bus.reads);
                        break;
                }
                delay_for_interval(POLL_MS, NSEC_PER_MSEC);
        }
out:
        if (steps < 5) {
                printf("buttons: stopping here (buttons_steps=%u)\n", steps);
        }
        thread_terminate(current_thread());
}

void
raphael_buttons_init(void)
{
        uint32_t enabled = 0, steps = 5;
        thread_t thread;

        if (!PE_parse_boot_argn("buttons", &enabled, sizeof(enabled)) || !enabled) {
                return;
        }
        PE_parse_boot_argn("buttons_steps", &steps, sizeof(steps));
        if (steps == 0) {
                return;
        }
        if (kernel_thread_start(buttons_thread, (void *)(uintptr_t)steps, &thread) == KERN_SUCCESS) {
                thread_deallocate(thread);
        }
}
