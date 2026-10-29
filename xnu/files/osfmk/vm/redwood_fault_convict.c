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
 */
#include <kern/kalloc.h>
#include <kern/misc_protos.h>
#include <kern/task.h>
#include <machine/atomic.h>
#include <mach/kern_return.h>
#include <mach/mach_types.h>
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
#define R43_VERBOSE_FIRST       3
#define R43_VERBOSE_EVERY       16
#define R43_SHADOW_MAX          4

static int r43_fault_verdicts;

void redwood_fault_convict(vm_map_t map, vm_map_offset_t vaddr, kern_return_t kr);

void
redwood_fault_convict(vm_map_t map, vm_map_offset_t vaddr, kern_return_t kr)
{
        vm_map_entry_t          entry;
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

        pid = proc_selfpid();
        n = os_atomic_add(&r43_fault_verdicts, 1, relaxed);
        verbose = (n <= R43_VERBOSE_FIRST) || ((n % R43_VERBOSE_EVERY) == 0);
        if (!verbose) {
                return;
        }

        if (get_bsdtask_info(current_task()) != NULL) {
                procname = proc_name_address(get_bsdtask_info(current_task()));
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
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x: map lock busy, deep look skipped\n",
                    n, pid, procname, (uint64_t) vaddr, kr);
                if (buf != NULL) {
                        kfree_data(buf, R43_PATH_MAX * 2);
                }
                (void) loader_log_flush();
                return;
        }
        if (!vm_map_lookup_entry(map, vaddr, &entry) || entry->is_sub_map) {
                vm_map_unlock_read(map);
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x: no map entry\n",
                    n, pid, procname, (uint64_t) vaddr, kr);
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
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x: chain lock busy at depth %d, deep look skipped\n",
                    n, pid, procname, (uint64_t) vaddr, kr, lock_busy_at);
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
         * buffer's lock; the flush asks the loader to write the log out) */
        for (int i = 0; i <= named - 1; i++) {
                printf("redwood-pager:   chain %d: size 0x%llx pager %d (shadowed by %d)\n",
                    i, (uint64_t) sizes[i], pagers[i] != NULL, i + 1);
        }
        if (named < 0) {
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x fileoff 0x%llx: no pager in the chain\n",
                    n, pid, procname, (uint64_t) vaddr, kr, (uint64_t) fileoff);
        } else if (name_kr != KERN_SUCCESS || pathname[0] == '\0') {
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x fileoff 0x%llx size 0x%llx: the pager kept its name (kr 0x%x)\n",
                    n, pid, procname, (uint64_t) vaddr, kr, (uint64_t) fileoff,
                    (uint64_t) sizes[named], name_kr);
        } else {
                printf("redwood-pager: verdict %d: pid %d[%s] address 0x%llx kr 0x%x file \"%s\" (depth %d truncated %d) fileoff 0x%llx size 0x%llx\n",
                    n, pid, procname, (uint64_t) vaddr, kr, pathname, named,
                    truncated ? 1 : 0, (uint64_t) fileoff, (uint64_t) sizes[named]);
        }
        if (buf != NULL) {
                kfree_data(buf, R43_PATH_MAX * 2);
        }
        (void) loader_log_flush();
}
