/*
 * R42: the pager forensics -- the fault names the file that died.
 *
 * The device verdict on the R41 run (4546 lines of logdump on the record)
 * put ONE crasher on the boot: com.apple.lsd died of SIGBUS 78 times in a
 * once-a-second respawn loop starting at 00:01:59.713, one frame after
 * backboardd's blocking LaunchServices database fetch (.712) failed the
 * same way ("Database mapping failed"); SpringBoard itself lived silent
 * to the end of the capture.  Two processes, one file: whatever kills
 * both lives under the mmap page-fill path (the vnode pager), and the
 * suspects are three -- a page-in the pager cannot serve, an extent that
 * reads as zeroes where the file's real bytes should be, or a size that
 * lies (a mapping past the file's end faults by definition).  The R40
 * GPU wall did not reproduce; this wall is new and it is ours to name.
 *
 * Every user page-fill failure in the kernel leaves through ONE exit:
 * vm_fault_internal()'s final return (osfmk/vm/vm_fault.c).  A failed
 * fault there is what a SIGBUS is made of in the BSD layer above.  So
 * the round's one knife is a call at that exit into this file: walk the
 * faulting address to its map entry, walk the entry's object chain down
 * to the file object, ask the vnode pager for the file's name, and put
 * the whole verdict on the loader log (printf lands in the kernel message
 * buffer, which the return-to-loader channel drains; the first verdicts
 * also ask loader_log_flush, the R41 channel, so the early ones reach the
 * logdump partition even if the boot never ends cleanly).
 *
 * Locks, R43 law: a probe may only use TRY-class primitives -- try-fail
 * means skip the deep look and say so, NEVER block, NEVER recurse.  The
 * R42 device verdict taught this the hard way: its loop pre-locked the
 * next shadow at the tail AND locked it again at the top, so the first
 * chain deeper than one object (SpringBoard's IOSurface shadow) took the
 * same object's exclusive lck_rw twice on one thread -- lock_rw.c:877,
 * "Lock already held, owner == self", SpringBoard panicked, screen black.
 * The fix is two knives in one file: every lock is a try (vm_map_try_
 * lock_read, vm_object_lock_try_scan -- both verified in the xnu-8796
 * sources to be non-blocking, assert-free try primitives), and each
 * chain level is locked exactly ONCE, at the top of its own iteration,
 * top-down (the collapse order, all levels held -- unchanged intent).
 * A busy lock prints a minimal verdict (pid/address/kr + where it gave
 * up) with no walk at all: the forensics must outlive the probe.
 *
 * The name buffer is allocated before the first lock is taken.  The
 * deepest object carrying a pager is the file; objects above it (copies,
 * shadows) are reported as sizes only.
 *
 * Throttle: lsd respawned once a second; a verdict per second with a
 * flush each would eat the logdump partition for no information -- the
 * file is named once and does not change.  The first R43_VERBOSE_FIRST
 * verdicts print in full (and flush); after that one full verdict every
 * R43_VERBOSE_EVERY-th failure keeps a heartbeat and the running count,
 * and the rest only tick the counter.  Everything is static, nothing is
 * tunable from the command line: this is a forensics round, not a policy.
 *
 * R49: THE HOLOGRAM.  The R48 boot settled the census: 128,204 user
 * aborts rode handle_user_abort (the floor census) while the triage
 * funnel at exception_triage_thread saw ZERO of them (traps 0/0), and
 * 1,059 vm_fault failures left this exit -- only 69 of them verbose
 * (the old 3/16 budget), in two shapes: kr 0x1 KERN_INVALID_ADDRESS on
 * addresses carrying a garbage top half (0xff7000010122c000 and friends,
 * the low 48 bits a plausible user address, across init/launchd/xpcproxy
 * during the exec storm) and kr 0x2 KERN_PROTECTION_FAILURE on read-only
 * Assets.car pages (SpringBoard's CoreGlyphs above all).  Pristine
 * sleh.c says a FAILED USER fault must deliver through exception_triage
 * -- so either these were never user aborts (kernel copyio EFAULTs on
 * user pointers, a path with no delivery and no triage), or something
 * between the fault and the delivery eats them.  The R49 knife names the
 * hand on every verdict: the requested access rides in (caller_prot,
 * the vm_fault_internal parameter the hook now forwards), each verdict
 * line names its kr, the no-map-entry verdict prints the map's shape
 * (entry count, span, first entry bounds, the address's top16/low48
 * decomposition) and the entry-found verdicts print the entry's own
 * protection against the ask, and every verbose verdict is preceded by
 * the faulting thread's upcb line (pc/lr/sp -- the USER state: for a
 * kernel copyio abort this is the syscall's userland caller, for a user
 * abort the faulting instruction itself).  The user-abort side of the
 * question is answered by the sleh.c exit census (redwood_user_fault_*,
 * user_exception_trace.c): failed user faults tick redwood_user_fault_
 * deliver on their way to exception_triage, so the singer's arithmetic
 * (verdicts - delivered) names the kernel-side share.  The budget lifts
 * with the detail: the first twelve verdicts print in full and one in
 * every eight after, still try-locked, still flushed.
 *
 * R50: THE REGISTER HOLOGRAM.  The R49 boot closed the R48 census: the
 * 128K user aborts are all SUCCESSFUL page-ins (uf-ok 119,646 of
 * 119,656, delivered 0 -- HR1 dead, HR2 proven), and all 1,063 failures
 * are kernel copyio EFAULTs that never rode a user abort at all.  The
 * verdicts split into two named shapes: kr 0x2 x111 = an ask read-write
 * against entry prot 0x1/7, the read-only Assets.car mapping used as a
 * SYSCALL BUFFER (the copyout destination), from one shared-cache PC
 * (0x20d6ff814), SpringBoard x72 + Spotlight x32; kr 0x1 x30 = garbage
 * top-half addresses on the exec path (argv copyin), callers in the
 * shared-cache exec shim x23 and init x6.  Both shapes are syscalls;
 * the missing hand is the syscall ITSELF.  The upcb is the SVC instant:
 * on the ARM64 ABI x16 carries the syscall number untouched through the
 * SVC and x0..x5 the first six arguments, so one extra line per verbose
 * verdict names the hand -- fd/buf/len for the write-on-read-only
 * syscall (HR4), and for the exec verdicts x1 IS the argv array
 * address, separating an unmapped array from a mapped array full of
 * garbage pointers (HR3).  The accessor is the R49 deliver line's own
 * (get_saved_state_reg, already in tree); no new locks, no new exits,
 * the budget unchanged -- one line per verdict.
 */
#include <arm/thread.h>
#include <kern/kalloc.h>
#include <kern/misc_protos.h>
#include <kern/task.h>
#include <kern/thread.h>
#include <machine/atomic.h>
#include <mach/kern_return.h>
#include <mach/mach_types.h>
#include <mach/machine/thread_status.h>
#include <mach/vm_param.h>
#include <vm/vm_fault.h>
#include <vm/vm_kern.h>
#include <vm/vm_map.h>
#include <vm/vm_object.h>
#include <vm/vm_protos.h>

extern int loader_log_flush(void);              /* osfmk/arm64/loader_return.c */
extern int proc_selfpid(void);
extern char *proc_name_address(void *p);        /* the bsd proc's name buffer */
extern void *get_bsdtask_info(task_t task);     /* bsd/sys/proc.h (declared here: an osfmk file keeps to its own externs, the file's own precedent above) */

#define R43_PATH_MAX            1024
#define R43_VERBOSE_FIRST       12
#define R43_VERBOSE_EVERY       8
#define R43_SHADOW_MAX          4

static int r43_fault_verdicts;

/* R49: every vm_fault failure that reaches this exit, counted for the
 * singer's arithmetic (r49_verdicts - delivered = the kernel-side share
 * of the failures, the share that never rode a user abort).  Non-static:
 * the adreno minute line reads it. */
unsigned long long r49_verdicts;

void redwood_fault_convict(vm_map_t map, vm_map_offset_t vaddr, kern_return_t kr, vm_prot_t fault_type);

static const char *
kr_name(kern_return_t kr)
{
        switch (kr) {
        case KERN_INVALID_ADDRESS:      return "KERN_INVALID_ADDRESS";
        case KERN_PROTECTION_FAILURE:   return "KERN_PROTECTION_FAILURE";
        case KERN_NO_SPACE:             return "KERN_NO_SPACE";
        case KERN_INVALID_ARGUMENT:     return "KERN_INVALID_ARGUMENT";
        case KERN_FAILURE:              return "KERN_FAILURE";
        case KERN_MEMORY_ERROR:         return "KERN_MEMORY_ERROR";
        case KERN_MEMORY_PRESENT:       return "KERN_MEMORY_PRESENT";
        case KERN_ABORTED:              return "KERN_ABORTED";
        case KERN_RESOURCE_SHORTAGE:    return "KERN_RESOURCE_SHORTAGE";
        default:                        return "?";
        }
}

static const char *
fault_type_text(vm_prot_t prot)
{
        switch (prot & (VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE)) {
        case VM_PROT_READ:                              return "read";
        case VM_PROT_WRITE:                             return "write";
        case VM_PROT_EXECUTE:                           return "exec";
        case VM_PROT_READ | VM_PROT_WRITE:              return "read-write";
        case VM_PROT_READ | VM_PROT_EXECUTE:            return "read-exec";
        case VM_PROT_WRITE | VM_PROT_EXECUTE:           return "write-exec";
        case VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE: return "read-write-exec";
        case 0:                                         return "none";
        default:                                        return "mixed";
        }
}

void
redwood_fault_convict(vm_map_t map, vm_map_offset_t vaddr, kern_return_t kr, vm_prot_t fault_type)
{
        vm_map_entry_t          entry, first;
        vm_object_t             object;
        vm_object_t             chain[R43_SHADOW_MAX];
        vm_object_offset_t      sizes[R43_SHADOW_MAX];
        memory_object_t         pagers[R43_SHADOW_MAX];
        vm_object_offset_t      fileoff = 0;
        char                    *buf = NULL;
        char                    *pathname = NULL, *filename = NULL;
        const char              *procname = "?";
        boolean_t               truncated = FALSE;
        kern_return_t           name_kr = KERN_INVALID_ARGUMENT;
        int                     walk_depth = 0, named = -1, lock_busy_at = -1;
        int                     n, pid;
        bool                    verbose;

        (void) os_atomic_add(&r49_verdicts, 1, relaxed);
        pid = proc_selfpid();
        n = os_atomic_add(&r43_fault_verdicts, 1, relaxed);
        verbose = (n <= R43_VERBOSE_FIRST) || ((n % R43_VERBOSE_EVERY) == 0);
        if (!verbose) {
                return;
        }

        if (get_bsdtask_info(current_task()) != NULL) {
                procname = proc_name_address(get_bsdtask_info(current_task()));
        }

        /* R49: the hologram's user-state line, before any lock or sleep.
         * The upcb holds the thread's USER state even while the kernel
         * aborts inside copyio -- so this line names the userland hand:
         * the faulting instruction for a user abort, the syscall's
         * caller for a kernel copyio abort. */
        {
                thread_t thread = current_thread();
                const arm_saved_state_t *state = get_user_regs(thread);

                if (state != NULL && is_saved_state64(state)) {
                        printf("redwood-fault: verdict %d context: pc 0x%llx lr 0x%llx sp 0x%llx (the upcb's user state: the faulting instruction, or the syscall's caller when the abort was kernel-side)\n",
                            n, (uint64_t) get_saved_state_pc(state),
                            (uint64_t) get_saved_state_lr(state),
                            (uint64_t) get_saved_state_sp(state));
                        /* R50: the register hologram -- the upcb is the SVC
                         * instant: x16 carries the syscall number and x0..x5
                         * the first six arguments, the ARM64 ABI rides them
                         * through the SVC untouched and copyio runs on
                         * temporaries, never the upcb.  A kr 0x2 write on a
                         * read-only entry names its syscall's fd/buf/len --
                         * buf IS the copyout destination that died (HR4);
                         * a kr 0x1 exec garbage-top names x1 = the argv
                         * array address, separating an unmapped array from
                         * a mapped one full of garbage pointers (HR3). */
                        printf("redwood-fault: verdict %d registers: x0 0x%llx x1 0x%llx x2 0x%llx x3 0x%llx x4 0x%llx x5 0x%llx x16 0x%llx\n",
                            n, (uint64_t) get_saved_state_reg(state, 0),
                            (uint64_t) get_saved_state_reg(state, 1),
                            (uint64_t) get_saved_state_reg(state, 2),
                            (uint64_t) get_saved_state_reg(state, 3),
                            (uint64_t) get_saved_state_reg(state, 4),
                            (uint64_t) get_saved_state_reg(state, 5),
                            (uint64_t) get_saved_state_reg(state, 16));
                }
        }

        /* the name buffer before any lock: Z_WAITOK may sleep, and the locks
         * below must not be held while it does (the code-signing caller
         * allocates the same shape: two PATH_MAXs, the path and the name) */
        buf = kalloc_data(R43_PATH_MAX * 2, Z_WAITOK);
        if (buf != NULL) {
                pathname = buf;
                filename = buf + R43_PATH_MAX;
                pathname[0] = '\0';
                filename[0] = '\0';
        }

        /* try, never block: the failure exits above this call are reached
         * from caller states this probe cannot enumerate (wire paths hold
         * the map lock across their faults); busy = skip the deep look */
        if (!vm_map_try_lock_read(map)) {
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x (%s) ask %s: map lock busy, deep look skipped\n",
                    n, pid, procname, (uint64_t) vaddr, kr, kr_name(kr), fault_type_text(fault_type));
                if (buf != NULL) {
                        kfree_data(buf, R43_PATH_MAX * 2);
                }
                (void) loader_log_flush();
                return;
        }
        if (!vm_map_lookup_entry(map, vaddr, &entry) || entry->is_sub_map) {
                /* R49: the map's shape, while the read lock still holds --
                 * how many entries the map really has, where its span and
                 * first entry sit, and the faulting address split into the
                 * plausible low 48 bits and the top 16 (the R48 boot's
                 * 0xff7000010122c000 pattern: the low half a user address,
                 * the top half garbage). */
                first = (vm_map_entry_t) map->hdr.links.next;
                if (first == (vm_map_entry_t) &map->hdr.links) {
                        first = VM_MAP_ENTRY_NULL;
                }
                vm_map_unlock_read(map);
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x (%s) ask %s: no map entry"
                    " (the map holds %d entries spanning 0x%llx..0x%llx; its first entry covers 0x%llx..0x%llx;"
                    " low48 0x%llx, top16 0x%llx)\n",
                    n, pid, procname, (uint64_t) vaddr, kr, kr_name(kr), fault_type_text(fault_type),
                    map->hdr.nentries, (uint64_t) map->min_offset, (uint64_t) map->max_offset,
                    first != VM_MAP_ENTRY_NULL ? (uint64_t) first->vme_start : 0,
                    first != VM_MAP_ENTRY_NULL ? (uint64_t) first->vme_end : 0,
                    (uint64_t) (vaddr & 0xffffffffffffULL),
                    (uint64_t) ((vaddr >> 48) & 0xffffULL));
                if (buf != NULL) {
                        kfree_data(buf, R43_PATH_MAX * 2);
                }
                (void) loader_log_flush();
                return;
        }
        /* vme_offset is a bitfield member of vm_map_entry (xnu-8796: vme_start
         * through vme_end are macros over links.*, offset lives as vme_offset) */
        fileoff = entry->vme_offset + (vaddr - entry->vme_start);

        /* down the shadow chain, every level locked EXACTLY ONCE at the top
         * of its own iteration (the R42 loop pre-locked the next shadow at
         * the tail and locked it again at the top -- the same object's
         * exclusive lock twice on one thread, the panic lock_rw.c:877
         * named); all levels stay held, the collapse order preserved.  Try
         * class only: busy = unlock what is held, say so, walk away. */
        /* xnu-8796 packs the object into the entry: there is no entry->object
         * union any more -- VME_OBJECT() is the accessor (vm_map.h), it UNPACKs
         * vme_object_or_delta and asserts the entry is not a sub map (this
         * caller already returned on is_sub_map above) */
        object = VME_OBJECT(entry);
        while (object != VM_OBJECT_NULL && walk_depth < R43_SHADOW_MAX) {
                vm_object_t next;

                if (!vm_object_lock_try_scan(object)) {
                        lock_busy_at = walk_depth;
                        break;
                }
                chain[walk_depth] = object;
                sizes[walk_depth] = object->vo_size;
                pagers[walk_depth] = object->pager;
                next = object->shadow;
                walk_depth++;
                object = next;
        }
        /* the deepest object that carries a pager is the file */
        for (int i = walk_depth - 1; i >= 0; i--) {
                if (pagers[i] != NULL) {
                        named = i;
                        break;
                }
        }
        if (lock_busy_at >= 0) {
                /* the chain is not fully held: no name call (it wants the
                 * file object's lock, which we did not get), report where
                 * the walk gave up and release what is held */
                while (walk_depth > 0) {
                        vm_object_unlock(chain[--walk_depth]);
                }
                vm_map_unlock_read(map);
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x (%s) ask %s: chain lock busy at depth %d, deep look skipped\n",
                    n, pid, procname, (uint64_t) vaddr, kr, kr_name(kr), fault_type_text(fault_type), lock_busy_at);
                if (buf != NULL) {
                        kfree_data(buf, R43_PATH_MAX * 2);
                }
                (void) loader_log_flush();
                return;
        }
        if (named >= 0 && pathname != NULL) {
                name_kr = vnode_pager_get_object_name(pagers[named],
                    pathname, R43_PATH_MAX, filename, R43_PATH_MAX, &truncated);
        }
        while (walk_depth > 0) {
                vm_object_unlock(chain[--walk_depth]);
        }
        vm_map_unlock_read(map);

        /* the report, after every lock is gone (printf takes the message
         * buffer's lock; the flush asks the loader to write the log out).
         * R49: the entry's own protection rides against the ask -- a kr 0x2
         * (KERN_PROTECTION_FAILURE) with ask write and entry r.. names the
         * hand at once; the entry's span shows how deep inside it the
         * address fell. */
        for (int i = 0; i <= named - 1; i++) {
                printf("redwood-pager:   chain %d: size 0x%llx pager %d (shadowed by %d)\n",
                    i, (uint64_t) sizes[i], pagers[i] != NULL, i + 1);
        }
        if (named < 0) {
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x (%s) ask %s entry 0x%llx..0x%llx prot 0x%x/%x fileoff 0x%llx: no pager in the chain\n",
                    n, pid, procname, (uint64_t) vaddr, kr, kr_name(kr), fault_type_text(fault_type),
                    (uint64_t) entry->vme_start, (uint64_t) entry->vme_end,
                    entry->protection, entry->max_protection, (uint64_t) fileoff);
        } else if (name_kr != KERN_SUCCESS || pathname[0] == '\0') {
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x (%s) ask %s entry 0x%llx..0x%llx prot 0x%x/%x fileoff 0x%llx size 0x%llx: the pager kept its name (kr 0x%x)\n",
                    n, pid, procname, (uint64_t) vaddr, kr, kr_name(kr), fault_type_text(fault_type),
                    (uint64_t) entry->vme_start, (uint64_t) entry->vme_end,
                    entry->protection, entry->max_protection, (uint64_t) fileoff,
                    (uint64_t) sizes[named], name_kr);
        } else {
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x (%s) ask %s entry 0x%llx..0x%llx prot 0x%x/%x file \"%s\" (depth %d truncated %d) fileoff 0x%llx size 0x%llx\n",
                    n, pid, procname, (uint64_t) vaddr, kr, kr_name(kr), fault_type_text(fault_type),
                    (uint64_t) entry->vme_start, (uint64_t) entry->vme_end,
                    entry->protection, entry->max_protection, pathname, named,
                    truncated ? 1 : 0, (uint64_t) fileoff, (uint64_t) sizes[named]);
        }
        if (buf != NULL) {
                kfree_data(buf, R43_PATH_MAX * 2);
        }
        (void) loader_log_flush();
}
