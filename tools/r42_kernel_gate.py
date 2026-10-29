#!/usr/bin/env python3
"""tools/r42_kernel_gate.py -- the R42 kernel's gate, against the R41 release kernel.

R42 is the pager-forensics round: the R41 device verdict (4546 logdump lines) put
ONE crasher on the boot -- com.apple.lsd SIGBUS x78 in a 1/s respawn loop, one
frame after backboardd's blocking LaunchServices database fetch failed the same
way; two processes, one file, the mmap page-fill path under suspicion.  The
round's one knife is a NEW kernel file (files/osfmk/vm/redwood_fault_convict.c)
called from THE one exit every user page-fill failure leaves through
(vm_fault_internal()'s final return): the fault names the file it died on, the
first verdicts flush the log themselves (the R41 channel), one boot convicts
the exact file.

So the r41_kernel_gate's content-equivalence stance changes shape ONCE more,
for a reason the R41 laws did not have to face: R41 added ONE CALL that fit
__TEXT padding (delta 0 was the real-build outcome); R42 adds a FUNCTION (plus
five printf format strings), so __TEXT GROWS and every payload segment after
it shifts by exactly that growth.  The laws, re-pointed at the R41 release
kernel:

  1. size:     the bytes MUST differ and the delta stays under 64 KiB.  Unlike
               R41, a POSITIVE delta is the expected outcome here (a new object
               cannot hide in padding); a zero delta would mean the patch did
               not land.
  2. marker:   the R42 marker (SAME byte length as the R41 marker's, so the
               same-length swap still contributes nothing by itself) is in r42
               exactly once and in the base zero times; the R41 marker is
               retired (x0 in r42, x1 in the base).
  3. lineage:  every R33 generation marker, every R38 display/Wi-Fi mark, the
               XNULIVE4 channel mark and the "frame dropped" literal ride in
               r42 (x2, as in both parents); the round's own forensic literal
               "redwood-pager: verdict" rides x4 (the four verdict printf
               formats; the chain line says "redwood-pager:   chain") and x0
               in the base.
  4. segments: the LC_SEGMENT_64 name sequence is identical; __TEXT grows by
               less than 16 KiB; every OTHER payload segment keeps its file
               size and shifts by exactly the __TEXT growth; __LINKEDIT
               absorbs the rest of the file delta (a foreign build moves
               payload segment SIZES, not just offsets).
  5. strings:  computed with the volatile regions MASKED OUT of both files
               (the load commands + the SYMTAB nlist/strtab span): every string
               r42 gains is the R42 marker, a "redwood-pager:" format, or a
               Darwin version line (the wall-clock build stamp); every string
               it loses is the R41 marker or a Darwin version line.  The
               marker must live OUTSIDE the masked spans (real __TEXT/__DATA
               rodata, not smuggled into the symbol table).
  6. symtab:   r42 carries at least as many symbols as the R41 release kernel
               (strictly more expected: the new function and its statics).

usage:
  python3 tools/r42_kernel_gate.py --r41-kernel base/r41-kernel.bin \
      --r42-kernel xnu-src/.../mach.development.bcm2837 \
      [--report release/r42-kernel-gate.txt]
"""
import argparse
import hashlib
import re
import struct
import sys

R42_MARKER = b'R42: the fault names the file that died, pager forensics'
R41_MARKER = b'R41: the snapshot buys its own ride onto the storage too'
R42_MARKER_LEN_LAW = len(R42_MARKER) == len(R41_MARKER)
assert R42_MARKER_LEN_LAW, 'the R42 marker must keep the R41 marker byte length'
REPORT_LITERAL = b'frame dropped'
PAGER_LITERAL = b'redwood-pager: verdict'

R41_KERNEL = 'f17119fba03a220cf6288bcdc3f11fbd0f847446c70fe59155705d891dc48d22'

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
    ap.add_argument('--r41-kernel', required=True)
    ap.add_argument('--r42-kernel', required=True)
    ap.add_argument('--report', default=None)
    args = ap.parse_args()

    old = open(args.r41_kernel, 'rb').read()
    new = open(args.r42_kernel, 'rb').read()
    lines = []

    def say(s):
        print(s)
        lines.append(s)

    osha, nsha = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    say('r41 kernel: %d B, sha256 %s' % (len(old), osha))
    assert osha == R41_KERNEL, 'the r41 base kernel is %s, not the release byte %s' % (osha, R41_KERNEL)
    say('r42 kernel: %d B, sha256 %s' % (len(new), nsha))

    lc_end_o, segs_o, sym_o = macho_layouts(old)
    lc_end_n, segs_n, sym_n = macho_layouts(new)

    # 1. size (R41's delta-0 outcome does NOT carry: a new FUNCTION cannot fit
    #    __TEXT padding -- a positive delta is the expected real-build outcome)
    delta = len(new) - len(old)
    assert nsha != osha, 'the r42 kernel is byte-identical to r41 (sha %s) -- the patch did not land?' % nsha
    assert abs(delta) < 65536, 'the size delta %d B exceeds the 64 KiB patch band' % delta
    say('size law:    bytes differ (new sha), delta %+d B -- the band is |d| < 64 KiB;'
        ' delta > 0 is the expected real-build outcome (a new function and its'
        ' format strings grow __TEXT, the same-length marker swap contributes nothing)' % delta)

    # 2. the markers
    assert new.count(R42_MARKER) == 1, 'the R42 marker rides x%d, want exactly 1' % new.count(R42_MARKER)
    assert old.count(R42_MARKER) == 0, 'the r41 kernel already carries the R42 marker?'
    assert new.count(R41_MARKER) == 0, 'the R41 marker still rides in r42 -- it was to be retired'
    assert old.count(R41_MARKER) == 1, 'the r41 base kernel lost its own marker'
    say('marker law:  the R42 marker x1 in r42, x0 in r41; the R41 marker retired (x0 in r42, x1 in r41)')

    # 3. lineage
    for mark, want in R33_MARKS:
        got = new.count(mark)
        assert got == want, 'R33 mark %s x%d != %d' % (mark.decode(), got, want)
    for mark in R38_MARKS:
        assert mark in new, 'the R38 mark is missing: %s' % mark.decode()
    assert new.count(XNULIVE4) >= 1, 'the XNULIVE4 channel mark is missing'
    assert new.count(REPORT_LITERAL) == 2, 'the forensic literal "frame dropped" x%d != 2 (the swap-timeout IOLog format string + the report literal)' % new.count(REPORT_LITERAL)
    assert old.count(REPORT_LITERAL) == 2, 'the r41 base lost one of its two "frame dropped" occurrences'
    assert new.count(PAGER_LITERAL) == 4, 'the forensic literal "redwood-pager: verdict" x%d != 4 (the four verdict printf formats)' % new.count(PAGER_LITERAL)
    assert old.count(PAGER_LITERAL) == 0, 'the r41 base already carries the pager literal?'
    say('lineage law: R33 generation 4 + R38 display/WiFi 5 + XNULIVE4 + the "frame dropped" literal'
        ' all ride; the "redwood-pager: verdict" literal rides x4')

    # 4. segment census (the R42 shape: __TEXT grows, the rest shifts, sizes pinned)
    names_o = [s[0] for s in segs_o]
    names_n = [s[0] for s in segs_n]
    assert names_o == names_n, 'the LC_SEGMENT_64 name sequence changed: %s -> %s' % (names_o, names_n)
    text_o = [s for s in segs_o if s[0] == b'__TEXT'][0]
    text_n = [s for s in segs_n if s[0] == b'__TEXT'][0]
    shift = text_n[2] - text_o[2]
    # The real build may absorb the whole round in __TEXT's tail padding: R41's
    # delta-0 was the real outcome, and R42's run#7 (40895517421) measured it
    # again -- the new function and its format strings fit the page-aligned
    # slack, __TEXT fileoff/filesize untouched, the +336 B all being __LINKEDIT
    # symbol/string growth.  The law is the ACCOUNTING, not the sign: __TEXT
    # must not shrink, the mid segments keep their sizes and shift by the same
    # amount, and __LINKEDIT takes exactly the rest (asserted below).
    assert shift >= 0, '__TEXT shrank %d B -- nothing in this round can shrink it' % shift
    assert shift < 16384, '__TEXT grew %d B, past the 16 KiB forensics band' % shift
    moved = []
    for (name_o, fo_o, fs_o), (name_n, fo_n, fs_n) in zip(segs_o, segs_n):
        if name_o == b'__TEXT':
            continue
        if name_o == b'__LINKEDIT':
            if fo_n != fo_o + shift:
                moved.append((name_o.decode(), 'fileoff', fo_o, fo_n, 'want +shift'))
            continue
        if fs_n != fs_o or fo_n != fo_o + shift:
            moved.append((name_o.decode(), fo_o, fs_o, fo_n, fs_n))
    assert not moved, 'payload segments did not follow the __TEXT growth law: %s' % moved[:4]
    link_o = [s for s in segs_o if s[0] == b'__LINKEDIT'][0]
    link_n = [s for s in segs_n if s[0] == b'__LINKEDIT'][0]
    assert link_n[2] - link_o[2] == delta - shift, \
        'file delta %d != __TEXT %d + __LINKEDIT %d -- something else grew' % (delta, shift, link_n[2] - link_o[2])
    say('segment law: %d segments, name sequence identical; __TEXT +%d B (< 16 KiB); every other'
        ' payload segment keeps its size and shifts by exactly +%d; __LINKEDIT +%d B == the rest'
        % (len(segs_n), shift, shift, link_n[2] - link_o[2]))

    # 5. strings delta, volatile regions masked
    mask_o = [(0, lc_end_o), (sym_o['symoff'], len(old))]
    mask_n = [(0, lc_end_n), (sym_n['symoff'], len(new))]
    o_str = masked_strings(old, mask_o)
    n_str = masked_strings(new, mask_n)
    gained, lost = n_str - o_str, o_str - n_str

    def allowed_gain(s):
        return s == R42_MARKER or s.startswith(b'redwood-pager:') or DARWIN.search(s)

    bad_gain = [s for s in gained if not allowed_gain(s)]
    bad_loss = [s for s in lost if not (s == R41_MARKER or DARWIN.search(s))]
    assert not bad_gain, 'r42 gains unexpected strings (outside symtab/load cmds): %s' % [s[:64] for s in bad_gain[:5]]
    assert not bad_loss, 'r42 loses unexpected strings: %s' % [s[:64] for s in bad_loss[:5]]
    assert R42_MARKER in gained, 'the R42 marker is missing from the masked delta'
    assert R41_MARKER in lost, 'the R41 marker did not show up as a masked loss (it must be retired from rodata)'
    assert not in_masks(new.find(R42_MARKER), mask_n), \
        'the R42 marker sits inside a masked span -- smuggled?'
    say('strings law: masked delta: gained %d (the R42 marker + the redwood-pager formats + %d Darwin stamp lines),'
        ' lost %d (the R41 marker + Darwin stamps); the marker at %#x rides real rodata'
        % (len(gained), sum(1 for s in gained if DARWIN.search(s)), len(lost), new.find(R42_MARKER)))

    # 6. symtab census
    assert sym_n['nsyms'] >= sym_o['nsyms'], \
        'r42 carries FEWER symbols than r41 (%d < %d)' % (sym_n['nsyms'], sym_o['nsyms'])
    say('symtab law:  nsyms %d -> %d (strtab %#x -> %#x)'
        % (sym_o['nsyms'], sym_n['nsyms'], sym_o['strsize'], sym_n['strsize']))

    say('GATE GREEN: the r42 kernel IS the r41 kernel plus the pager forensics'
        ' (bytes differ, __TEXT +%d B with the payload law held, the marker swapped same-length).' % shift)
    if args.report:
        with open(args.report, 'w') as f:
            f.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
