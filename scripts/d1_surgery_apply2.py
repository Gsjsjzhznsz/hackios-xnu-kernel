#!/usr/bin/env python3
"""d1_surgery_apply2.py -- the RedwoodGPU.cpp half of the D1 surgery (the
tab-indented file: every inserted line is tabified to the file's own
8-column tab).  Run after d1_surgery_apply.py."""
import pathlib
import re

ROOT = pathlib.Path('/home/z/my-project/repo_hackios/xnu/files')
p = ROOT / 'iokit' / 'Kernel' / 'RedwoodGPU.cpp'
src = p.read_text()


def tabify(block):
    out = []
    for line in block.split('\n'):
        stripped = line.lstrip(' ')
        n = len(line) - len(stripped)
        out.append('\t' * (n // 8) + ' ' * (n % 8) + stripped)
    return '\n'.join(out)


def sub1(text, old, new, what):
    n = text.count(old)
    assert n == 1, '%s: %d hits (want exactly 1)' % (what, n)
    return text.replace(old, new)


# ---- a. the ledger, after } gGPUStat;
LEDGER = '''
/*
 * D1: the submission ledger the stall reads.  Every submission the core is given leaves
 * its command buffers here -- where each indirect buffer sits in the GPU's address space,
 * how many words it carries, and the buffer it was cut from (its place, its size, and
 * where it is in the kernel's own map) -- plus the buffer ranges the submission's bos
 * named.  When the core stalls, redwood_gpu_report() (raphael_adreno.c) hands the CP's
 * own IB1 reading over, redwood_gpu_dump_stalled_ib() (below) finds the submission whose
 * buffer that is and reads the stalled words aloud from the kernel's map.  The read stays
 * safe for as long as the stall stands: a submission the core never finishes holds its
 * buffers (freeBuffer keeps the memory -- "they stay its own" -- and reap() waits for the
 * fence), so no kernel pointer in the ledger can dangle behind a stalled fence.  Written
 * under gGPULock at submit time; read back at report time with IOLockTryLock (upstream
 * xnu-8796.121.2 IOLocks.h: a macro under IOLOCKS_INLINE, an extern otherwise), because
 * the report arrives both under the lock (gpuWait's no-answer path, submit's ring-full
 * path) and without it (the framebuffer's dropped-frame path).
 */
struct gpu_stall_range {
        uint64_t        base, size;             /* a named buffer's place in the GPU's address space */
};
struct gpu_stall_ib {
        uint64_t        address;                /* the indirect buffer's place: buffer + submit offset */
        uint32_t        words;                  /* its length in words */
        uint64_t        buf_base;               /* the carrying buffer's own place */
        uint64_t        buf_size;               /* ... and its whole size */
        uint8_t *       kernel;                 /* the carrying buffer in the kernel's map */
};
struct gpu_stall_record {
        uint32_t        fence;                  /* the submission's number (0: this slot is empty) */
        uint32_t        ibs;                    /* recorded of its command buffers... */
        uint32_t        ib_all;                 /* ... of how many it had (beyond GPU_STALL_IBS: counted, not recorded) */
        uint32_t        bos;                    /* recorded of its buffer ranges... */
        uint32_t        bo_all;                 /* ... of how many the submission named */
        struct gpu_stall_ib     ib[GPU_STALL_IBS];
        struct gpu_stall_range  bo[GPU_STALL_BOS];
};
#define GPU_STALL_RING  48              /* the last so many submissions the ledger holds */
#define GPU_STALL_IBS   8               /* the most command buffers of one submission it records */
#define GPU_STALL_BOS   16              /* the most buffer ranges of one submission it records */
#define GPU_STALL_WORDS 96              /* the words read around the stop (the pre-registered half-before, half-after) */
#define GPU_STALL_HUNT_WORDS    16384   /* the most the hunt scans across the record's command buffers */
#define GPU_STALL_HUNT_HITS     6       /* the most hits of each shape the hunt prints */
static struct gpu_stall_record gGPUStall[GPU_STALL_RING];
static uint32_t gGPUStallAt;            /* where the next entry goes; wraps at GPU_STALL_RING, never %'d off a wrapped uint32 */'''
src = sub1(src, '} gGPUStat;\n', '} gGPUStat;\n' + tabify(LEDGER) + '\n', 'ledger block')

# ---- b. the capture, in the cmd loop
old = '''                addresses[count] = b->address + cmds[i].submit_offset;
                words[count++] = cmds[i].size / 4;'''
new = '''                addresses[count] = b->address + cmds[i].submit_offset;
                words[count] = cmds[i].size / 4;
                if (count < GPU_STALL_IBS) {
                        /* D1: the command buffer enters the stall ledger with its carrying buffer */
                        stall_ib[count].address = addresses[count];
                        stall_ib[count].words = words[count];
                        stall_ib[count].buf_base = b->address;
                        stall_ib[count].buf_size = b->size;
                        stall_ib[count].kernel = b->kernel;
                }
                count++;'''
# the file is tab-indented here: rewrite the old block with its own leading whitespace
old_t = '\n\t\taddresses[count] = b->address + cmds[i].submit_offset;\n\t\twords[count++] = cmds[i].size / 4;'
new_t = '\n' + tabify(new)
n = src.count(old_t)
assert n == 1, 'cmd loop: %d hits (want exactly 1)' % n
src = src.replace(old_t, new_t)

# ---- c. the commit, after the ring-full retry block
old_t = '\tif (request->fence == 0 && !gGPUReported) {\n' \
        '\t\tredwood_gpu_report("the ring is full");\n' \
        '\t\tgGPUReported = !redwood_gpu_recover();\n' \
        '\t\tif (!gGPUReported) {\n' \
        '\t\t\trequest->fence = redwood_gpu_submit(addresses, words, count);\n' \
        '\t\t}\n' \
        '\t}'
assert src.count(old_t) == 1, 'retry block: %d hits' % src.count(old_t)
COMMIT = '''
        /*
         * D1: the submission enters the stall ledger.  Under the lock, as everything here
         * is; a slot of the ring is overwritten in place, the index wraps at the ring's own
         * bound (a uint32 %'d off a power of two that is not its divisor would wander at the
         * wrap).  Only a submission the core was actually given is recorded: fence 0 means
         * the ring never took it.
         */
        if (request->fence != 0) {
                struct gpu_stall_record * r = &gGPUStall[gGPUStallAt];

                /*
                 * No zeroing first: every field the stall's dump ever reads (fence, ibs,
                 * ib_all, bos, bo_all, and the ib[]/bo[] entries below their own counts)
                 * is written here before the ring moves on -- a reused slot's stale tail
                 * is never read, the loops bound by the record's own counts.
                 */
                r->fence = request->fence;
                r->ib_all = count;
                r->ibs = count < GPU_STALL_IBS ? count : GPU_STALL_IBS;
                for (uint32_t i = 0; i < r->ibs; i++) {
                        r->ib[i] = stall_ib[i];
                }
                r->bo_all = request->nr_bos;
                for (uint32_t i = 0; i < request->nr_bos && r->bos < GPU_STALL_BOS; i++) {
                        struct gpu_buffer * b = buffer(bos[i].handle);

                        if (b != NULL) {
                                r->bo[r->bos].base = b->address;
                                r->bo[r->bos].size = b->size;
                                r->bos++;
                        }
                }
                if (++gGPUStallAt == GPU_STALL_RING) {
                        gGPUStallAt = 0;
                }
        }'''
src = src.replace(old_t, old_t + '\n' + tabify(COMMIT))

# ---- d. the stall_ib declaration
old_t = '\tuint32_t words[GPU_SUBMIT_CMDS], count = 0;'
n = src.count(old_t)
assert n == 1, 'submit decls: %d hits' % n
decl = '\tstruct gpu_stall_ib stall_ib[GPU_STALL_IBS];   /* D1: the first GPU_STALL_IBS command buffers, for the stall ledger; every slot read is written this submission */'
src = src.replace(old_t, old_t + '\n' + decl)

# ---- e. the dump, before MiniIOSurfaceDrawn
DUMP = '''
/*
 * D1: the stalled command buffer's words, read aloud.  raphael_adreno.c's report reads
 * the command processor's own IB1 registers and hands them here; this side holds the
 * submission ledger (gGPUStall: every submission left its command buffers' places and
 * lengths, and the carrying buffers' kernel addresses), so the words the core stalled on
 * can be named and read from the kernel's own map.  One dump per boot: every caller of
 * redwood_gpu_report() is gated by a static already, and this one is the dump's own.
 *
 * The lock: the report comes from the wait paths with gGPULock held and from the
 * framebuffer's dropped-frame path without it, so the ledger is read under
 * IOLockTryLock -- taken, the read is clean; refused, the read goes on unlocked and says
 * so (the ring's writer is submit() alone, and the first dropped frame's report fires
 * while the ring's tail still holds the stalled submission).
 *
 * The hunt: the SMMU refused 0x3500 three generations running (R39 shutdown, R41 reset,
 * D0b runtime), so the words are scanned for that exact value and for its pointer shape
 * (a 64-bit operand whose high word is zero and whose low word is near NULL -- an
 * unpatched intra-buffer offset on a pool-reused buffer is the family suspect), and
 * every hit is classified against the ranges the submission itself named: inside one of
 * its buffers, or inside no range at all.
 */
extern "C" void
redwood_gpu_dump_stalled_ib(uint64_t base, uint32_t rem_words)
{
        static bool said;
        bool locked;
        const struct gpu_stall_record * found = NULL, * newest = NULL;
        uint32_t found_ib = 0, held = 0;

        if (said || gGPULock == NULL) {
                return;
        }
        said = true;
        locked = IOLockTryLock(gGPULock);
        if (!locked) {
                printf("adreno: gpu: dump: the ledger's read runs without the lock (the report's own path holds it); the words are read as they lay\\n");
        }
        for (uint32_t i = 0; i < GPU_STALL_RING; i++) {         /* newest first */
                const struct gpu_stall_record * r =
                    &gGPUStall[(gGPUStallAt + GPU_STALL_RING - 1 - i) % GPU_STALL_RING];

                if (r->fence == 0) {
                        continue;
                }
                held++;
                if (newest == NULL) {
                        newest = r;
                }
                if (found == NULL) {
                        for (uint32_t j = 0; j < r->ibs; j++) {
                                if (r->ib[j].address == base) {
                                        found = r;
                                        found_ib = j;
                                        break;
                                }
                        }
                }
        }
        if (held == 0) {
                printf("adreno: gpu: dump: the ledger is empty -- no submission the core was given is on record; the words of %#llx stay unread\\n", base);
                if (locked) {
                        IOLockUnlock(gGPULock);
                }
                return;
        }
        if (found == NULL) {
                found = newest;
                printf("adreno: gpu: dump: the CP's first command buffer %#llx is not in the ledger's %u records; the newest (submission %u) is read instead\\n",
                    base, held, newest->fence);
        } else {
                printf("adreno: gpu: dump: submission %u names the CP's first command buffer %#llx (its command buffer %u of the %u recorded)\\n",
                    found->fence, base, found_ib, found->ibs);
        }
        printf("adreno: gpu: dump: submission %u: %u of %u command buffers recorded, %u of %u buffer ranges recorded\\n",
            found->fence, found->ibs, found->ib_all, found->bos, found->bo_all);
        for (uint32_t j = 0; j < found->ibs; j++) {
                printf("adreno: gpu: dump:   command buffer %u at %#llx, %u words (from the buffer at %#llx, %llu B, kernel %p)\\n",
                    j, found->ib[j].address, found->ib[j].words, found->ib[j].buf_base, found->ib[j].buf_size,
                    found->ib[j].kernel);
        }
        for (uint32_t i = 0; i < GPU_STALL_RING; i++) {         /* the pool's reuse: who else ran from this place */
                const struct gpu_stall_record * r =
                    &gGPUStall[(gGPUStallAt + GPU_STALL_RING - 1 - i) % GPU_STALL_RING];

                if (r->fence == 0 || r == found) {
                        continue;
                }
                for (uint32_t j = 0; j < r->ibs; j++) {
                        if (r->ib[j].address == base) {
                                printf("adreno: gpu: dump:   the same place was submission %u's command buffer %u too (the pool gave it out again)\\n", r->fence, j);
                                break;
                        }
                }
        }
        const struct gpu_stall_ib * ib = &found->ib[found_ib];
        unsigned long total = ib->words, done, scanned = 0, all_words = 0, hits = 0;
        bool clamped = false;

        for (uint32_t j = 0; j < found->ibs; j++) {
                all_words += found->ib[j].words;
        }
        if (ib->kernel == NULL || total == 0) {
                printf("adreno: gpu: dump: the carrying buffer's kernel address is gone from the record (%u words recorded); the words stay unread\\n",
                    found->ibs);
        } else {
                unsigned long start, end;
                const uint32_t * w = (const uint32_t *)(ib->kernel + (unsigned long)(ib->address - ib->buf_base));

                if ((uint64_t) rem_words > total) {
                        printf("adreno: gpu: dump: the register says %u words to go but the ledger says the buffer carries %lu: they disagree, the buffer is read from its first word\\n",
                            rem_words, total);
                        done = 0;
                } else {
                        done = total - rem_words;
                        printf("adreno: gpu: dump: the CP stopped %lu words into command buffer %u (the register said %u to go of %lu)\\n",
                            done, found_ib, rem_words, total);
                }
                start = done >= GPU_STALL_WORDS / 2 ? done - GPU_STALL_WORDS / 2 : 0;
                end = start + GPU_STALL_WORDS;
                if (end > total) {
                        end = total;
                }
                if (end - start < GPU_STALL_WORDS && start > 0) {
                        start = end > GPU_STALL_WORDS ? end - GPU_STALL_WORDS : 0;
                }
                printf("adreno: gpu: dump: the words around the stop, %lu of them from word %lu (the stop is word %lu):\\n",
                    end - start, start, done);
                for (unsigned long i = start; i < end; i += 8) {
                        unsigned long n = end - i < 8 ? end - i : 8;

                        printf("adreno: gpu: dump:   [%4lu]", i);
                        for (unsigned long k = 0; k < n; k++) {
                                printf(" %08x", w[i + k]);
                        }
                        printf("\\n");
                }
        }
        /* the hunt: 0x3500 and its pointer shape, across the record's own command buffers */
        for (uint32_t j = 0; j < found->ibs && scanned < GPU_STALL_HUNT_WORDS; j++) {
                const uint32_t * h;
                unsigned long n;

                if (found->ib[j].kernel == NULL || found->ib[j].words == 0) {
                        continue;
                }
                h = (const uint32_t *)(found->ib[j].kernel +
                    (unsigned long)(found->ib[j].address - found->ib[j].buf_base));
                n = found->ib[j].words;
                if (scanned + n > GPU_STALL_HUNT_WORDS) {
                        n = GPU_STALL_HUNT_WORDS - scanned;
                        clamped = true;
                }
                scanned += n;
                for (unsigned long k = 0; k < n; k++) {
                        uint64_t shaped = 0;

                        if (h[k] == 0x3500) {
                                shaped = 0x3500;
                        } else if (h[k] != 0 && h[k] < 0x10000 && k + 1 < n && h[k + 1] == 0) {
                                shaped = h[k];          /* a 64-bit operand whose high word is zero, whose low word is near NULL */
                        }
                        if (shaped == 0 || hits >= GPU_STALL_HUNT_HITS) {
                                continue;
                        }
                        hits++;
                        uint32_t which = 0;
                        uint64_t off = 0;
                        bool inside = false;

                        for (uint32_t q = 0; q < found->bos; q++) {
                                if (shaped >= found->bo[q].base && shaped < found->bo[q].base + found->bo[q].size) {
                                        which = q;
                                        off = shaped - found->bo[q].base;
                                        inside = true;
                                        break;
                                }
                        }
                        if (inside) {
                                printf("adreno: gpu: dump:   hunt: command buffer %u word %lu = %#llx: inside the submission's buffer range %u at +%#llx (context %#x %#x | %#x %#x)\\n",
                                    j, k, shaped, which, off,
                                    k >= 2 ? h[k - 2] : 0, k >= 1 ? h[k - 1] : 0,
                                    k + 1 < n ? h[k + 1] : 0, k + 2 < n ? h[k + 2] : 0);
                        } else {
                                printf("adreno: gpu: dump:   hunt: command buffer %u word %lu = %#llx: in no range the submission named (context %#x %#x | %#x %#x)\\n",
                                    j, k, shaped,
                                    k >= 2 ? h[k - 2] : 0, k >= 1 ? h[k - 1] : 0,
                                    k + 1 < n ? h[k + 1] : 0, k + 2 < n ? h[k + 2] : 0);
                        }
                }
        }
        if (clamped) {
                printf("adreno: gpu: dump: the hunt scanned %lu of %lu words across the record's command buffers (the cap stopped it): %lu hits\\n",
                    scanned, all_words, hits);
        } else {
                printf("adreno: gpu: dump: the hunt scanned %lu of %lu words across the record's command buffers: %lu hits\\n",
                    scanned, all_words, hits);
        }
        if (locked) {
                IOLockUnlock(gGPULock);
        }
}
'''
anchor = 'bool\nMiniIOSurfaceDrawn(MiniIOSurface * surface, uint64_t milliseconds)'
n = src.count(anchor)
assert n == 1, 'dump anchor: %d hits' % n
src = src.replace(anchor, tabify(DUMP) + '\n' + anchor)

p.write_text(src)

# ---- the quick census before declaring victory
s = src
assert s.count('{') == s.count('}'), 'brace balance broke: %d/%d' % (s.count('{'), s.count('}'))
assert s.count('(') == s.count(')'), 'paren balance broke'
assert 'memset' not in s, 'memset survived'
assert 'r61_assoc_mark' not in s
for const, want in [('GPU_STALL_RING', '48'), ('GPU_STALL_IBS', '8'), ('GPU_STALL_BOS', '16'),
                    ('GPU_STALL_WORDS', '96'), ('GPU_STALL_HUNT_WORDS', '16384'), ('GPU_STALL_HUNT_HITS', '6')]:
    assert re.search(r'#define\s+%s\s+%s\b' % (const, want), s), '%s define moved' % const
import subprocess
g = subprocess.run(['git', '-C', '/home/z/my-project/repo_hackios', 'diff', '--stat', '--', 'xnu/files/iokit/Kernel/RedwoodGPU.cpp'],
                   capture_output=True, text=True).stdout
print('4. RedwoodGPU.cpp: ledger + capture + commit + dump in; census green')
print('   diff stat vs HEAD: %s' % ' '.join(g.split()[-1:]))
wt = (ROOT / 'iokit' / 'Kernel' / 'RedwoodGPU.cpp').read_bytes()
assert wt.count(b'adreno: gpu: dump:') == 16, 'dump print count moved: %d' % wt.count(b'adreno: gpu: dump:')
print('   dump prints: %d' % wt.count(b'adreno: gpu: dump:'))
print('phase B done -- the surgery is complete on all four files')
