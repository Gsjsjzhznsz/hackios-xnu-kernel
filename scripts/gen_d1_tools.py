#!/usr/bin/env python3
"""Generate the D1 round from the R61 generation: three tools (flash_build /
kernel_gate / kernel-id), each produced by asserted single-hit substitutions.

D1 = the GPU stall forensics round.  D0b's capture convicted the wall: the SMMU
refused the 3D core at 0x3500 (status 0x80000402, syndrome 0x20002, own-walk
L2[0]=0) 12 ms after swap 30 hit its 500 ms gate; the CP idle-stopped (ring
1299==1299); not one SpringBoard frame reached the panel.  The 0x3500 shape
stood three generations running (R39 shutdown, R41 reset, D0b runtime).  D1
arms the one exit every stall reports through: redwood_gpu_report() hands the
CP's IB1 registers to RedwoodGPU.cpp's submission ledger, which names the
submission whose buffer the core stopped in, notes the pool reuse, reads the
96 words around the stop from the kernel's own map, and hunts every 0x3500 and
near-NULL pointer operand against the ranges the submission itself named.

The bases = the r61 release (the gpu 39c6e70e carries the R61 kernel entry
8ef12fef as the gate's BASE; the daily b0f9f6df and the flashboot 14485e7c are
byte-identical to the r60/r54 ones).  The marker: 'D1: the stalled command
buffers read aloud, word by word' (56 B, the R61 marker's exact length).

Every substitution below asserts EXACTLY the expected number of hits -- a
silent near-miss must be impossible.
"""
import pathlib

ROOT = pathlib.Path('/home/z/my-project/repo_hackios')
TOOLS = ROOT / 'tools'
R61_MARKER = b'R61: the association answer read aloud in hex, all bytes'
D1_MARKER = b'D1: the stalled command buffers read aloud, word by word'
assert len(R61_MARKER) == len(D1_MARKER) == 56

R61_GPU = '39c6e70e430e424b93f6cfe40d2c7d264c3bf2dde0813087fdd48aa559f99ea1'
R61_KERNEL = '8ef12fefbb2edc1cafdb0bb38bca4d0bb6adffee1771fdd333efc9c77b7b5ae7'
D1_DAILY = 'b0f9f6df4ea13ad9d45048e7ff15e33d7b2fcb67204d01964b1014517ca8a60b'


def sub1(text, old, new, what):
    n = text.count(old)
    assert n == 1, '%s: %d hits (want exactly 1)' % (what, n)
    return text.replace(old, new)


def subn(text, old, new, what, want):
    n = text.count(old)
    assert n == want, '%s: %d hits (want exactly %d)' % (what, n, want)
    return text.replace(old, new)


# ---------------------------------------------------------------- flash_build
src = (TOOLS / 'r61_flash_build.py').read_text()
src = sub1(src, '"""tools/r61_flash_build.py -- pack the r61 GPU image: the r60 release bytes with ONE entry swapped.',
           '"""tools/d1_flash_build.py -- pack the d1 GPU image: the r61 release bytes with ONE entry swapped.', 'fb doc head')
src = sub1(src, """R54 is the decode round.  R53's offline proof (V1..V7, ALL GREEN on the R52
boot's bytes) settled the arithmetic: the copyin'd all_image_infos template
IS dyld's static bytes, every pointer field still wears its chained-fixup
rebase encoding (target = the low 36 bits, the next chain in the top half),
and the upstream slide arithmetic walks a chain fragment into the address
(broken = real + 0xFF70000000000000) -- so all three dyld prefetches die
fail-fast on no-map-entry: harmless, but the pages were never warmed.  R54
decodes BEFORE the prefetch: target = field & 0xFFFFFFFFF; dyld_base =
aii_addr - infos_target; the prefetch walks the REAL page.  A census line
counts the decodes (ok) and the skips (any bind/auth/high8/unused bit set).
The marker swaps same-length.  The cmdline stays the R39 GPU form VERBATIM
-- the decoder needs no boot-arg (the template bytes themselves say what
they are).""",
           """D1 is the GPU stall forensics round.  D0b's capture convicted the wall: the
SMMU refused the 3D core at 0x3500 (status 0x80000402, syndrome 0x20002,
own-walk L2[0]=0) 12 ms after swap 30 hit its 500 ms gate; the CP idle-stopped
(ring 1299==1299); not one SpringBoard frame reached the panel; and the 0x3500
shape stood three generations running (R39 shutdown, R41 reset, D0b runtime).
D1 arms the one exit every stall reports through: redwood_gpu_report() hands
the CP's IB1 registers to RedwoodGPU.cpp's submission ledger, which names the
submission whose buffer the core stopped in, notes the pool reuse, reads the
96 words around the stop from the kernel's own map, and hunts every 0x3500 and
near-NULL pointer operand against the ranges the submission itself named.
The marker swaps same-length.  The cmdline stays the R39 GPU form VERBATIM
-- the forensics arm on the already-failing path alone, no boot-arg.""", 'fb doc story')
src = sub1(src, """  - the bases are EXACTLY the r60 release's (b0f9f6df... / 0a0683f1...; the daily and
    the flashboot are byte-identical to the r54 ones, the gpu carries the R60 kernel)
  - the base loader/kernel/volume are EXACTLY the r54 release's internal
    entries (4bf828e4... / e0570b2a... / 5b9bbbcb...), the loader
    fingerprint-gated
  - the new kernel carries the R33 lineage, every R38 mark, the R54 marker
    x1, the sigbirth format x1, the sigpost format x1 (the R46/R45 wires
    stay), the siglive format x1 AND the sigcensus births format x1 (the
    R48 census), the sigcensus uf-ok tail x1 (the R51 split), the
    r49-deliver format x1 AND the redwood-fault CONTEXT format x1 (the
    R49 wires), the redwood-fault literal x2 AND the registers format x1
    (the R50 hologram, unchanged), the R51 prefault hologram format x1 and
    the R51 clamp counter symbol (the R51 knives, unchanged), the R52
    infos-hex head x1, the hex line format x1, the two channel fingerprint
    formats x2 and the archaeology counter symbol (the R52 knives STAY --
    the decode rides on top of them), the R54 decode hologram format x1 and
    the decode census line x1 and the decode counter symbol (this round's
    knives), and the R52/R51/R50/R49/.../R40 markers ZERO times, the
    "redwood-pager: verdict" literal x6 (unchanged -- R54 adds no pager
    verdict format)
  - the cmdline, the md1 volume and usb are the r54 release's own bytes""",
           """  - the bases are EXACTLY the r61 release's (%s... / %s...; the daily and
    the flashboot are byte-identical to the r60/r54 ones, the gpu carries the R61 kernel)
  - the base loader/kernel/volume are EXACTLY the r61 release's internal
    entries (4bf828e4... / 8ef12fef... / 5b9bbbcb...), the loader
    fingerprint-gated
  - the new kernel carries the R33 lineage, every R38 mark, the D1 marker
    x1 (the R61 marker retired), the sigbirth format x1, the sigpost format
    x1 (the R46/R45 wires stay), the siglive format x1 AND the sigcensus
    births format x1 (the R48 census), the sigcensus uf-ok tail x1 (the R51
    split), the r49-deliver format x1 AND the redwood-fault CONTEXT format
    x1 (the R49 wires), the redwood-fault literal x2 AND the registers
    format x1 (the R50 hologram, unchanged), the R51 prefault hologram
    format x1 and the R51 clamp counter symbol (the R51 knives, unchanged),
    the R52 infos-hex head x1, the hex line format x1, the two channel
    fingerprint formats x2 and the archaeology counter symbol (the R52
    knives STAY -- the decode rides on top of them), the R54 decode
    hologram format x1 and the decode census line x1 and the decode counter
    symbol (the R54 knives stay), and the R61/R60/R52/R51/R50/R49/.../R40
    markers ZERO times, the "redwood-pager: verdict" literal x6 (unchanged
    -- D1 adds no pager verdict format)
  - the cmdline, the md1 volume and usb are the r61 release's own bytes""" % (D1_DAILY[:8], R61_GPU[:8]), 'fb doc laws')
src = sub1(src, """  python3 tools/r61_flash_build.py --daily base/raphael-xnu-r60-flash.img \\
      --gpu base/raphael-xnu-r60-gpu-flash.img \\
      --kernel xnu-src/.../mach.development.bcm2837 \\
      --out-daily raphael-xnu-r54-flash.img --out-gpu raphael-xnu-r54-gpu-flash.img""",
           """  python3 tools/d1_flash_build.py --daily base/raphael-xnu-r61-flash.img \\
      --gpu base/raphael-xnu-r61-gpu-flash.img \\
      --kernel xnu-src/.../mach.development.bcm2837 \\
      --out-daily raphael-xnu-d1-flash.img --out-gpu raphael-xnu-d1-gpu-flash.img""", 'fb doc usage')
src = sub1(src, "R60_DAILY = '%s'" % D1_DAILY, "R61_DAILY = '%s'" % D1_DAILY, 'fb R61_DAILY const')
src = sub1(src, "R60_GPU = '0a0683f1e7771dc99c23446afad42c255b5001137210685a888fd3ab69f296fe'",
           "R61_GPU = '%s'" % R61_GPU, 'fb R61_GPU const')
src = sub1(src, "R60_KERNEL = 'e0570b2adfa46400f38834aab1fa95dc4f838dd18cd700368817535bf8c106a7'",
           "R61_KERNEL = '%s'" % R61_KERNEL, 'fb R61_KERNEL const')
src = sub1(src, "R60_VOLUME = '5b9bbbcb6b5ede61d734bf8144fc639d4e419a22bb81e7d6cf93141f4fbe8746'",
           "R61_VOLUME = '5b9bbbcb6b5ede61d734bf8144fc639d4e419a22bb81e7d6cf93141f4fbe8746'", 'fb R61_VOLUME const')
src = sub1(src, "R61_MARKER = b'%s'" % R61_MARKER.decode(), "D1_MARKER = b'%s'\nR61_MARKER = b'%s'" % (D1_MARKER.decode(), R61_MARKER.decode()), 'fb D1 marker const added, R61 kept')
src = sub1(src, "help=\"this round's kernel (the R52 source + the decoder + the decode census)\")",
           "help=\"this round's kernel (the R61 source + the stall ledger + the dump)\")", 'fb kernel help')
src = sub1(src, "'the R61 kernel keeps the R33 generation markers'", "'the D1 kernel keeps the R33 generation markers'", 'fb r33 mark text 1')
src = sub1(src, "'the R60 kernel keeps the R33 lineage'", "'the D1 kernel keeps the R33 lineage'", 'fb r33 mark text 2')
src = sub1(src, "    assert kernel.count(R61_MARKER) == 1, 'the R61 marker must ride exactly once, got %d' % kernel.count(R61_MARKER)\n"
                "    assert kernel.count(R60_MARKER) == 0, 'the R60 marker must stay retired from the R61 kernel'",
             "    assert kernel.count(D1_MARKER) == 1, 'the D1 marker must ride exactly once, got %d' % kernel.count(D1_MARKER)\n"
             "    assert kernel.count(R61_MARKER) == 0, 'the R61 marker must stay retired from the D1 kernel'\n"
             "    assert kernel.count(R60_MARKER) == 0, 'the R60 marker must stay retired from the D1 kernel'", 'fb marker asserts')
src = sub1(src, "'the R54 marker must stay retired from the R61 kernel'", "'the R54 marker must stay retired from the D1 kernel'", 'fb r54 retired text')
src = subn(src, 'must stay retired from the R60 kernel', 'must stay retired from the D1 kernel', 'fb retired texts', 13)
src = subn(src, 'the R60 kernel must carry', 'the D1 kernel must carry', 'fb must-carry texts', 8)
src = subn(src, 'the R60 kernel lost an R38 mark', 'the D1 kernel lost an R38 mark', 'fb r38 mark text', 1)
src = sub1(src, "    print('R54 kernel: %d B, sha256 %s (R33 lineage + R38 marks + the R54 marker x1 + the sigbirth format x1'",
           "    print('D1 kernel: %d B, sha256 %s (R33 lineage + R38 marks + the D1 marker x1 + the sigbirth format x1'", 'fb kernel print head')
src = subn(src, 'R52/R51/R50/R49/R48/R47/R46/R45/R44/R43/R42/R41/R40', 'R61/R52/R51/R50/R49/R48/R47/R46/R45/R44/R43/R42/R41/R40', 'fb retired list print', 1)
src = sub1(src, "assert sha(open(args.daily, 'rb').read()) == R60_DAILY, 'daily: the base is not the r54 release daily'",
           "assert sha(open(args.daily, 'rb').read()) == R61_DAILY, 'daily: the base is not the r61 release daily'", 'fb daily base assert')
src = sub1(src, "print('daily base: the r54 release daily (loader v7, kernel R33, volume R13-lineage) -- copying byte for byte')",
           "print('daily base: the r61 release daily (loader v7, kernel R33, volume R13-lineage) -- copying byte for byte')", 'fb daily print')
src = sub1(src, "assert sha(open(args.gpu, 'rb').read()) == R60_GPU, 'gpu: the base is not the r54 release GPU image'",
           "assert sha(open(args.gpu, 'rb').read()) == R61_GPU, 'gpu: the base is not the r61 release GPU image'", 'fb gpu base assert')
src = sub1(src, "assert sha(g_ents['kernel']) == R60_KERNEL, 'gpu: the kernel is not the r54 release kernel'",
           "assert sha(g_ents['kernel']) == R61_KERNEL, 'gpu: the kernel is not the r61 release kernel (the D1 gate BASE)'", 'fb gpu kernel assert')
src = sub1(src, "assert sha(g_ents['ramdisk']) == R60_VOLUME, 'gpu: the base ramdisk is not the r39..r54 volume'",
           "assert sha(g_ents['ramdisk']) == R61_VOLUME, 'gpu: the base ramdisk is not the r39..r61 volume'", 'fb gpu volume assert')
src = sub1(src, "print('gpu base: loader 4bf828e4, kernel 36438a97, volume 5b9bbbcb, cmdline = the R39 token set verbatim')",
           "print('gpu base: loader 4bf828e4, kernel 8ef12fef, volume 5b9bbbcb, cmdline = the R39 token set verbatim')", 'fb gpu print')
src = sub1(src, "'the packed ramdisk is not the r39..r54 volume'", "'the packed ramdisk is not the r39..r61 volume'", 'fb reprove ramdisk')
src = sub1(src, "print('re-prove: usb/DTB/loader/cmdline/ramdisk byte-identical to the r54 base; the kernel is the R54 build')",
           "print('re-prove: usb/DTB/loader/cmdline/ramdisk byte-identical to the r61 base; the kernel is the D1 build')", 'fb reprove print')
(TOOLS / 'd1_flash_build.py').write_text(src)
print('tools/d1_flash_build.py written (%d lines)' % len(src.splitlines()))

# ---------------------------------------------------------------- kernel_gate
src = (TOOLS / 'r61_kernel_gate.py').read_text()
src = sub1(src, '"""tools/r61_kernel_gate.py -- the six release laws for the r60 kernel, measured\nat the R52 release kernel:',
           '"""tools/d1_kernel_gate.py -- the six release laws for the d1 kernel, measured\nat the R61 release kernel:', 'gate doc head')
src = sub1(src, """  2. marker:   the R54 marker (SAME byte length as the R52 marker's) is in
               r54 exactly once and in the base zero times; the R52 marker
               is retired (x0 in r60, x1 in the base).""",
           """  2. marker:   the D1 marker (SAME byte length as the R61 marker's, 56 B)
               is in d1 exactly once and in the base zero times; the R61
               marker is retired (x0 in d1, x1 in the base).""", 'gate doc marker law')
src = sub1(src, """  6. symtab:   r54 carries at least as many symbols as the R52 release
               (the decode counters are new data symbols -- none may vanish).""",
           """  6. symtab:   d1 carries at least as many symbols as the R61 release
               (the ledger and the dump are new code symbols -- none may
               vanish).""", 'gate doc symtab law')
src = sub1(src, """  python3 tools/r60_kernel_gate.py --base-kernel base/r54-kernel.bin \\
      --r60-kernel xnu-src/.../mach.development.bcm2837 \\
      [--report release/r60-kernel-gate.txt]""",
           """  python3 tools/d1_kernel_gate.py --base-kernel base/kernel.bin \\
      --r61-kernel xnu-src/.../mach.development.bcm2837 \\
      [--report release/d1-kernel-gate.txt]""", 'gate doc usage')
src = sub1(src, "R61_MARKER = b'%s'" % R61_MARKER.decode(),
           "D1_MARKER = b'%s'\nR61_MARKER = b'%s'" % (D1_MARKER.decode(), R61_MARKER.decode()), 'gate D1 marker const')
src = sub1(src, "assert len(R61_MARKER) == len(R60_MARKER), 'the R61 marker must keep the R60 marker byte length'",
           "assert len(D1_MARKER) == len(R61_MARKER), 'the D1 marker must keep the R61 marker byte length'", 'gate marker length law')
src = sub1(src, "R60_KERNEL = 'e0570b2adfa46400f38834aab1fa95dc4f838dd18cd700368817535bf8c106a7'",
           "R61_KERNEL = '%s'" % R61_KERNEL, 'gate R61_KERNEL pin')
src = sub1(src, "    ap.add_argument('--r60-kernel', required=True)", "    ap.add_argument('--r61-kernel', required=True)", 'gate arg name')
src = sub1(src, "    new = open(args.r60_kernel, 'rb').read()", "    new = open(args.r61_kernel, 'rb').read()", 'gate arg use')
src = sub1(src, "    say('r54 base kernel: %d B, sha256 %s' % (len(old), osha))\n"
             "    assert osha == R60_KERNEL, 'the r54 base kernel is %s, not the release byte %s' % (osha, R60_KERNEL)\n"
             "    say('r60 kernel: %d B, sha256 %s' % (len(new), nsha))",
             "    say('r61 base kernel: %d B, sha256 %s' % (len(old), osha))\n"
             "    assert osha == R61_KERNEL, 'the r61 base kernel is %s, not the release byte %s' % (osha, R61_KERNEL)\n"
             "    say('d1 kernel: %d B, sha256 %s' % (len(new), nsha))", 'gate base say+pin assert')
src = sub1(src, "    assert nsha != osha, 'the r60 kernel is byte-identical to r54 (sha %s) -- the patch did not land?' % nsha",
           "    assert nsha != osha, 'the d1 kernel is byte-identical to the r61 release (sha %s) -- the patch did not land?' % nsha", 'gate size assert')
src = sub1(src, """    assert new.count(R61_MARKER) == 1, 'the R61 marker rides x%d, want exactly 1' % new.count(R61_MARKER)
    assert old.count(R61_MARKER) == 0, 'the r60 base already carries the R61 marker?'
    assert new.count(R54_MARKER) == 0, 'the R54 marker still rides in r61 -- it was to be retired'
    assert old.count(R54_MARKER) == 0, 'the r60 base still carries the retired R54 marker?'
    assert new.count(R60_MARKER) == 0, 'the R60 marker still rides in r61 -- it was to be retired'
    assert old.count(R60_MARKER) == 1, 'the r60 base kernel lost its own marker'
    say('marker law:  the R61 marker x1 in r61, x0 in r60; the R60 marker retired (x0 in r61, x1 in r60); the R54 marker retired in both (x0/x0); same length (56 B)')""",
           """    assert new.count(D1_MARKER) == 1, 'the D1 marker rides x%d, want exactly 1' % new.count(D1_MARKER)
    assert old.count(D1_MARKER) == 0, 'the r61 base already carries the D1 marker?'
    assert new.count(R54_MARKER) == 0, 'the R54 marker still rides in d1 -- it was to be retired'
    assert old.count(R54_MARKER) == 0, 'the r61 base still carries the retired R54 marker?'
    assert new.count(R60_MARKER) == 0, 'the R60 marker still rides in d1 -- it was to be retired'
    assert old.count(R60_MARKER) == 0, 'the r61 base still carries the retired R60 marker?'
    assert new.count(R61_MARKER) == 0, 'the R61 marker still rides in d1 -- it was to be retired'
    assert old.count(R61_MARKER) == 1, 'the r61 base kernel lost its own marker'
    say('marker law:  the D1 marker x1 in d1, x0 in the r61 base; the R61 marker retired (x0 in d1, x1 in the base); R60/R54 retired in both; same length (56 B)')""", 'gate marker law block')
src = sub1(src, "'the r54 base lost one of its two \"frame dropped\" occurrences'", "'the r61 base lost one of its two \"frame dropped\" occurrences'", 'gate frame-dropped base text')
src = subn(src, "'the r54 base lost the sigpost format?'", "'the r61 base lost the sigpost format?'", 'gate sigpost base text', 1)
src = subn(src, "'the r54 base lost the sigbirth format?'", "'the r61 base lost the sigbirth format?'", 'gate sigbirth base text', 1)
src = subn(src, "'the r54 base lost the siglive format?'", "'the r61 base lost the siglive format?'", 'gate siglive base text', 1)
src = subn(src, "'the r54 base lost the sigcensus births format?'", "'the r61 base lost the sigcensus births format?'", 'gate sigcensus base text', 1)
src = subn(src, "'the r54 base lost the r49-deliver format?'", "'the r61 base lost the r49-deliver format?'", 'gate deliver base text', 1)
src = subn(src, "'the r54 base lost the redwood-fault context format?'", "'the r61 base lost the redwood-fault context format?'", 'gate context base text', 1)
src = subn(src, "'the r54 base should carry the redwood-fault literal x2, got x%d' % old.count(HOLOGRAM_FMT)",
           "'the r61 base should carry the redwood-fault literal x2, got x%d' % old.count(HOLOGRAM_FMT)", 'gate hologram base text', 1)
src = subn(src, "'the r54 base lost the registers format?'", "'the r61 base lost the registers format?'", 'gate registers base text', 1)
src = subn(src, "'the r54 base lost its own prefault hologram format?'", "'the r61 base lost its own prefault hologram format?'", 'gate prefault base text', 1)
src = subn(src, "'the r54 base lost its own census tail?'", "'the r61 base lost its own census tail?'", 'gate tail base text', 1)
src = subn(src, "'the r54 base carries no clamp counter symbol?'", "'the r61 base carries no clamp counter symbol?'", 'gate clamp base text', 1)
src = subn(src, "'the r54 base carries no archaeology counter symbol?'", "'the r61 base carries no archaeology counter symbol?'", 'gate archaeology base text', 1)
src = sub1(src, "'the r54 strtab lost the clamp counter symbol (the R51 hint clamp did not stay linked)'",
           "'the d1 strtab lost the clamp counter symbol (the R51 hint clamp did not stay linked)'", 'gate clamp strtab text')
src = sub1(src, "'the r54 strtab lost the archaeology counter symbol (the R52 knife did not stay linked)'",
           "'the d1 strtab lost the archaeology counter symbol (the R52 knife did not stay linked)'", 'gate archaeology strtab text')
src = sub1(src, "'the r60 strtab lost the decode counter symbol (the knife did not link)'",
           "'the d1 strtab lost the decode counter symbol (the knife did not link)'", 'gate decode strtab text')
src = sub1(src, "'the r54 base carries no decode counter symbol?'",
           "'the r61 base carries no decode counter symbol?'", 'gate decode base text')
src = sub1(src, "assert b'loader_log_flush' in new, 'the r54 strtab lost the flush symbol'",
           "assert b'loader_log_flush' in new, 'the d1 strtab lost the flush symbol'", 'gate flush strtab text')
src = sub1(src, "assert b'redwood_fault_seen' in old, 'the r54 base lost the floor-census symbol?'",
           "assert b'redwood_fault_seen' in old, 'the r61 base lost the floor-census symbol?'", 'gate floor base text')
src = sub1(src, "        ' the decode hologram x1 + the decode census line x1 + the decode counter symbol ride in r54 only (the R54 knives);'",
           "        ' the decode hologram x1 + the decode census line x1 + the decode counter symbol ride on BOTH sides (the R54 knives stay);'", 'gate lineage say decode')
src = sub1(src, "'r54 carries FEWER symbols than r52 (%d < %d)' % (sym_n['nsyms'], sym_o['nsyms'])",
           "'d1 carries FEWER symbols than the r61 release (%d < %d)' % (sym_n['nsyms'], sym_o['nsyms'])", 'gate symtab assert')
src = sub1(src, """        # the R61 gains: the evidence round's own words (the hex dump, the per-ask
        # print, the honest ask count on the summary, the shortened NO-ANSWER)
        return s == R61_MARKER or \\""",
           """        # the D1 gains: the stall forensics' own words -- every print the dump
        # makes carries one prefix, so one prefix-mask covers the round
        return s == D1_MARKER or s.startswith(b'adreno: gpu: dump:') or \\""", 'gate allowed_gain head')
src = sub1(src, """        # the R61 losses: the retired R60 marker and the two association strings it
        # replaced (the old summary without the ask count, the stale "three requests")""",
           """        # the D1 losses: the retired R61 marker (the only cstring this round retires)""",
           'gate allowed_loss head')
src = sub1(src, "        return s == R60_MARKER or \\", "        return s == R61_MARKER or \\", 'gate allowed_loss marker')
src = sub1(src, "    assert R61_MARKER in gained, 'the R61 marker is missing from the masked delta'",
           "    assert D1_MARKER in gained, 'the D1 marker is missing from the masked delta'", 'gate gained assert')
src = sub1(src, "    assert R60_MARKER in lost, 'the R60 marker did not show up as a masked loss (it must be retired from rodata)'",
           "    assert R61_MARKER in lost, 'the R61 marker did not show up as a masked loss (it must be retired from rodata)'", 'gate lost assert')
src = sub1(src, """    assert not in_masks(new.find(R61_MARKER), mask_n), \\
        'the R61 marker sits inside a masked span -- smuggled?'""",
           """    assert not in_masks(new.find(D1_MARKER), mask_n), \\
        'the D1 marker sits inside a masked span -- smuggled?'""", 'gate smuggled assert')
src = sub1(src, """    say('strings law: masked delta (code segs masked too -- machine code is not strings, the run-12 law):'
        ' gained %d (the R61 marker + the evidence words + %d Darwin stamp lines + %d layout-run forms),'
        ' lost %d (the R60 marker + the two association strings it replaced + %d Darwin stamp lines + %d layout-run forms); the marker at %#x rides real rodata'
        % (len(gained), sum(1 for s in gained if DARWIN.search(s)), sum(1 for s in gained if run_like(s)),
           len(lost), sum(1 for s in lost if DARWIN.search(s)), sum(1 for s in lost if run_like(s)),
           new.find(R61_MARKER)))""",
           """    say('strings law: masked delta (code segs masked too -- machine code is not strings, the run-12 law):'
        ' gained %d (the D1 marker + the adreno: gpu: dump: words + %d Darwin stamp lines + %d layout-run forms),'
        ' lost %d (the R61 marker + %d Darwin stamp lines + %d layout-run forms); the marker at %#x rides real rodata'
        % (len(gained), sum(1 for s in gained if DARWIN.search(s)), sum(1 for s in gained if run_like(s)),
           len(lost), sum(1 for s in lost if DARWIN.search(s)), sum(1 for s in lost if run_like(s)),
           new.find(D1_MARKER)))""", 'gate strings say')
src = sub1(src, """    say('GATE GREEN: the r54 kernel IS the r52 kernel plus the decoder (the fixup chains,'
        ' turned into real pages before the prefetch) and the decode census (bytes differ, __TEXT +%d B with the payload'
        ' law held, the marker swapped same-length).' % shift)""",
           """    say('GATE GREEN: the d1 kernel IS the r61 kernel plus the stall forensics (the submission ledger,'
        ' the dump at the one exit every stall reports through, the 0x3500 hunt -- bytes differ, __TEXT +%d B with'
        ' the payload law held, the marker swapped same-length).' % shift)""", 'gate green say')
(TOOLS / 'd1_kernel_gate.py').write_text(src)
print('tools/d1_kernel_gate.py written (%d lines)' % len(src.splitlines()))
print('phase 1 done (flash_build + kernel_gate); kernel-id follows in gen_d1_tools2.py')
