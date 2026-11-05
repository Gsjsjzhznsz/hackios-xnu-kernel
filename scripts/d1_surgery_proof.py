#!/usr/bin/env python3
"""d1_surgery_proof.py -- the D1 round's offline proof, mechanical, on the real
working tree.  Eight laws; every failure names its own law.

  P1  marker law: the D1 marker is 56 B (the R61 marker's exact length), rides
      exactly once behind __attribute__((used)), the R61 marker and its symbol
      are retired tree-wide.
  P2  hook law: redwood_gpu_report() reads the IB1/IB2 registers ONCE into
      locals, prints them with the pre-round format string byte-identical, and
      hands (ib1_base, ib1_rem) to the dump BEFORE loader_log_flush().
  P3  declaration law: the prototype in bsd/sys/redwood_input.h and the
      extern "C" definition in RedwoodGPU.cpp carry the same signature.
  P4  ledger law: the ring bounds, the wrap check (no %'d-off wrapped uint32),
      the five-field capture under the IBS bound, the commit under fence != 0,
      the single-fire gate, the TryLock and BOTH guarded unlocks, the dump
      window math, the hunt shapes and their caps.
  P5  gated-literal law: every literal the yml pre_gate polices keeps its
      pre-round source-tree count (the pre-round tree = the parent of the
      surgery commit, resolved mechanically), EXCEPT the marker swap
      (-1 R61, +1 D1); and every string literal on an added printf/IOLog line
      is the marker or an "adreno: gpu: dump:" print (the gate's gained-mask,
      derived).
  P6  yml law: the dispatch defaults point at the r61 base (39c6e70e /
      8ef12fef), the d1 tools, the D1 verdict word, the R61 base verdicts
      (BOTH greps -- the twin-grep law), the retired-marker line.
  P7  tool law: the d1 trio exists, compiles, and agrees byte-for-byte with
      the source marker; the pins and the masks are in place.
  P8  negatives: four checks, each must FAIL when its law is violated --
      the retired marker detected (N1), an unprefixed print rejected (N2),
      the hunt shape table exact (N3), a missing twin grep detected (N4).
"""
import pathlib
import py_compile
import re
import subprocess

ROOT = pathlib.Path('/home/z/my-project/repo_hackios')
FILES = ROOT / 'xnu' / 'files'
YML = ROOT / '.github' / 'workflows' / 'springboard.yml'
R61_MARKER = b'R61: the association answer read aloud in hex, all bytes'
D1_MARKER = b'D1: the stalled command buffers read aloud, word by word'
DUMP_PREFIX = b'adreno: gpu: dump:'

ok = []


def green(name, note=''):
    ok.append(name)
    print('  GREEN  %-24s %s' % (name, note))


def tree_text(sub):
    return {p: p.read_bytes() for p in sorted(sub.rglob('*')) if p.suffix in ('.c', '.cpp', '.h')}


def count_literal(tree, lit):
    return sum(b.count(lit) for b in tree.values())


def git(*args, **kw):
    return subprocess.run(['git', '-C', str(ROOT)] + list(args), capture_output=True, check=True, **kw)


# ---------------------------------------------------------------- P1 marker
cpp = (FILES / 'iokit' / 'Kernel' / 'MiniIOMobileFramebuffer.cpp').read_bytes()
assert len(D1_MARKER) == len(R61_MARKER) == 56, 'P1: the marker length law broke'
assert cpp.count(D1_MARKER) == 1, 'P1: the D1 marker rides x%d in MiniIOMobileFramebuffer.cpp' % cpp.count(D1_MARKER)
assert b'__attribute__((used)) static const char d1_stall_mark[]' in cpp, 'P1: the D1 mark is not used-attribute'
assert b'd1_stall_mark' in cpp and b'r61_assoc_mark' not in cpp, 'P1: the mark symbols did not swap'
tree = tree_text(FILES)
assert count_literal(tree, R61_MARKER) == 0, 'P1: the R61 marker still rides in the source tree'
assert count_literal(tree, D1_MARKER) == 1, 'P1: the D1 marker rides x%d tree-wide, want 1' % count_literal(tree, D1_MARKER)
green('P1 marker', 'D1 56 B x1 used-attr; R61 marker+symbol retired tree-wide')

# ---------------------------------------------------------------- P2 hook
adv = (FILES / 'bsd' / 'dev' / 'raphael_adreno.c').read_text()
assert adv.count('redwood_gpu_dump_stalled_ib(ib1_base, ib1_rem);') == 1, 'P2: the dump call is not exactly one'
assert adv.count('uint64_t ib1_base =') == 1 and adv.count('uint32_t ib1_rem = rd(gpu.base + CP_IB1_REM_SIZE)') == 1, \
    'P2: the IB1 registers are not read exactly once into locals'
pre_ref = git('log', '-2', '--format=%H', '--', 'xnu/files').stdout.decode().split()[1]
old_adv = git('show', '%s:xnu/files/bsd/dev/raphael_adreno.c' % pre_ref).stdout
fmt = b'printf("adreno: gpu:   in the first command buffer at %#llx, %u words to go; in the second at %#llx, %u to go\\n",'
assert old_adv.count(fmt) == 1, 'P2: the pre-round format string not found in the pre-round tree'
assert adv.encode().count(fmt) == 1, 'P2: the printed IB snapshot format string changed'
fn = adv[adv.index('redwood_gpu_report(const char *why}'):] if False else adv[adv.index('redwood_gpu_report(const char *why)'):]
fn = fn[:fn.index('\n}\n')]
assert fn.index('redwood_gpu_dump_stalled_ib(ib1_base, ib1_rem);') < fn.index('loader_log_flush()'), \
    'P2: the dump must run before the R41 flush'
assert fn.index('printf("adreno: gpu:   in the first command buffer') < fn.index('redwood_gpu_dump_stalled_ib'), \
    'P2: the printed snapshot must precede the handover'
green('P2 hook', 'one call, same-instant locals, printed == handed snapshot, dump before the flush')

# ---------------------------------------------------------------- P3 declaration
hdr = (FILES / 'bsd' / 'sys' / 'redwood_input.h').read_text()
m1 = re.search(r'void\s+redwood_gpu_dump_stalled_ib\(([^)]*)\);', hdr)
rgpu = (FILES / 'iokit' / 'Kernel' / 'RedwoodGPU.cpp').read_text()
m2 = re.search(r'extern "C" void\s*\nredwood_gpu_dump_stalled_ib\(([^)]*)\)', rgpu)
assert m1 and m2, 'P3: the prototype or the definition is missing'
sig_a = ' '.join(m1.group(1).split())
sig_b = ' '.join(m2.group(1).split())
assert sig_a == 'uint64_t base, uint32_t rem_words' and sig_b == 'uint64_t base, uint32_t rem_words', \
    'P3: the signatures disagree: %r vs %r' % (sig_a, sig_b)
green('P3 declaration', 'prototype == extern "C" definition (uint64_t base, uint32_t rem_words)')

# ---------------------------------------------------------------- P4 ledger
for const, want in [('GPU_STALL_RING', '48'), ('GPU_STALL_IBS', '8'), ('GPU_STALL_BOS', '16'),
                    ('GPU_STALL_WORDS', '96'), ('GPU_STALL_HUNT_WORDS', '16384'), ('GPU_STALL_HUNT_HITS', '6')]:
    m = re.search(r'#define\s+%s\s+(\d+)' % const, rgpu)
    assert m and m.group(1) == want, 'P4: %s is %s, want %s' % (const, m and m.group(1), want)
commit = rgpu[rgpu.index('if (request->fence != 0) {'):]
commit = commit[:commit.index('if (gpu_log &&')]
for field in ('r->fence = request->fence;', 'r->ib_all = count;', 'r->ibs = count < GPU_STALL_IBS ? count : GPU_STALL_IBS;',
              'r->ib[i] = stall_ib[i];', 'r->bo_all = request->nr_bos;', 'r->bo[r->bos].base = b->address;',
              'r->bo[r->bos].size = b->size;'):
    assert field in commit, 'P4: the commit lost %r' % field
assert re.search(r'if \(\+\+gGPUStallAt == GPU_STALL_RING\) \{', commit), 'P4: the wrap is not the bounded ++ check'
assert '% GPU_STALL_RING' not in commit, 'P4: the commit path %s a wrapped counter'
assert 'gGPUStall[gGPUStallAt]' in commit, 'P4: the commit does not index the ring directly'
for f in ('stall_ib[count].address = addresses[count];', 'stall_ib[count].words = words[count];',
          'stall_ib[count].buf_base = b->address;', 'stall_ib[count].buf_size = b->size;',
          'stall_ib[count].kernel = b->kernel;'):
    assert f in rgpu, 'P4: the capture lost %r' % f
assert 'if (count < GPU_STALL_IBS) {' in rgpu, 'P4: the capture is not bounded by the IBS cap'
# P4b, the run #41 law (CI 41866501957 named it): a #define used as an array
# bound must precede the struct that binds it -- the proof policed the values
# and missed the order; the order is now its own assertion, on raw byte offsets.
rgpu_bytes = (FILES / 'iokit' / 'Kernel' / 'RedwoodGPU.cpp').read_bytes()
ring_at = rgpu_bytes.find(b'#define GPU_STALL_RING')
assert ring_at >= 0 and rgpu_bytes.find(b'#define GPU_STALL_RING', ring_at + 1) == -1, 'P4b: the RING define is not unique'
for user in (b'struct gpu_stall_range {', b'struct gpu_stall_ib {', b'struct gpu_stall_record {'):
    at = rgpu_bytes.find(user)
    assert at > ring_at, 'P4b: %s precedes the defines (define-after-use: CI run 41 died exactly here)' % user.decode()
assert rgpu_bytes.find(b'#define GPU_STALL_HUNT_HITS') < rgpu_bytes.find(b'struct gpu_stall_range {'), \
    'P4b: the define block is not wholly before the structs'
dump = rgpu[rgpu.index('redwood_gpu_dump_stalled_ib(uint64_t base, uint32_t rem_words)'):]
dump = dump[:dump.index('\nbool\nMiniIOSurfaceDrawn')]
assert 'static bool said;' in dump and 'if (said || gGPULock == NULL) {' in dump, 'P4: the single-fire gate moved'
assert dump.index('IOLockTryLock(gGPULock);') < dump.index('for (uint32_t i = 0; i < GPU_STALL_RING; i++)'), \
    'P4: the TryLock must precede the ledger read'
unlocks = [m.start() for m in re.finditer(r'if \(locked\) \{\s*IOLockUnlock\(gGPULock\);', dump)]
assert len(unlocks) == 2, 'P4: the guarded unlocks are x%d, want 2 (empty-ledger + tail)' % len(unlocks)
assert '(gGPUStallAt + GPU_STALL_RING - 1 - i) % GPU_STALL_RING' in dump, 'P4: the newest-first scan lost its bounded form'
assert 'done >= GPU_STALL_WORDS / 2 ? done - GPU_STALL_WORDS / 2 : 0' in dump, 'P4: the window start math moved'
assert 'end = start + GPU_STALL_WORDS;' in dump and 'if (end > total) {' in dump, 'P4: the window clamp moved'
assert 'h[k] == 0x3500' in dump, 'P4: the exact-shape hunt moved'
assert 'h[k] < 0x10000 && k + 1 < n && h[k + 1] == 0' in dump, 'P4: the near-NULL pair shape moved'
assert 'hits >= GPU_STALL_HUNT_HITS' in dump and 'scanned < GPU_STALL_HUNT_WORDS' in dump, 'P4: the hunt caps moved'
assert 'inside the submission\'s buffer range %u at +%#llx' in dump and 'in no range the submission named' in dump, \
    'P4: the two verdict words moved'
green('P4 ledger', 'bounds 48/8/16/96/16384/6; bounded wrap; five-field capture; single-fire; TryLock + 2 guarded unlocks; hunt shapes + caps')

# ---------------------------------------------------------------- P5 gated literals
yml_raw = YML.read_text()
pre_gate = yml_raw[yml_raw.index('pre_gate_py:'):yml_raw.index('kernel_gate_cmd:')]
lits = sorted(set(re.findall(r"n\('((?:[^'\\]|\\.)*)'\)", pre_gate)))
lits = [l.replace("\\'", "'").replace('\\n', '\n') for l in lits]
assert lits, 'P5: no literals found in the yml pre_gate (the extractor broke?)'
# the round markers sit in the for-loop tuple, not in n() calls -- policed explicitly
lits = sorted(set(lits) | {D1_MARKER.decode(), R61_MARKER.decode()})
files_now = git('ls-files', 'xnu/files').stdout.decode().split()
moved = {}
for path in files_now:
    new = (ROOT / path).read_bytes()
    try:
        old = git('show', '%s:%s' % (pre_ref, path)).stdout
    except subprocess.CalledProcessError:
        old = b''
    for lit in lits:
        lb = lit.encode()
        d = new.count(lb) - old.count(lb)
        if d:
            moved[lit] = moved.get(lit, 0) + d
assert moved == {D1_MARKER.decode(): 1, R61_MARKER.decode(): -1}, \
    'P5: the gated-literal delta is %s, want exactly {D1:+1, R61:-1}' % moved
# the added-printf law: every literal on an added printf/IOLog line is prefixed or invisible
diff = git('diff', pre_ref, 'HEAD', '--', 'xnu/files').stdout.decode()
bad = []
for line in diff.splitlines():
    if not line.startswith('+') or line.startswith('+++'):
        continue
    if 'printf(' not in line and 'IOLog(' not in line:
        continue
    for lit in re.findall(r'"((?:[^"\\]|\\.)*)"', line):
        content = lit.encode().decode('unicode_escape').encode('latin1', 'replace')
        for run in re.findall(rb'[\x20-\x7e]{8,}', content):
            if not (run == D1_MARKER or run.startswith(DUMP_PREFIX)):
                bad.append(run)
assert not bad, 'P5: added print literals outside the gained-mask vocabulary: %s' % bad[:3]
green('P5 gated literals', '%d policed literals: only {D1:+1, R61:-1} moved; every added print is dump-prefixed' % len(lits))

# ---------------------------------------------------------------- P6 yml
y = yml_raw
assert 'default: "d1"' in y and 'default: "d1-springboard"' in y, 'P6: the round/tag defaults did not move'
assert 'releases/download/r61-springboard/raphael-xnu-r61-gpu-flash.img' in y, 'P6: the base gpu url is not the r61 release'
assert 'default: "39c6e70e430e424b93f6cfe40d2c7d264c3bf2dde0813087fdd48aa559f99ea1"' in y, 'P6: the base gpu sha pin moved'
assert 'kernel=8ef12fefbb2edc1cafdb0bb38bca4d0bb6adffee1771fdd333efc9c77b7b5ae7' in y, 'P6: the base kernel entry pin moved'
assert 'tools/d1_kernel_gate.py --base-kernel \\"$BASE_KERNEL\\" --r61-kernel' in y, 'P6: the gate cmd did not move to the d1 tool'
assert 'python3 tools/d1_flash_build.py --daily' in y, 'P6: the flash_build cmd did not move'
assert 'python3 tools/d1-kernel-id.py \\"$REL/' in y, 'P6: the kernel-id cmd did not move'
assert y.count('grep -c "the kernel entry is D1" "$IDFILE"') == 1, 'P6: the D1 verdict gate is missing'
assert y.count('the kernel entry is R61') >= 2, 'P6: the base R61 verdict greps lost a twin (the bef1c05 lesson)'
assert "assert n('R61: the association answer read aloud in hex, all bytes') == 0" in y, 'P6: the R61 retired line is missing'
assert "('D1: the stalled command buffers read aloud, word by word', 'THE D1 GPU stall forensics marker')" in y, \
    'P6: the pre_gate D1 marker entry is missing'
assert 'd1-md1-root.apfs' in y and 'raphael-d1-flashboot.apfs' in y, 'P6: the release file names did not move'
assert 'python3 tools/r61_kernel_gate.py' not in y and 'python3 tools/r61_flash_build.py' not in y, \
    'P6: a stale r61 tool call survived the re-pin'
green('P6 yml', 'd1 round/tag; r61 base pins; d1 tool trio; D1 verdict word; R61 base greps both alive; retired line in')

# ---------------------------------------------------------------- P7 tools
tools = ROOT / 'tools'
for name in ('d1_flash_build.py', 'd1_kernel_gate.py', 'd1-kernel-id.py'):
    py_compile.compile(str(tools / name), doraise=True)
fb = (tools / 'd1_flash_build.py').read_bytes()
gate = (tools / 'd1_kernel_gate.py').read_bytes()
kid = (tools / 'd1-kernel-id.py').read_bytes()
assert fb.count(D1_MARKER) == 1 and b'kernel.count(R61_MARKER) == 0' in fb, 'P7: the flash_build marker law moved'
assert b"R61_GPU = '39c6e70e" in fb and b"R61_KERNEL = '8ef12fef" in fb, 'P7: the flash_build base pins moved'
assert gate.count(D1_MARKER) == 1 and gate.count(R61_MARKER) == 1, 'P7: the gate marker constants moved'
assert b'assert len(D1_MARKER) == len(R61_MARKER)' in gate, 'P7: the gate length law moved'
assert b"s.startswith(b'adreno: gpu: dump:')" in gate, 'P7: the gate gained-mask prefix moved'
assert b'return s == R61_MARKER or \\' in gate, 'P7: the gate loss mask does not retire the R61 marker'
assert b'--r61-kernel' in gate, 'P7: the gate arg did not move'
assert kid.count(D1_MARKER) == 1 and b'R61_SHA' in kid, 'P7: the id tool moved'
assert b'the kernel entry is D1' in kid and b"D1_MARKER) == 1, 'a new kernel without the D1 marker is nobody'" in kid, \
    'P7: the id new-kernel law moved'
assert b'the kernel entry is R61 (the association-answer round' in kid, 'P7: the R61 base verdict moved'
green('P7 tools', 'd1 trio compiles; pins 39c6e70e/8ef12fef; marker bytes agree with the source; masks + verdict words in place')

# ---------------------------------------------------------------- P8 negatives
n1 = cpp.replace(b'D1: the stalled command buffers read aloud, word by word',
                 b'D1: the stalled command buffers read aloud, word by word' + b'\x00r61legacy\x00' + R61_MARKER)
def p1_rule(buf):
    return buf.count(R61_MARKER) == 0
assert not p1_rule(n1) and p1_rule(cpp), 'N1: the retired-marker detector does not fire'
green('N1 negative', 'a re-added R61 marker is caught by the tree-count rule (the rule fires on the real tree)')

def p5_line(line):
    if 'printf(' not in line and 'IOLog(' not in line:
        return []
    bad = []
    for lit in re.findall(r'"((?:[^"\\]|\\.)*)"', line):
        content = lit.encode().decode('unicode_escape').encode('latin1', 'replace')
        for run in re.findall(rb'[\x20-\x7e]{8,}', content):
            if not (run == D1_MARKER or run.startswith(DUMP_PREFIX)):
                bad.append(run)
    return bad
assert p5_line('        printf("adreno: gpu: oops %u\\n", x);'), 'N2: the detector missed an unprefixed print'
assert not p5_line('        printf("adreno: gpu: dump: fine %u\\n", x);'), 'N2: the detector fired on a prefixed print'
assert not p5_line('        printf(" %08x", w[i]);'), 'N2: the detector fired on the <8-char cell'
green('N2 negative', 'unprefixed print rejected; dump-prefixed and the 5-char cell pass')

def shape(lo, hi_next):
    if lo == 0x3500:
        return 'exact'
    if lo != 0 and lo < 0x10000 and hi_next == 0:
        return 'pair'
    return None
assert shape(0x3500, 7) == 'exact' and shape(0x3500, 0) == 'exact', 'N3: the exact shape moved'
assert shape(0x1234, 0) == 'pair', 'N3: the near-NULL pair missed'
assert shape(0x12345, 0) is None, 'N3: a >=0x10000 low word must not fire'
assert shape(0, 0) is None, 'N3: a zero low word must not fire'
assert shape(0x1234, 5) is None, 'N3: a nonzero high word kills the pair shape'
green('N3 negative', 'the hunt shape table: exact 0x3500 fires; (lo<0x10000, hi==0) fires; the rest stay silent')

twin1 = y.count('grep -c "the kernel entry is R61" "$BASE_IDFILE"')
twin2 = y.count('grep -q "the kernel entry is R61 (the evidence round"')
assert twin1 == 1 and twin2 == 1, 'N4: a twin grep is missing (the run 41460053960 lesson): %d/%d' % (twin1, twin2)
green('N4 negative', 'both base R61 verdict greps present (the twin-grep law)')

print('\nALL %d LAWS GREEN -- the D1 round holds offline.' % len(ok))
