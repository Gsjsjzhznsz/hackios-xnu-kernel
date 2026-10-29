#!/usr/bin/env python3
"""tools/r40_kernel_gate.py -- the R40 kernel's gate, against the R39 release kernel.

R40 changes ONE kernel file (MiniIOMobileFramebuffer.cpp): the first dropped frame now
calls redwood_gpu_report("frame dropped") -- the R39 autopsy's own verdict on its own
forensic gap (a 500 ms swap wait sits below gpuWait's 1000 ms report bar, so the stalled
core died silently: CP_RB_RPTR stuck at 1488 of 2439, RBBM_INT_0_STATUS 0, nothing on
the log but the drops).  The build also carries the round's used-marker string.

So the r34_shadiff --gate content-equivalence verdict (identical size, diffs confined
to the UUID + version-timestamp clusters) CANNOT apply: this round's kernel is SUPPOSED
to differ, by exactly one patch.  And a NAIVE "the diff clusters in <= 32 regions"
verdict cannot apply EITHER -- that law was drafted on a STUB (r39 bytes + one appended
string) and died on its first contact with a REAL build (CI run 40826230850): one patch
re-lengthens one section, every following section's addr/size/offset shifts, and the
linker re-points every absolute pointer -- 541,900 differing regions / ~5 MB, 99.99% of
it compiler-and-linker bookkeeping.  LESSON (recorded 2026-10-29, real-artifact bytes
release/r40-kernel-stripped.bin = dd5b7d43... vs base/r39-kernel.bin = 24ec4e42...):
a gate law drafted on synthetic bytes must be validated on real build output before it
rides in CI.  The laws below are what the REAL bytes support:

  1. size:      r40 > r39 and the delta stays under 64 KiB (observed +128 B, ALL of it
                __LINKEDIT symbol-table growth -- the patch's code fits existing slack)
  2. marker:    the R40 marker is in r40 exactly once and in r39 zero times
  3. lineage:   every R33 generation marker and every R38 display/Wi-Fi mark rides in r40
  4. segments:  the LC_SEGMENT_64 name sequence is identical; every payload segment's
                fileoff/filesize is byte-identical; only __LINKEDIT may grow, by exactly
                the file delta (a wrong/foreign build moves payload segments)
  5. strings:   computed with the volatile regions MASKED OUT of both files (the load
                commands + the SYMTAB nlist/strtab span): every string r40 gains is
                either the R40 marker, the patch's own report literal "frame dropped",
                or a Darwin version line (the wall-clock build stamp); every string it
                loses is a Darwin version line.  The R40 marker must live OUTSIDE the
                masked spans (real __TEXT/__DATA rodata, not smuggled into the symbol
                table).  (The report literal surfaced on the REAL CI bytes -- the stub
                never had it; validated 2026-10-29, run 40826230850's kernel.)
  6. symtab:    r40 carries at least as many symbols as r39 (the patch adds statics).

usage:
  python3 tools/r40_kernel_gate.py --r39-kernel base/r39-kernel.bin \
      --r40-kernel xnu-src/.../mach.development.bcm2837 \
      [--report release/r40-kernel-gate.txt]
"""
import argparse
import hashlib
import re
import struct
import sys

R40_MARKER = b'R40: the first dropped frame buys the full core snapshot'
# the patch's own forensic literal: show() calls redwood_gpu_report("frame dropped")
R40_REPORT_LITERAL = b'frame dropped'

R39_KERNEL = '24ec4e421c3fb760972af3f97324f2c3bbe490c441239c7f8139d1914a626f16'

R33_MARKS = (
    (b'R33: the kernel generation line', 2),
    (b'R33: the resident round', 1),
    (b'THE GPU STAYS ON for the system to draw with', 1),
    (b'SCMDMP: R33 begins', 1),
)
R38_MARKS = (
    b'raphael-display: three scanout buffers, VIG0/CTL0, hardware TE',
    b'wifi: raphael needs WCN3990 SNOC/ath10k and MPSS service 0x45',
    b'display: raphael command TE:',
    b'page flips retained (fb_scan=1)',
    b'raphael-display: flip did not latch at TE, restoring boot framebuffer',
)
XNULIVE4 = b'XNULIVE4'
DARWIN = re.compile(rb'Darwin Kernel Version')

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2


def macho_layouts(d):
    """Parse the 64-bit Mach-O: (lc_end, segments[(name, fileoff, filesize)], symtab dict)."""
    magic, = struct.unpack_from('<I', d, 0)
    assert magic == 0xfeedfacf, 'not a 64-bit Mach-O (magic %#x)' % magic
    ncmds, sizeofcmds = struct.unpack_from('<II', d, 16)
    lc_end = 32 + sizeofcmds
    off = 32
    segs, sym = [], None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', d, off)
        if cmd == LC_SEGMENT_64:
            name = d[off + 8:off + 24].split(b'\0')[0]
            fileoff, filesize = struct.unpack_from('<QQ', d, off + 40)
            segs.append((name, fileoff, filesize))
        elif cmd == LC_SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from('<IIII', d, off + 8)
            sym = {'symoff': symoff, 'nsyms': nsyms, 'stroff': stroff, 'strsize': strsize}
        off += size
    assert sym, 'no LC_SYMTAB'
    assert sym['stroff'] + sym['strsize'] <= len(d), 'symtab strtab runs past EOF'
    return lc_end, segs, sym


def masked_strings(d, masks, minimum=8):
    """Printable runs >= minimum, with every masked span zeroed first."""
    buf = bytearray(d)
    for a, b in masks:
        buf[a:b] = b'\x00' * (b - a)
    return set(re.findall(rb'[\x20-\x7e]{%d,}' % minimum, bytes(buf)))


def in_masks(off, masks):
    return any(a <= off < b for a, b in masks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--r39-kernel', required=True)
    ap.add_argument('--r40-kernel', required=True)
    ap.add_argument('--report', default=None)
    args = ap.parse_args()

    old = open(args.r39_kernel, 'rb').read()
    new = open(args.r40_kernel, 'rb').read()
    lines = []

    def say(s):
        print(s)
        lines.append(s)

    osha, nsha = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    say('r39 kernel: %d B, sha256 %s' % (len(old), osha))
    assert osha == R39_KERNEL, 'the r39 base kernel is %s, not the release byte %s' % (osha, R39_KERNEL)
    say('r40 kernel: %d B, sha256 %s' % (len(new), nsha))

    lc_end_o, segs_o, sym_o = macho_layouts(old)
    lc_end_n, segs_n, sym_n = macho_layouts(new)

    # 1. size
    delta = len(new) - len(old)
    assert delta > 0, 'the r40 kernel is not bigger than r39 (delta %d) -- the patch did not land?' % delta
    assert delta < 65536, 'the size delta %d B exceeds the 64 KiB patch band' % delta
    say('size law:    +%d B (the patch band is 0 < d < 64 KiB)' % delta)

    # 2. the R40 marker
    assert new.count(R40_MARKER) == 1, 'the R40 marker rides x%d, want exactly 1' % new.count(R40_MARKER)
    assert old.count(R40_MARKER) == 0, 'the R39 kernel already carries the R40 marker?'
    say('marker law:  the R40 marker x1 in r40, x0 in r39')

    # 3. lineage
    for mark, want in R33_MARKS:
        got = new.count(mark)
        assert got == want, 'R33 mark %s x%d != %d' % (mark.decode(), got, want)
    for mark in R38_MARKS:
        assert mark in new, 'the R38 mark is missing: %s' % mark.decode()
    assert new.count(XNULIVE4) >= 1, 'the XNULIVE4 channel mark is missing'
    say('lineage law: R33 generation 4 + R38 display/WiFi 5 + XNULIVE4 all ride')

    # 4. segment census (the law the real CI bytes taught us)
    names_o = [s[0] for s in segs_o]
    names_n = [s[0] for s in segs_n]
    assert names_o == names_n, 'the LC_SEGMENT_64 name sequence changed: %s -> %s' % (names_o, names_n)
    moved = []
    for (name_o, fo_o, fs_o), (name_n, fo_n, fs_n) in zip(segs_o, segs_n):
        if name_o == b'__LINKEDIT':
            continue
        if (fo_o, fs_o) != (fo_n, fs_n):
            moved.append((name_o.decode(), fo_o, fs_o, fo_n, fs_n))
    assert not moved, 'payload segments moved (a foreign build does this): %s' % moved[:4]
    link_o = [s for s in segs_o if s[0] == b'__LINKEDIT'][0]
    link_n = [s for s in segs_n if s[0] == b'__LINKEDIT'][0]
    assert link_o[1] == link_n[1], '__LINKEDIT fileoff moved'
    assert link_n[2] - link_o[2] == delta, \
        'file delta %d != __LINKEDIT delta %d -- payload grew, not just symbols' % (delta, link_n[2] - link_o[2])
    say('segment law: %d segments, name sequence identical; every payload segment'
        ' fileoff/filesize byte-identical; __LINKEDIT +%d B == the file delta'
        % (len(segs_n), link_n[2] - link_o[2]))

    # 5. strings delta, volatile regions masked
    mask_o = [(0, lc_end_o), (sym_o['symoff'], len(old))]
    mask_n = [(0, lc_end_n), (sym_n['symoff'], len(new))]
    o_str = masked_strings(old, mask_o)
    n_str = masked_strings(new, mask_n)
    gained, lost = n_str - o_str, o_str - n_str
    def allowed_gain(s):
        return s == R40_MARKER or s == R40_REPORT_LITERAL or DARWIN.search(s)

    bad_gain = [s for s in gained if not allowed_gain(s)]
    bad_loss = [s for s in lost if not DARWIN.search(s)]
    assert not bad_gain, 'r40 gains unexpected strings (outside symtab/load cmds): %s' % [s[:64] for s in bad_gain[:5]]
    assert not bad_loss, 'r40 loses unexpected strings: %s' % [s[:64] for s in bad_loss[:5]]
    assert R40_REPORT_LITERAL in gained, \
        'the report literal "frame dropped" is missing from the masked delta'
    assert not in_masks(new.find(R40_REPORT_LITERAL), mask_n), \
        'the report literal sits inside a masked span -- smuggled?'
    marker_at = new.find(R40_MARKER)
    assert not in_masks(marker_at, mask_n), \
        'the R40 marker sits inside a masked span (symtab/load cmds) at %#x -- smuggled?' % marker_at
    say('strings law: masked delta: gained %d (the R40 marker + the report literal'
        ' "frame dropped" + %d Darwin stamp lines), lost %d (all Darwin stamps);'
        ' marker at %#x rides real rodata'
        % (len(gained), len(gained) - 2, len(lost), marker_at))

    # 6. symtab census
    assert sym_n['nsyms'] >= sym_o['nsyms'], \
        'r40 carries FEWER symbols than r39 (%d < %d)' % (sym_n['nsyms'], sym_o['nsyms'])
    say('symtab law:  nsyms %d -> %d (the patch adds statics; strtab %#x -> %#x)'
        % (sym_o['nsyms'], sym_n['nsyms'], sym_o['strsize'], sym_n['strsize']))

    say('GATE GREEN: the r40 kernel IS the r39 kernel plus one forensic patch'
        ' (payload layout pinned, only the symbol table grew).')
    if args.report:
        with open(args.report, 'w') as f:
            f.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
