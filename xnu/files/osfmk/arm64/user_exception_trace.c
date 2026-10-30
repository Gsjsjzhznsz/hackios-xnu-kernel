/*
 * Bring-up aid: describe every fatal-looking exception a user thread takes,
 * before it is delivered. There is no crash reporter in this system, so this
 * is the only place a crash is recorded.  Since R44 the trace is ON by default
 * (boot-arg systrace=0 silences it again): the R43 window lost six daemons to
 * a SIGBUS wave -- 194 deaths, one shared-cache instruction at unslid
 * 0x1a45c313c, one process-independent data address 0x2681bfd34 -- while the
 * only per-death recorder that sees every process (this one) sat behind the
 * default-off switch, and the R43 cmdline kept the R39 form verbatim, so
 * nothing kernel-side was ever written down.
 *
 * Besides the registers it prints the shared cache slide (most crashes are
 * inside system libraries: subtract the slide and look the address up in
 * the cache with "ipsw dyld a2s"), any text a register points at (the
 * library crash helpers leave their message in one) and the return
 * addresses found by walking the frame pointers.
 */

#include <arm/thread.h>
#include <kern/debug.h>
#include <kern/misc_protos.h>
#include <kern/startup.h>
#include <kern/task.h>
#include <kern/thread.h>
#include <mach/exception_types.h>
#include <mach/machine/thread_status.h>
#include <string.h>
#include <vm/vm_map.h>
#include <vm/vm_shared_region.h>

extern char *proc_name_address(struct proc *p);
extern int loader_log_flush(void);             /* osfmk/arm64/loader_return.c, the R42 verdict's flush */

void user_exception_trace(exception_type_t exception, mach_exception_data_t code, mach_msg_type_number_t count, thread_t thread);

/*
 * R44: the wave dies about once per 10 s per daemon (launchd respawn), and
 * this trace fires once per death -- the R42 partition law says a respawn
 * loop must not eat the logdump partition.  Per process name: the first 4
 * deaths trace in full, then only every 16th does (the pager convict's
 * heartbeat precedent).  The wave has six killer names, so the 8-slot table
 * carries it with room to spare; a ninth dying name stays silent rather
 * than starving the named ones.
 */
static struct {
        char name[32];
        unsigned count;
} user_trace_budget[8];
static unsigned user_trace_budget_count;

static bool
user_trace_admitted(const char *name)
{
        unsigned i;

        for (i = 0; i < user_trace_budget_count; i++) {
                if (strncmp(user_trace_budget[i].name, name, sizeof(user_trace_budget[i].name)) == 0) {
                        break;
                }
        }
        if (i == sizeof(user_trace_budget) / sizeof(user_trace_budget[0])) {
                return false;
        }
        if (i == user_trace_budget_count) {
                strlcpy(user_trace_budget[i].name, name, sizeof(user_trace_budget[i].name));
                user_trace_budget_count++;
        }
        user_trace_budget[i].count++;
        return user_trace_budget[i].count <= 4 || user_trace_budget[i].count % 16 == 0;
}

/* R44: on by default -- the retracted sandbox attribution left the wave
 * unconvicted, and this is the one recorder every death crosses (it sits at
 * exception_triage_thread(), before any handler search).  systrace=0 turns
 * it off again without touching the cmdline laws. */
static TUNABLE(uint32_t, user_exception_trace_enabled, "systrace", 1);

/*
 * R48: THE CENSUS.  The R47 boot proved the channel lives (ten kernel-asked
 * flushes rode the logdump whole) and the hooks sit in the flashed binary
 * (every funnel's BL verified), yet not one recorder line appeared -- so the
 * gates themselves are under arrest.  These plain counters tick BEFORE every
 * gate, and the adreno minute line sings them on the one channel proven to
 * print and flush (sigcensus, raphael_adreno.c); the first four births print
 * regardless of every gate (no flush -- the early ring is quiet and the
 * two-second notes carry them).  The next boot can tell "the wire never ran"
 * apart from "the wire ran and the line died" -- the one distinction four
 * rounds of silence could not make.  Non-static: the census reads them.
 */
unsigned long long r48_sigbirth_seen, r48_sigbirth_printed, r48_sigbirth_switch_denied, r48_sigbirth_budget_denied;
unsigned long long r48_usertrap_seen, r48_usertrap_printed;
unsigned long long r48_sigpost_seen, r48_sigpost_printed;
unsigned long long r48_userabort_seen, r48_fault_seen;

/* R48: the floor's census call -- sleh.c's handle_user_abort counts every
 * user abort here, silently, before anything decides what the fault was. */
void
redwood_fault_seen(void)
{
        r48_fault_seen++;
}

/*
 * R49: THE FUNNEL RE-MOUNTED AT THE PHYSICAL EXITS.  The R48 boot closed
 * the census: 128,204 user aborts rode handle_user_abort, 1,059 vm_fault
 * failures left vm_fault_internal (the pager verdicts), and ZERO user
 * exceptions ever reached the triage funnel (traps 0/0).  Pristine sleh.c
 * ends handle_user_abort in `exception_triage(exc, codes, numcodes)` --
 * a failed USER fault must deliver.  So the 1,059 were (a) kernel copyio
 * aborts recovered to EFAULT (a path with no delivery at all), or (b)
 * user faults that never made it to the delivery line, or (c) user
 * faults whose delivery the funnel could not see -- and pristine
 * exception.c routes exception_triage straight into
 * exception_triage_thread, where the R44 hook sits.  The R49 knife mounts
 * the census on handle_user_abort's REAL exits so the singer's arithmetic
 * closes the question in one boot:
 *
 *   faults (the R48 floor census, every entry)
 *     = redwood_user_fault_exit's share (the page-ins that succeeded or
 *       were aborted -- counted, never printed: 127K quiet successes)
 *     + redwood_user_fault_align's share (emulated alignment aborts)
 *     + redwood_user_fault_deliver's share (the delivery line: every
 *       genuinely delivered user exception, with its full hologram)
 *     + the rare parity/else shares (pristine panics or KERN_FAILURE).
 *
 * redwood_user_fault_deliver IS the R48 CR3 verdict's own prescription
 * ("next round records far/esr at sleh"): it receives the abort's real
 * registers, the real ESR (ec + fsc + the write bit) and the fault's
 * requested access -- the first eight deliveries print in full (and
 * flush), then one in every thirty-two keeps the heartbeat.  The verdicts
 * minus this counter names the kernel-side (copyio EFAULT) share of the
 * vm_fault failures; the top16/low48 decomposition and the entry
 * protection rides in the pager verdict (redwood_fault_convict.c).
 * Non-static: the adreno minute line reads the counters.
 */
unsigned long long r49_uf_ok, r49_uf_aborted, r49_uf_align, r49_uf_delivered;

static unsigned long long r49_deliver_lines;

static const char *
r49_ec_text(unsigned int ec)
{
        switch (ec) {
        case 0x20: return "IABORT_EL0";
        case 0x21: return "IABORT_EL1";
        case 0x24: return "DABORT_EL0";
        case 0x25: return "DABORT_EL1";
        case 0x22: return "PC_ALIGN";
        case 0x26: return "SP_ALIGN";
        case 0x2f: return "SERROR";
        default:   return "ec?";
        }
}

static const char *
r49_fsc_text(unsigned int fsc)
{
        switch (fsc & 0x3f) {
        case 0x04: return "translation L0";
        case 0x05: return "translation L1";
        case 0x06: return "translation L2";
        case 0x07: return "translation L3";
        case 0x09: case 0x0a: case 0x0b: return "access-flag";
        case 0x0d: return "permission L1";
        case 0x0e: return "permission L2";
        case 0x0f: return "permission L3";
        case 0x10: return "sync-external";
        case 0x21: return "alignment";
        case 0x22: return "debug";
        case 0x30: return "tlb-conflict";
        default:   return "fsc?";
        }
}

/* sleh.c handle_user_abort: the page-in worked, or the page-in was
 * interrupted (KERN_ABORTED -- a silent pristine return with NO delivery:
 * if the R48-era storms page in under signal pressure, this counter is
 * where it shows).  Called at the pristine `return` only -- 127K boots'
 * worth of successes stay a counter, never a line. */
void
redwood_user_fault_exit(kern_return_t result)
{
        if (result == KERN_ABORTED) {
                r49_uf_aborted++;
        } else {
                r49_uf_ok++;
        }
}

/* sleh.c handle_user_abort: the alignment abort emulated (or redrives
 * after a copyin failure) -- thread_exception_return, no delivery. */
void
redwood_user_fault_align(void)
{
        r49_uf_align++;
}

/* sleh.c handle_user_abort, at the delivery line: every user exception
 * that genuinely leaves for exception_triage counts here, the first
 * eight with the full hologram.  This is the trap funnel's re-mount:
 * if lines print HERE while the R44 funnel still shows seen 0, the
 * disconnect is BELOW the delivery line; if this counter stays 0 while
 * the pager verdicts climb, the failures were never user aborts at all. */
void
redwood_user_fault_deliver(const arm_saved_state_t *state, unsigned int esr, uint64_t fault_addr,
    unsigned int fault_code, unsigned int fault_type, exception_type_t exc, uint64_t code0)
{
        thread_t thread = current_thread();
        task_t task;
        struct proc *proc;

        r49_uf_delivered++;
        if (r49_deliver_lines >= 8 && (r49_uf_delivered % 32) != 0) {
                return;
        }
        r49_deliver_lines++;
        task = get_threadtask(thread);
        proc = task ? get_bsdtask_info(task) : NULL;
        printf("r49-deliver: %s[%d] exc %d code 0x%llx: pc 0x%llx lr 0x%llx sp 0x%llx far 0x%llx"
            " esr 0x%x (ec 0x%x %s, fsc 0x%x %s, write %d), ask 0x%x\n",
            proc ? proc_name_address(proc) : "?", proc ? proc_pid(proc) : -1,
            exc, (unsigned long long) code0,
            state ? (unsigned long long) get_saved_state_pc(state) : 0,
            state ? (unsigned long long) get_saved_state_lr(state) : 0,
            state ? (unsigned long long) get_saved_state_sp(state) : 0,
            (unsigned long long) fault_addr, esr,
            (esr >> 26) & 0x3f, r49_ec_text((esr >> 26) & 0x3f),
            fault_code & 0x3f, r49_fsc_text(fault_code),
            (int) ((esr >> 6) & 1), fault_type);
        (void) loader_log_flush();
}

static bool
plausible_user_address(uint64_t address)
{
        return address >= 0x100000000ULL && address < MACH_VM_MAX_ADDRESS;
}

void
user_exception_trace(exception_type_t exception, mach_exception_data_t code, mach_msg_type_number_t count, thread_t thread)
{
        static const unsigned message_registers[] = { 0, 1, 2, 3, 8, 16 };
        const arm_saved_state_t *state;
        vm_shared_region_t region;
        uint64_t frame[2], fp;
        uint32_t slide = 0;
        task_t task;
        struct proc *proc;

        r48_usertrap_seen++;
        if (!user_exception_trace_enabled || thread != current_thread()) {
                return;
        }
        /* R44: EXC_RESOURCE / EXC_GUARD join the census -- if the SIGBUS wave is a
         * resource/monitor exception (the retracted R28d lead), the code pair names
         * the flavor right here instead of staying a count-coincidence theory. */
        if (exception != EXC_BAD_ACCESS && exception != EXC_BAD_INSTRUCTION && exception != EXC_ARITHMETIC &&
            exception != EXC_BREAKPOINT && exception != EXC_RESOURCE && exception != EXC_GUARD) {
                return;
        }
        task = get_threadtask(thread);
        state = get_user_regs(thread);
        if (task == kernel_task || state == NULL || !is_saved_state64(state)) {
                return;
        }
        region = vm_shared_region_get(task);
        if (region != NULL) {
                slide = region->sr_slide;
                vm_shared_region_deallocate(region);
        }
        proc = get_bsdtask_info(task);
        if (!user_trace_admitted(proc ? proc_name_address(proc) : "?")) {
                return;
        }

        printf("usertrap: %s[%d] exception %d code 0x%llx 0x%llx: pc 0x%llx lr 0x%llx sp 0x%llx, shared cache slide 0x%x\n",
            proc ? proc_name_address(proc) : "?", proc ? proc_pid(proc) : -1, exception,
            count > 0 ? (unsigned long long)code[0] : 0, count > 1 ? (unsigned long long)code[1] : 0,
            (unsigned long long)get_saved_state_pc(state), (unsigned long long)get_saved_state_lr(state),
            (unsigned long long)get_saved_state_sp(state), slide);
        r48_usertrap_printed++;

        for (unsigned i = 0; i < sizeof(message_registers) / sizeof(message_registers[0]); i++) {
                uint64_t value = (uint64_t)get_saved_state_reg(state, message_registers[i]);
                char text[128];
                vm_size_t length = 0;
                bool printable = true;

                if (!plausible_user_address(value)) {
                        continue;
                }
                bzero(text, sizeof(text));
                (void)copyinstr((user_addr_t)value, text, sizeof(text) - 1, &length);
                for (unsigned c = 0; c < 6; c++) {
                        printable = printable && text[c] >= 0x20 && text[c] < 0x7f;
                }
                if (printable) {
                        printf("usertrap:   x%u -> \"%s\"\n", message_registers[i], text);
                }
        }

        fp = (uint64_t)get_saved_state_fp(state);
        for (int depth = 0; depth < 10 && plausible_user_address(fp) && (fp & 7) == 0; depth++) {
                if (copyin((user_addr_t)fp, frame, sizeof(frame)) != 0) {
                        break;
                }
                printf("usertrap:   called from 0x%llx\n", (unsigned long long)frame[1]);
                if (frame[0] <= fp) {
                        break;
                }
                fp = frame[0];
        }
        (void) loader_log_flush();
}

/*
 * R45: the signal funnel.  The R44 boot proved the wave never takes a
 * kernel-visible fault -- zero usertrap lines under a recorder proven live,
 * enabled and sitting at the one hub -- while 108 SIGBUS deaths ran, all
 * backboardd.  The wave travels the SIGNAL path: psignal -> bsd_ast ->
 * issignal_locked -> bsd_exception(EXC_SOFT_SIGNAL, signum), launchd's
 * sigexc port at the end -- a path that never reaches exception_triage
 * (the release kernel's call map: _exception_triage_thread's only caller is
 * the corpse path, _bsd_exception delivers directly).  So the recorder
 * moves to the funnel itself: one throttled line per delivered signal,
 * naming who died and what was delivered -- for the wave, exception 3
 * (EXC_SOFTWARE), code 0x10003 (EXC_SOFT_SIGNAL), subcode = the signal
 * number.  The same budget as the fault trace, the same switch: systrace=0
 * silences both.  The exception_triage wire stays: genuine faults still
 * trace there in full.
 */
void sigpost_trace(exception_type_t exception, mach_exception_data_t code, mach_msg_type_number_t count, thread_t thread);

void
sigpost_trace(exception_type_t exception, mach_exception_data_t code, mach_msg_type_number_t count, thread_t thread)
{
        task_t task;
        struct proc *proc;

        r48_sigpost_seen++;
        if (!user_exception_trace_enabled || thread != current_thread()) {
                return;
        }
        task = get_threadtask(thread);
        if (task == kernel_task) {
                return;
        }
        proc = get_bsdtask_info(task);
        if (!user_trace_admitted(proc ? proc_name_address(proc) : "?")) {
                return;
        }
        printf("sigpost: %s[%d] exception %d code 0x%llx 0x%llx\n",
            proc ? proc_name_address(proc) : "?", proc ? proc_pid(proc) : -1, exception,
            count > 0 ? (unsigned long long)code[0] : 0, count > 1 ? (unsigned long long)code[1] : 0);
        r48_sigpost_printed++;
        (void) loader_log_flush();
}


/*
 * R46: the signal BIRTH ledger.  The R45 boot answered the funnel question
 * and opened a deeper one: zero sigpost lines under the recorder proven
 * live, enabled and sitting at bsd_exception() -- while the wave GREW
 * (144 deaths: lsd x108 + analyticsd x28 + backboardd x8, a fresh victim
 * set every boot) and SpringBoard composed its first real frames (flip 2/3,
 * the white apple -- a stage R43/R44 never reached).  Silence at the funnel
 * means nothing crossed it: no signal was ever DELIVERED.  And the base
 * source now kills the R45 terminal theory: proc_terminate() sends only
 * memorystatus_on_terminate(p)'s verdict (SIGKILL/SIGTERM) -- no
 * user-supplied signal number rides proc_info.  The one birth funnel that
 * explains BOTH silences (usertrap AND sigpost) is the ux_handler: a
 * faulting thread's Mach exception caught by the task's own exception-
 * handler thread (the logdump's "exc handler[own pid]" is literal -- the
 * handler IS in the dying process), translated by ux_exception()
 * (EXC_BAD_ACCESS with KERN_PROTECTION_FAILURE -> SIGBUS) and posted with
 * threadsignal(.., TRUE) -- a path that never calls psignal, never produces
 * a bsd_exception(EXC_SOFT_SIGNAL) delivery, and never reaches
 * exception_triage.  So the recorder moves UPSTREAM to the births: one
 * throttled line per signal born -- the target, the signal, the code, the
 * birth site (the caller's return address, unslid, straight against the
 * release symtab) and who is running.  Wired (redwood.patch) at psignal,
 *
 * R47: THE FLUSH LAW.  The R46 boot answered the channel question: the
 * loader reported the round's own kernel (size 0x1038b00, the release byte
 * count), launchd recorded the wave at full strength (lsd x63 + analyticsd
 * x8, every death "exited due to SIGBUS | sent by exc handler[own pid]" --
 * the ux_handler birth the ledger was built to catch), and not one
 * sigbirth line made the logdump.  Not because the recorder died: the R42
 * verdict proved the way out -- printf only lands in the kernel message
 * buffer, and the buffer is a RING the adreno poller rewrites at ~8 lines
 * a second, so by the time the logdump is taken the wave's lines are
 * overwritten (the R46 snapshot holds 690 lines: 666 of them the adreno
 * poller's).  The R42 verdict survived because it calls loader_log_flush()
 * after its last printf -- the flush asks the loader to write the buffer
 * out, and the flushed lines ride the logdump for good.  So every recorder
 * here now ends with the same flush call: usertrap, sigpost, sigbirth,
 * userabort.  The budget throttles the lines, so it throttles the flushes
 * too -- the R42 partition law holds (78 flushes ran through R42's boot
 * without eating the partition).
 * psignal_with_reason, psignal_uthread (pthread_kill -- the one
 * user-directed thread funnel) and threadsignal (the ux_handler's own
 * mouth, handle_ux_exception+0x10c in the R45 autopsy).
 * psignal_locked / psignal_try_thread / psignal_vfork /
 * psignal_thread_with_reason stay unhooked: their signal numbers are
 * compile-time constants (SIGILL/SIGKILL/SIGTRAP/SIGVTALRM/SIGPROF/
 * SIGXCPU), none can carry the wave; proc_terminate rides through its own
 * psignal call (the birth line's site reads _proc_terminate, the "by"
 * field names the requester).  The same systrace switch silences the whole
 * family; the budget is a FRESH (name, signal) table -- a launchd SIGCHLD
 * storm must not eat the wave's lines the way a per-name table would.
 */
void sigbirth_trace(struct proc *p, thread_t thread, int signum, uint64_t code, void *from);

static struct {
        char name[32];
        int signal;
        unsigned count;
} sigbirth_budget[24];
static unsigned sigbirth_budget_count;

static bool
sigbirth_admitted(const char *name, int signal)
{
        unsigned i;

        for (i = 0; i < sigbirth_budget_count; i++) {
                if (sigbirth_budget[i].signal == signal &&
                    strncmp(sigbirth_budget[i].name, name, sizeof(sigbirth_budget[i].name)) == 0) {
                        break;
                }
        }
        if (i == sizeof(sigbirth_budget) / sizeof(sigbirth_budget[0])) {
                return false;
        }
        if (i == sigbirth_budget_count) {
                strlcpy(sigbirth_budget[i].name, name, sizeof(sigbirth_budget[i].name));
                sigbirth_budget[i].signal = signal;
                sigbirth_budget_count++;
        }
        sigbirth_budget[i].count++;
        return sigbirth_budget[i].count <= 4 || sigbirth_budget[i].count % 16 == 0;
}

void
sigbirth_trace(struct proc *p, thread_t thread, int signum, uint64_t code, void *from)
{
        task_t task;
        struct proc *who, *self;

        /* R48: the census ticks and the live probe fire before every gate. */
        r48_sigbirth_seen++;
        if (r48_sigbirth_seen <= 4) {
                printf("siglive: birth %llu target %s[%d] sig %d from 0x%llx\n",
                    r48_sigbirth_seen, p ? proc_name_address(p) : "?",
                    p ? proc_pid(p) : -1, signum, (unsigned long long)(uintptr_t)from);
        }
        if (!user_exception_trace_enabled) {
                r48_sigbirth_switch_denied++;
                return;
        }
        who = p;
        if (who == NULL && thread != NULL) {
                task = get_threadtask(thread);
                if (task == NULL || task == kernel_task) {
                        return;
                }
                who = get_bsdtask_info(task);
        }
        if (who == NULL || !sigbirth_admitted(proc_name_address(who), signum)) {
                r48_sigbirth_budget_denied++;
                return;
        }
        self = get_bsdtask_info(current_task());
        printf("sigbirth: %s[%d] signal %d code 0x%llx from 0x%llx by %s[%d]\n",
            proc_name_address(who), proc_pid(who), signum, (unsigned long long)code,
            (unsigned long long)(uintptr_t)from,
            self ? proc_name_address(self) : "?", self ? proc_pid(self) : -1);
        r48_sigbirth_printed++;
        (void) loader_log_flush();
}

/*
 * A program calls abort(): say which, and from where. The reason is usually an exception nobody
 * caught, or an assertion; the text of it goes to the program's standard error (boot-arg applog=1
 * puts that on the console), the stack is here.
 */
void user_abort_trace(void);

void
user_abort_trace(void)
{
        thread_t thread = current_thread();
        task_t task = get_threadtask(thread);
        const arm_saved_state_t *state = get_user_regs(thread);
        struct proc *proc = get_bsdtask_info(task);
        vm_shared_region_t region;
        uint64_t frame[2], fp;
        uint32_t slide = 0;

        r48_userabort_seen++;
        if (task == kernel_task || state == NULL || !is_saved_state64(state)) {
                return;
        }
        region = vm_shared_region_get(task);
        if (region != NULL) {
                slide = region->sr_slide;
                vm_shared_region_deallocate(region);
        }
        printf("userabort: %s[%d] aborts, shared cache slide 0x%x; called from:", proc ? proc_name_address(proc) : "?",
            proc ? proc_pid(proc) : -1, slide);
        fp = (uint64_t)get_saved_state_fp(state);
        for (int depth = 0; depth < 24 && plausible_user_address(fp) && (fp & 7) == 0; depth++) {
                if (copyin((user_addr_t)fp, frame, sizeof(frame)) != 0) {
                        break;
                }
                printf(" 0x%llx", (unsigned long long)frame[1]);
                if (frame[0] <= fp) {
                        break;
                }
                fp = frame[0];
        }
        printf("\n");
        (void) loader_log_flush();
}

/*
 * Boot-args ustacks=NAME ustacks_at=SECONDS [ustacks_ms=MILLISECONDS]: that long after boot
 * (and then periodically) print where every thread of every process called NAME is -- pc, lr
 * and the return addresses up the stack. Shows what a daemon that "does nothing" waits for.
 * ustacks_now(NAME) does it at once: under QEMU, "qemu/touch.py stacks NAME" on the host.
 * The addresses are symbolized on the host (qemu/symstack.py).
 * The name "top" asks for something else: who has had the processor (see ustacks_top()).
 */
#include <kern/recount.h>
#include <kern/thread_call.h>
#include <kern/clock.h>
#include <kern/queue.h>
#include <pexpert/pexpert.h>

extern queue_head_t tasks;
extern lck_mtx_t tasks_threads_lock;

#define USTACKS_MAX_THREADS 96
#define USTACKS_MAX_FRAMES  14

static char ustacks_name[32];
static uint32_t ustacks_every;
static uint32_t ustacks_threads = USTACKS_MAX_THREADS;  /* boot-arg ustacks_threads=N: only the first N (1: the main thread) */
/*
 * Boot-arg ustacks_tname=TEXT: only the threads whose name has TEXT in it (ustacks_tname=coreanimation:
 * the window server's render-server and its other threads), each line with the thread's name and, in
 * the first, the time in microseconds of mach_absolute_time (as the "tl:" lines of tools/latency.py);
 * the lines are few enough then to go to the loader's log as well, where all of a long run is kept.
 * ustacks_tname=* takes every thread, named or not, and sends them to the loader's log too.
 */
static char ustacks_tname[32];
static uint32_t ustacks_count;          /* boot-arg ustacks_count=N: so many times, then no more (0: for ever) */
static thread_call_t ustacks_call;

extern void bsd_getthreadname(void *uth, char *buffer);
extern void loader_log_line(const char *line);

static void
ustacks_say(const char *line)
{
        printf("%s", line);
        if (ustacks_tname[0] != 0) {
                loader_log_line(line);
        }
}

static void
ustacks_task(task_t task, struct proc *proc)
{
        static struct {
                uint64_t tid, pc, lr, fp;
                int state;
                char name[64];          /* (bsd_getthreadname() copies MAXTHREADNAMESIZE, 64) */
        } snap[USTACKS_MAX_THREADS];
        char line[512];
        int at;
        vm_shared_region_t region;
        vm_map_t map, old_map;
        thread_t thread;
        uint32_t slide = 0;
        unsigned count = 0;

        task_lock(task);
        queue_iterate(&task->threads, thread, thread_t, task_threads) {
                const arm_saved_state_t *state = get_user_regs(thread);

                if (count == USTACKS_MAX_THREADS || count >= ustacks_threads || state == NULL || !is_saved_state64(state)) {
                        continue;
                }
                snap[count].name[0] = 0;
                if (get_bsdthread_info(thread) != NULL) {
                        bsd_getthreadname(get_bsdthread_info(thread), snap[count].name);
                        snap[count].name[63] = 0;
                }
                if (ustacks_tname[0] != 0 && strcmp(ustacks_tname, "*") != 0 &&
                    strnstr(snap[count].name, ustacks_tname, sizeof(snap[count].name)) == NULL) {
                        continue;
                }
                snap[count].tid = thread_tid(thread);
                snap[count].state = thread->state;
                snap[count].pc = get_saved_state_pc(state);
                snap[count].lr = get_saved_state_lr(state);
                snap[count].fp = get_saved_state_fp(state);
                count++;
        }
        map = task->map;
        if (map != VM_MAP_NULL) {
                vm_map_reference(map);
        }
        task_unlock(task);
        if (map == VM_MAP_NULL) {
                return;
        }
        region = vm_shared_region_get(task);
        if (region != NULL) {
                slide = region->sr_slide;
                vm_shared_region_deallocate(region);
        }
        clock_sec_t secs;
        clock_usec_t usecs;

        uint64_t nanos;

        clock_get_system_microtime(&secs, &usecs);
        absolutetime_to_nanoseconds(mach_absolute_time(), &nanos);
        snprintf(line, sizeof(line), "ustacks: %s[%d] %u threads, shared cache slide 0x%x, uptime %lu.%03u, at %llu\n",
            proc_name_address(proc), proc_pid(proc), count, slide, (unsigned long)secs, (unsigned)usecs / 1000, nanos / 1000);
        ustacks_say(line);

        old_map = vm_map_switch(map);
        for (unsigned i = 0; i < count; i++) {
                uint64_t frame[2], fp = snap[i].fp;

                /* (a thread that runs shows where it last came into the kernel, not where it is) */
                at = snprintf(line, sizeof(line), "ustacks:  thread 0x%llx%s%s%s%s: 0x%llx 0x%llx", (unsigned long long)snap[i].tid,
                    snap[i].name[0] ? " \"" : "", snap[i].name, snap[i].name[0] ? "\"" : "",
                    (snap[i].state & TH_RUN) ? " (running)" : "", (unsigned long long)snap[i].pc, (unsigned long long)snap[i].lr);
                for (int depth = 0; depth < USTACKS_MAX_FRAMES && plausible_user_address(fp) && (fp & 7) == 0; depth++) {
                        if (copyin((user_addr_t)fp, frame, sizeof(frame)) != 0) {
                                break;
                        }
                        if (at < (int)sizeof(line) - 24) {
                                at += snprintf(line + at, sizeof(line) - at, " 0x%llx", (unsigned long long)frame[1]);
                        }
                        if (frame[0] <= fp) {
                                break;
                        }
                        fp = frame[0];
                }
                snprintf(line + at, sizeof(line) - at, "\n");
                ustacks_say(line);
        }
        vm_map_switch(old_map);
        vm_map_deallocate(map);
}

/*
 * ustacks_now("top"): the processes that have had the processor since this was last asked (the
 * first time: since boot), each with its share of that time. A system that feels slow is either
 * busy -- this says with what -- or waiting for something, and the two look the same on the screen.
 */
#define USTACKS_TOP_TASKS 512
#define USTACKS_TOP_LINES 12

static void
ustacks_top(void)
{
        static struct {
                int pid;
                uint64_t total;                 /* user + system time so far, in Mach time units */
                uint64_t used;                  /* ... of it since the last time */
                char name[33];                  /* as long as a process name gets (2 * MAXCOMLEN + 1) */
        } now[USTACKS_TOP_TASKS];
        static struct {
                int pid;
                uint64_t total;
        } before[USTACKS_TOP_TASKS];
        static unsigned before_count;
        static uint64_t before_when;
        uint64_t when = mach_absolute_time(), elapsed = when - before_when, elapsed_ns, busy = 0;
        unsigned count = 0;
        task_t task;

        lck_mtx_lock(&tasks_threads_lock);
        queue_iterate(&tasks, task, task_t, tasks) {
                struct proc *proc = get_bsdtask_info(task);
                struct recount_times_mach times;

                if (count == USTACKS_TOP_TASKS || proc == NULL) {
                        continue;
                }
                times = recount_task_times(task);
                now[count].pid = proc_pid(proc);
                now[count].total = times.rtm_user + times.rtm_system;
                strlcpy(now[count].name, proc_name_address(proc), sizeof(now[count].name));
                count++;
        }
        lck_mtx_unlock(&tasks_threads_lock);

        /* a process that was not there the last time has used all it has since */
        for (unsigned i = 0; i < count; i++) {
                uint64_t earlier = 0;

                for (unsigned j = 0; j < before_count; j++) {
                        if (before[j].pid == now[i].pid) {
                                earlier = before[j].total;
                                break;
                        }
                }
                now[i].used = now[i].total - MIN(earlier, now[i].total);
                busy += now[i].used;
        }
        absolutetime_to_nanoseconds(elapsed, &elapsed_ns);
        printf("top: in the last %llu ms the processor was busy %llu%% of the time\n", elapsed_ns / NSEC_PER_MSEC,
            elapsed ? busy * 100 / elapsed : 0);
        for (unsigned i = 0; i < count; i++) {
                before[i].pid = now[i].pid;
                before[i].total = now[i].total;
        }
        before_count = count;
        before_when = when;
        for (unsigned line = 0; line < USTACKS_TOP_LINES && count != 0 && elapsed != 0; line++) {
                unsigned most = 0;

                for (unsigned i = 1; i < count; i++) {
                        if (now[i].used > now[most].used) {
                                most = i;
                        }
                }
                if (now[most].used * 1000 / elapsed == 0) {
                        break;
                }
                printf("top:  %3llu.%llu%%  %s[%d]\n", now[most].used * 100 / elapsed, now[most].used * 1000 / elapsed % 10,
                    now[most].name, now[most].pid);
                now[most].used = 0;
        }
}

static void
ustacks_fire(__unused thread_call_param_t p0, __unused thread_call_param_t p1)
{
        static task_t found[8];
        unsigned count = 0;
        task_t task;

        if (strcmp(ustacks_name, "top") == 0) {
                ustacks_top();
                goto again;
        }
        lck_mtx_lock(&tasks_threads_lock);
        queue_iterate(&tasks, task, task_t, tasks) {
                struct proc *proc = get_bsdtask_info(task);

                if (count < 8 && proc != NULL && strncmp(proc_name_address(proc), ustacks_name, sizeof(ustacks_name)) == 0) {
                        task_reference(task);
                        found[count++] = task;
                }
        }
        lck_mtx_unlock(&tasks_threads_lock);
        if (count == 0) {
                printf("ustacks: no process called %s\n", ustacks_name);
        }
        for (unsigned i = 0; i < count; i++) {
                struct proc *proc = get_bsdtask_info(found[i]);

                if (proc != NULL) {
                        ustacks_task(found[i], proc);
                }
                task_deallocate(found[i]);
        }
again:
        if (ustacks_count != 0 && --ustacks_count == 0) {
                return;
        }
        if (ustacks_every != 0) {
                uint64_t deadline;

                clock_interval_to_deadline(ustacks_every, NSEC_PER_MSEC, &deadline);
                thread_call_enter_delayed(ustacks_call, deadline);
        }
}

/* The same at once, for whoever asks (a debugging key sequence, see bsd/dev/redwood_virtio_input.c). */
void ustacks_now(const char *name);

void
ustacks_now(const char *name)
{
        strlcpy(ustacks_name, name, sizeof(ustacks_name));
        if (ustacks_call == NULL) {
                ustacks_call = thread_call_allocate(ustacks_fire, NULL);
        }
        thread_call_enter(ustacks_call);
}

static void
ustacks_init(void)
{
        uint32_t at = 0;
        uint64_t deadline;

        if (!PE_parse_boot_argn("ustacks", ustacks_name, sizeof(ustacks_name)) ||
            !PE_parse_boot_argn("ustacks_at", &at, sizeof(at)) || at == 0) {
                return;
        }
        PE_parse_boot_argn("ustacks_ms", &ustacks_every, sizeof(ustacks_every));
        PE_parse_boot_argn("ustacks_threads", &ustacks_threads, sizeof(ustacks_threads));
        PE_parse_boot_argn("ustacks_tname", ustacks_tname, sizeof(ustacks_tname));
        PE_parse_boot_argn("ustacks_count", &ustacks_count, sizeof(ustacks_count));
        ustacks_call = thread_call_allocate(ustacks_fire, NULL);
        clock_interval_to_deadline(at, NSEC_PER_SEC, &deadline);
        thread_call_enter_delayed(ustacks_call, deadline);
}
STARTUP(EARLY_BOOT, STARTUP_RANK_LAST, ustacks_init);
