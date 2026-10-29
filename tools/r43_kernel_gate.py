#!/usr/bin/env python3
"""tools/r43_kernel_gate.py -- the R43 kernel's gate, against the R42 release kernel.

R43 is the try-lock probe round: the R42 device verdict (5600 logdump lines) proved
the forensics chain ALIVE on the device (160 verdicts, throttle + flush all worked)
and killed two assumptions at once.  (a) The 160 verdicts were ALL "no map entry"
benign probes -- not one convicts lsd, whose SIGBUS loop (7 ms per death, "sent by
exc handler") never passes vm_fault_internal's failure exit; the page-fill
hypothesis is REFUTED (the in-log R28d note points at the sandbox-refusal path --
R44's lead).  (b) The black screen was OURS: the probe's shadow-chain loop
pre-locked the next object at the tail AND locked it again at the top, so the
first chain deeper than one object (SpringBoard's IOSurface shadow) took the same
object's exclusive lck_rw twice on one thread -- lock_rw.c:877 "Lock already held,
owner == self" -- and SpringBoard panicked.  R43's one knife: every lock is a TRY
(vm_map_try_lock_read, vm_object_lock_try_scan -- both verified non-blocking and
assert-free in the xnu-8796 sources), each chain level locked exactly ONCE, and a
busy lock prints a minimal verdict and walks away.  Two NEW verdict formats join
the four (map lock busy / chain lock busy), so the pager literal rides x6.

The gate laws keep the R42 shape (the ACCOUNTING, not the sign -- the R42 lesson:
assert what must be true of every legal shape, never the round's expected
magnitude), re-pointed at the R42 release kernel:

  1. size:     the bytes MUST differ; the delta stays under 64 KiB; zero or
               positive are both legal (the probe's lock edits may fit __TEXT
               padding the way R41's call did).
  2. marker:   the R43 marker (SAME byte length as the R42 marker's) is in r43
               exactly once and in the base zero times; the R42 marker is
               retired (x0 in r43, x1 in the base).
  3. lineage:  every R33 generation marker, every R38 display/Wi-Fi mark, the
               XNULIVE4 channel mark and the "frame dropped" literal ride in
               r43; the forensic literal "redwood-pager: verdict" rides x6
               (the four R42 verdict formats + the two R43 busy formats)
               and x4 in the base (the base itself is the R42 release, which
               carries the four R42 verdict formats).
  4. segments: the LC_SEGMENT_64 name sequence is identical; __TEXT must not
               shrink and stays under 16 KiB of growth; every OTHER payload
               segment keeps its file size and shifts by exactly the __TEXT
               growth; __LINKEDIT absorbs the rest of the file delta.
  5. strings:  computed with the volatile regions MASKED OUT: every string r43
               gains is the R43 marker, a "redwood-pager:" format, or a Darwin
               version line; every string it loses is the R42 marker or a
               Darwin line.  The marker must live OUTSIDE the masked spans.
  6. symtab:   r43 carries at least as many symbols as the R42 release kernel.

usage:
  python3 tools/r43_kernel_gate.py --r42-kernel base/r42-kernel.bin \
      --r43-kernel xnu-src/.../mach.development.bcm2837 \
      [--report release/r43-kernel-gate.txt]
"""
import argparse
import hashlib
import re
import struct
import sys

R43_MARKER = b'R43: the probe try-locks first or walks away, no recurse'
R42_MARKER = b'R42: the fault names the file that died, pager forensics'
R43_MARKER_LEN_LAW = len(R43_MARKER) == len(R42_MARKER)
assert R43_MARKER_LEN_LAW, 'the R43 marker must keep the R42 marker byte length'
REPORT_LITERAL = b'frame dropped'
PAGER_LITERAL = b'redwood-pager: verdict'

R42_KERNEL = 'ef204f284b5825d3598d7b2630159945dbb11e990f0eac75bcbff30f3f15c021'

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
    ap.add_argument('--r42-kernel', required=True)
    ap.add_argument('--r43-kernel', required=True)
    ap.add_argument('--report', default=None)
    args = ap.parse_args()

    old = open(args.r42_kernel, 'rb').read()
    new = open(args.r43_kernel, 'rb').read()
    lines = []

    def say(s):
        print(s)
        lines.append(s)

    osha, nsha = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    say('r42 base kernel: %d B, sha256 %s' % (len(old), osha))
    assert osha == R42_KERNEL, 'the r42 base kernel is %s, not the release byte %s' % (osha, R42_KERNEL)
    say('r43 kernel: %d B, sha256 %s' % (len(new), nsha))

    lc_end_o, segs_o, sym_o = macho_layouts(old)
    lc_end_n, segs_n, sym_n = macho_layouts(new)

    # 1. size (the R42 lesson: the ACCOUNTING, not the sign -- the probe's lock
    #    edits may fit __TEXT padding, so delta 0 is a legal outcome)
    delta = len(new) - len(old)
    assert nsha != osha, 'the r43 kernel is byte-identical to r42 (sha %s) -- the patch did not land?' % nsha
    assert abs(delta) < 65536, 'the size delta %d B exceeds the 64 KiB patch band' % delta
    say('size law:    bytes differ (new sha), delta %+d B -- the band is |d| < 64 KiB;'
        ' zero and positive are both legal here (the probe\'s lock edits may fit'
        ' __TEXT padding; the law is the accounting, not the sign)' % delta)

    # 2. the markers
    assert new.count(R43_MARKER) == 1, 'the R43 marker rides x%d, want exactly 1' % new.count(R43_MARKER)
    assert old.count(R43_MARKER) == 0, 'the r42 kernel already carries the R43 marker?'
    assert new.count(R42_MARKER) == 0, 'the R42 marker still rides in r43 -- it was to be retired'
    assert old.count(R42_MARKER) == 1, 'the r42 base kernel lost its own marker'
    say('marker law:  the R43 marker x1 in r43, x0 in r42; the R42 marker retired (x0 in r43, x1 in r42)')

    # 3. lineage
    for mark, want in R33_MARKS:
        got = new.count(mark)
        assert got == want, 'R33 mark %s x%d != %d' % (mark.decode(), got, want)
    for mark in R38_MARKS:
        assert mark in new, 'the R38 mark is missing: %s' % mark.decode()
    assert new.count(XNULIVE4) >= 1, 'the XNULIVE4 channel mark is missing'
    assert new.count(REPORT_LITERAL) == 2, 'the forensic literal "frame dropped" x%d != 2 (the swap-timeout IOLog format string + the report literal)' % new.count(REPORT_LITERAL)
    assert old.count(REPORT_LITERAL) == 2, 'the r42 base lost one of its two "frame dropped" occurrences'
    assert new.count(PAGER_LITERAL) == 6, 'the forensic literal "redwood-pager: verdict" x%d != 6 (the four R42 verdict formats + the two R43 busy formats)' % new.count(PAGER_LITERAL)
    # THE RUN-8 LESSON (40917994778): this assert shipped as "old.count == 0" --
    # the R42-era copy-paste.  But old IS the R42 release kernel, and the R42
    # round is exactly where the four pager verdict formats landed: the base
    # carries x4, and this round's law is 4 -> 6, never 0 -> 6.
    assert old.count(PAGER_LITERAL) == 4, 'the r42 base should carry the pager literal x4 (the four R42 verdict formats), got x%d' % old.count(PAGER_LITERAL)
    say('lineage law: R33 generation 4 + R38 display/WiFi 5 + XNULIVE4 + the "frame dropped" literal'
        ' all ride; the "redwood-pager: verdict" literal rides x6 (x4 in the r42 base + the two new busy formats)')

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
        return s == R43_MARKER or s.startswith(b'redwood-pager:') or DARWIN.search(s)

    bad_gain = [s for s in gained if not allowed_gain(s)]
    bad_loss = [s for s in lost if not (s == R42_MARKER or DARWIN.search(s))]
    assert not bad_gain, 'r43 gains unexpected strings (outside symtab/load cmds): %s' % [s[:64] for s in bad_gain[:5]]
    assert not bad_loss, 'r43 loses unexpected strings: %s' % [s[:64] for s in bad_loss[:5]]
    assert R43_MARKER in gained, 'the R43 marker is missing from the masked delta'
    assert R42_MARKER in lost, 'the R42 marker did not show up as a masked loss (it must be retired from rodata)'
    assert not in_masks(new.find(R43_MARKER), mask_n), \
        'the R43 marker sits inside a masked span -- smuggled?'
    say('strings law: masked delta: gained %d (the R43 marker + the redwood-pager formats + %d Darwin stamp lines),'
        ' lost %d (the R42 marker + Darwin stamps); the marker at %#x rides real rodata'
        % (len(gained), sum(1 for s in gained if DARWIN.search(s)), len(lost), new.find(R43_MARKER)))

    # 6. symtab census
    assert sym_n['nsyms'] >= sym_o['nsyms'], \
        'r43 carries FEWER symbols than r42 (%d < %d)' % (sym_n['nsyms'], sym_o['nsyms'])
    say('symtab law:  nsyms %d -> %d (strtab %#x -> %#x)'
        % (sym_o['nsyms'], sym_n['nsyms'], sym_o['strsize'], sym_n['strsize']))

    say('GATE GREEN: the r43 kernel IS the r42 kernel plus the try-lock probe'
        ' (bytes differ, __TEXT +%d B with the payload law held, the marker swapped same-length).' % shift)
    if args.report:
        with open(args.report, 'w') as f:
            f.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
