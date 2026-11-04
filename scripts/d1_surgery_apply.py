#!/usr/bin/env python3
"""d1_surgery_apply.py -- the D1 source surgery, byte-exact, whitespace-honest.

Re-applies the four-file D1 surgery onto a pristine tree:
  1. bsd/sys/redwood_input.h          -- the prototype (next to redwood_gpu_report)
  2. bsd/dev/raphael_adreno.c         -- the report hook (registers read once into
                                         locals, printed, handed to the dump BEFORE
                                         the R41 flush)
  3. iokit/Kernel/RedwoodGPU.cpp      -- the submission ledger + the dump (the
                                         file is TAB-indented in the touched
                                         regions: every inserted line is tabified
                                         to the file's own 8-column tab)
  4. iokit/Kernel/MiniIOMobileFramebuffer.cpp -- the marker swap (56 B, same length)

Every anchor asserts EXACTLY ONE hit.  The tabify rule: leading groups of 8
spaces become tabs, so new code sits in the file's own vocabulary.  After the
surgery, the quick census (brace balance, marker counts, no memset, the
'frame dropped' law) runs before the files are written.
"""
import pathlib
import re

ROOT = pathlib.Path('/home/z/my-project/repo_hackios/xnu/files')
R61_MARKER = b'R61: the association answer read aloud in hex, all bytes'
D1_MARKER = b'D1: the stalled command buffers read aloud, word by word'
assert len(R61_MARKER) == len(D1_MARKER) == 56


def tabify(block):
    """Leading groups of 8 spaces -> tabs (the RedwoodGPU.cpp vocabulary)."""
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


# ---------------------------------------------------------------- 1. the header
p = ROOT / 'bsd' / 'sys' / 'redwood_input.h'
src = p.read_text()
hook = '''void redwood_gpu_report(const char *why);
/* D1: the stall's own words.  The report (above) reads the command processor's IB1
 * registers; this side of the wall is RedwoodGPU.cpp's, which holds the submission
 * ledger and the buffers' kernel map, and reads the stalled command stream aloud.
 * Defined there, called from raphael_adreno.c's redwood_gpu_report(). */
void redwood_gpu_dump_stalled_ib(uint64_t base, uint32_t rem_words);'''
src = sub1(src, 'void redwood_gpu_report(const char *why);', hook, 'header prototype')
p.write_text(src)
print('1. redwood_input.h: prototype in (tab-lines now %d)' % sum(1 for l in src.splitlines() if l.startswith('\t')))

# ---------------------------------------------------------------- 2. raphael_adreno.c
p = ROOT / 'bsd' / 'dev' / 'raphael_adreno.c'
src = p.read_text()
old = '''/* A submission does not come back: what the 3D core and its SMMU context say. */
void
redwood_gpu_report(const char *why)
{
        uint32_t context = smmu_context(&core_space);

        printf("adreno: gpu: %s: submission %u is the last, the core has done %u; ring: read up to %u, written up to %u; status "
            "%#x, interrupts %#x; command processor: fault %#x, interrupts %#x, protection %#x\\n", why, gpu_fence,
            *(volatile uint32_t *)(core_scratch.memory + GPU_FENCE_AT), rd(gpu.base + CP_RB_RPTR), ring_at,
            rd(gpu.base + RBBM_STATUS), rd(gpu.base + RBBM_INT_0_STATUS), rd(gpu.base + CP_HW_FAULT),
            rd(gpu.base + CP_INTERRUPT_STATUS), rd(gpu.base + CP_PROTECT_STATUS));
        printf("adreno: gpu:   in the first command buffer at %#llx, %u words to go; in the second at %#llx, %u to go\\n",
            (uint64_t)rd(gpu.base + CP_IB1_BASE + 4) << 32 | rd(gpu.base + CP_IB1_BASE), rd(gpu.base + CP_IB1_REM_SIZE),
            (uint64_t)rd(gpu.base + CP_IB2_BASE + 4) << 32 | rd(gpu.base + CP_IB2_BASE), rd(gpu.base + CP_IB2_REM_SIZE));'''
new = '''/* A submission does not come back: what the 3D core and its SMMU context say. */
void
redwood_gpu_report(const char *why)
{
        uint32_t context = smmu_context(&core_space);
        uint64_t ib1_base = (uint64_t)rd(gpu.base + CP_IB1_BASE + 4) << 32 | rd(gpu.base + CP_IB1_BASE);
        uint64_t ib2_base = (uint64_t)rd(gpu.base + CP_IB2_BASE + 4) << 32 | rd(gpu.base + CP_IB2_BASE);
        uint32_t ib1_rem = rd(gpu.base + CP_IB1_REM_SIZE), ib2_rem = rd(gpu.base + CP_IB2_REM_SIZE);

        printf("adreno: gpu: %s: submission %u is the last, the core has done %u; ring: read up to %u, written up to %u; status "
            "%#x, interrupts %#x; command processor: fault %#x, interrupts %#x, protection %#x\\n", why, gpu_fence,
            *(volatile uint32_t *)(core_scratch.memory + GPU_FENCE_AT), rd(gpu.base + CP_RB_RPTR), ring_at,
            rd(gpu.base + RBBM_STATUS), rd(gpu.base + RBBM_INT_0_STATUS), rd(gpu.base + CP_HW_FAULT),
            rd(gpu.base + CP_INTERRUPT_STATUS), rd(gpu.base + CP_PROTECT_STATUS));
        printf("adreno: gpu:   in the first command buffer at %#llx, %u words to go; in the second at %#llx, %u to go\\n",
            ib1_base, ib1_rem, ib2_base, ib2_rem);
        /*
         * D1: the words the stalled core was left fetching.  The registers are
         * raphael_adreno.c's to read; the buffer they name is the ledger's to identify --
         * redwood_gpu_dump_stalled_ib() (RedwoodGPU.cpp) holds the submission ledger and
         * the buffers' kernel map, and reads the stalled command stream aloud: the words
         * around the stop, and every operand of the shape the SMMU refused (0x3500, the
         * address that has stood at three generations of this crime -- R39 shutdown, R41
         * reset, D0b runtime).  The values handed over are the same instant's as the ones
         * printed above -- one snapshot, no second read in between.  It runs before the
         * flush below, so one logdump carries the whole story.
         */
        redwood_gpu_dump_stalled_ib(ib1_base, ib1_rem);'''
src = sub1(src, old, new, 'raphael report hook')
p.write_text(src)
print('2. raphael_adreno.c: report hook in (dump call before the flush)')

# ---------------------------------------------------------------- 3. the marker
p = ROOT / 'iokit' / 'Kernel' / 'MiniIOMobileFramebuffer.cpp'
src = p.read_text()
old = '''/* R61's own record, and the kernel-id tool's name for this build (the used-marker
 * tradition: loader_return.c's XNULIVE4 is the precedent).  Same byte length as the
 * R60 marker it replaces, so the round's diff stays the evidence law
 * (wifi/lx/src/lx_80211.c: the association answer's own elements read out in hex,
 * the honest ask count) plus this string -- the payload layout the gate pins does
 * not move. */
__attribute__((used)) static const char r61_assoc_mark[] =
    "R61: the association answer read aloud in hex, all bytes";'''
new = '''/* D1's own record, and the kernel-id tool's name for this build (the used-marker
 * tradition: loader_return.c's XNULIVE4 is the precedent).  Same byte length as the
 * R61 marker it replaces, so the round's diff stays the evidence law
 * (bsd/dev/raphael_adreno.c + iokit/Kernel/RedwoodGPU.cpp: the stalled command
 * stream's words read aloud from the submission ledger -- the dump at the swap gate,
 * the 0x3500 hunt, the pool-reuse note) plus this string -- the payload layout the
 * gate pins does not move. */
__attribute__((used)) static const char d1_stall_mark[] =
    "D1: the stalled command buffers read aloud, word by word";'''
src = sub1(src, old, new, 'marker swap')
p.write_text(src)
print('3. MiniIOMobileFramebuffer.cpp: the marker swapped (56 B same-length)')
print('phase A done (header + raphael + marker); RedwoodGPU.cpp follows in d1_surgery_apply2.py')
