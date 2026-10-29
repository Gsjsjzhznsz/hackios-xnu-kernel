#!/usr/bin/env python3
"""tools/r41_kernel_gate.py -- the R41 kernel's gate, against the R40 release kernel.

R41 changes the R40 forensic round in ONE way: redwood_gpu_report() now asks the
loader to put the log on the storage (loader_log_flush) right after printing the
snapshot.  R40's device capture proved WHY that is needed: the loader's own 2-second
notes end at ~34 s (NOTE_SECONDS), and after that the logdump partition only takes
the log from a kernel-asked flush -- which nothing on the swap/GPU path ever made.
R40's own record ends at ~44 s of kernel time, far before the ~124 s stall it was
built to catch: the report printed into the ring and died there.  (R39's record
reached the storage at all only because the exit gesture's teardown flushes.)

So the r34_shadiff --gate content-equivalence verdict still cannot apply: this
round's kernel is SUPPOSED to differ, by exactly one flush call plus the marker
swap.  The laws are the R40 gate's (r40_kernel_gate.py), re-pointed at the R40
release kernel, with two deltas the R41 patch dictates:

  - the marker: the R41 marker (SAME byte length as the R40 marker's, so __TEXT's
    and __rodata's sizes do not move -- the segment census law survives) replaces
    the R40 marker, which must be GONE;
  - the strings law: the forensic literal "frame dropped" must ride in BOTH files
    (R40 introduced it; R41 keeps it), so it leaves the gained-set and joins the
    both-files assertions instead.

  1. size:      r41 > r40 and the delta stays under 64 KiB (R40's real build was
                +128 B, ALL of it __LINKEDIT growth; R41 adds one call -- the call
                target loader_log_flush is already extern'd, so no new symbol)
  2. marker:    the R41 marker is in r41 exactly once and in r40 zero times; the
                R40 marker is in r41 ZERO times (retired)
  3. lineage:   every R33 generation marker, every R38 display/Wi-Fi mark, the
                XNULIVE4 channel mark and the "frame dropped" literal ride in r41
  4. segments:  the LC_SEGMENT_64 name sequence is identical; every payload segment's
                fileoff/filesize is byte-identical; only __LINKEDIT may grow, by
                exactly the file delta (a wrong/foreign build moves payload segments)
  5. strings:   computed with the volatile regions MASKED OUT of both files (the load
                commands + the SYMTAB nlist/strtab span): every string r41 gains is
                the R41 marker or a Darwin version line (the wall-clock build stamp);
                every string it loses is the R40 marker or a Darwin version line.
                Both markers must live OUTSIDE the masked spans (real __TEXT/__DATA
                rodata, not smuggled into the symbol table).
  6. symtab:    r41 carries at least as many symbols as r40.

usage:
  python3 tools/r41_kernel_gate.py --r40-kernel base/r40-kernel.bin \
      --r41-kernel xnu-src/.../mach.development.bcm2837 \
      [--report release/r41-kernel-gate.txt]
"""
import argparse
import hashlib
import re
import struct
import sys

R41_MARKER = b'R41: the snapshot buys its own ride onto the storage too'
R40_MARKER = b'R40: the first dropped frame buys the full core snapshot'
R41_MARKER_LEN_LAW = len(R41_MARKER) == len(R40_MARKER)
assert R41_MARKER_LEN_LAW, 'the R41 marker must keep the R40 marker byte length'
REPORT_LITERAL = b'frame dropped'

R40_KERNEL = 'dc03efe0d0e837cf694808dbeda0be4197ac9e701d0420ac098f61c7ced77028'

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
    ap.add_argument('--r40-kernel', required=True)
    ap.add_argument('--r41-kernel', required=True)
    ap.add_argument('--report', default=None)
    args = ap.parse_args()

    old = open(args.r40_kernel, 'rb').read()
    new = open(args.r41_kernel, 'rb').read()
    lines = []

    def say(s):
        print(s)
        lines.append(s)

    osha, nsha = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    say('r40 kernel: %d B, sha256 %s' % (len(old), osha))
    assert osha == R40_KERNEL, 'the r40 base kernel is %s, not the release byte %s' % (osha, R40_KERNEL)
    say('r41 kernel: %d B, sha256 %s' % (len(new), nsha))

    lc_end_o, segs_o, sym_o = macho_layouts(old)
    lc_end_n, segs_n, sym_n = macho_layouts(new)

    # 1. size
    delta = len(new) - len(old)
    assert delta > 0, 'the r41 kernel is not bigger than r40 (delta %d) -- the patch did not land?' % delta
    assert delta < 65536, 'the size delta %d B exceeds the 64 KiB patch band' % delta
    say('size law:    +%d B (the patch band is 0 < d < 64 KiB)' % delta)

    # 2. the markers
    assert new.count(R41_MARKER) == 1, 'the R41 marker rides x%d, want exactly 1' % new.count(R41_MARKER)
    assert old.count(R41_MARKER) == 0, 'the r40 kernel already carries the R41 marker?'
    assert new.count(R40_MARKER) == 0, 'the R40 marker still rides in r41 -- it was to be retired'
    assert old.count(R40_MARKER) == 1, 'the r40 base kernel lost its own marker'
    say('marker law:  the R41 marker x1 in r41, x0 in r40; the R40 marker retired (x0 in r41, x1 in r40)')

    # 3. lineage
    for mark, want in R33_MARKS:
        got = new.count(mark)
        assert got == want, 'R33 mark %s x%d != %d' % (mark.decode(), got, want)
    for mark in R38_MARKS:
        assert mark in new, 'the R38 mark is missing: %s' % mark.decode()
    assert new.count(XNULIVE4) >= 1, 'the XNULIVE4 channel mark is missing'
    assert new.count(REPORT_LITERAL) == 2, 'the forensic literal "frame dropped" x%d != 2 (the swap-timeout IOLog format string + the report literal)' % new.count(REPORT_LITERAL)
    assert old.count(REPORT_LITERAL) == 2, 'the r40 base lost one of its two "frame dropped" occurrences'
    say('lineage law: R33 generation 4 + R38 display/WiFi 5 + XNULIVE4 + the "frame dropped" literal all ride')

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
        return s == R41_MARKER or DARWIN.search(s)

    bad_gain = [s for s in gained if not allowed_gain(s)]
    bad_loss = [s for s in lost if not (s == R40_MARKER or DARWIN.search(s))]
    assert not bad_gain, 'r41 gains unexpected strings (outside symtab/load cmds): %s' % [s[:64] for s in bad_gain[:5]]
    assert not bad_loss, 'r41 loses unexpected strings: %s' % [s[:64] for s in bad_loss[:5]]
    assert R41_MARKER in gained, 'the R41 marker is missing from the masked delta'
    assert R40_MARKER in lost, 'the R40 marker did not show up as a masked loss (it must be retired from rodata)'
    assert not in_masks(new.find(R41_MARKER), mask_n), \
        'the R41 marker sits inside a masked span -- smuggled?'
    say('strings law: masked delta: gained %d (the R41 marker + %d Darwin stamp lines), lost %d'
        ' (the R40 marker + Darwin stamps); the marker at %#x rides real rodata'
        % (len(gained), len(gained) - 1, len(lost), new.find(R41_MARKER)))

    # 6. symtab census
    assert sym_n['nsyms'] >= sym_o['nsyms'], \
        'r41 carries FEWER symbols than r40 (%d < %d)' % (sym_n['nsyms'], sym_o['nsyms'])
    say('symtab law:  nsyms %d -> %d (strtab %#x -> %#x)'
        % (sym_o['nsyms'], sym_n['nsyms'], sym_o['strsize'], sym_n['strsize']))

    say('GATE GREEN: the r41 kernel IS the r40 kernel plus one flush call'
        ' (payload layout pinned, only the symbol table grew, the marker swapped same-length).')
    if args.report:
        with open(args.report, 'w') as f:
            f.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
