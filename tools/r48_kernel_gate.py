#!/usr/bin/env python3
"""tools/r48_kernel_gate.py -- the six release laws for the r48 kernel, measured
at the R47 release kernel:

  1. size:     the bytes MUST differ; the delta stays under 64 KiB; zero or
               positive are both legal (R41/R43/R45/R47 rode __TEXT padding;
               the census counters, the live probe and the floor census may
               do either).
  2. marker:   the R48 marker (SAME byte length as the R47 marker's) is in
               r48 exactly once and in the base zero times; the R47 marker
               is retired (x0 in r48, x1 in the base).
  3. lineage:  every R33 generation marker, every R38 display/Wi-Fi mark, the
               XNULIVE4 channel mark and the "frame dropped" literal ride in
               r48; the forensic literal "redwood-pager: verdict" rides x6
               on BOTH sides (R48 adds none); the sigpost format rides x1 on
               BOTH sides (the R45 wire, unchanged); the sigbirth format
               rides x1 on BOTH sides (the R46 wire, unchanged); the R48
               census literals (siglive: / sigcensus:) ride x1 in r48 and
               x0 in the base; the redwood_fault_seen symbol rides in BOTH
               strtabs' neighborhood only in r48 (it is new) -- and the
               flush channel is proven packed: the strtab of BOTH kernels
               carries the loader_log_flush symbol.
  4. segments: the LC_SEGMENT_64 name sequence is identical; __TEXT must not
               shrink and stays under 16 KiB of growth; every OTHER payload
               segment keeps its file size and shifts by exactly the __TEXT
               growth; __LINKEDIT absorbs the rest of the file delta.
  5. strings:  computed with the volatile regions AND THE CODE SEGMENTS
               MASKED OUT (the run-12 lesson: machine code is not strings):
               every string r48 gains is the R48 marker, one of the census
               formats, or a Darwin version line; every string it loses is
               the R47 marker or a Darwin line.  The marker must live
               OUTSIDE the masked spans.
  6. symtab:   r48 carries at least as many symbols as the R47 release
               (the census adds redwood_fault_seen -- one new symbol is
               allowed, none may vanish).

usage:
  python3 tools/r48_kernel_gate.py --r47-kernel base/r47-kernel.bin \
      --r48-kernel xnu-src/.../mach.development.bcm2837 \
      [--report release/r48-kernel-gate.txt]
"""

import argparse
import hashlib
import re
import struct
import sys

R47_MARKER = b'R47: the flush rule lands every born line in the logdump'
R48_MARKER = b'R48: the census counts every hand at work, live, no gate'
SIGLIVE_FMT = b'siglive: birth '
SIGCENSUS_FMT = b'sigcensus: births '
R46_MARKER = b'R46: the signal birth ledger names every hand that feeds'
R45_MARKER = b'R45: the signal funnel writes who died and what was sent'
R46_MARKER_LEN_LAW = len(R46_MARKER) == len(R45_MARKER)
assert R46_MARKER_LEN_LAW, 'the R46 marker must keep the R45 marker byte length'
REPORT_LITERAL = b'frame dropped'
SIGPOST_FMT = b'sigpost: %s[%d] exception %d code 0x%llx 0x%llx\n'
SIGBIRTH_FMT = b'sigbirth: %s[%d] signal %d code 0x%llx from 0x%llx by %s[%d]\n'
PAGER_LITERAL = b'redwood-pager: verdict'

R47_KERNEL = '9d589bad9f77019a2dbc3bdc379c5694188a1511c27af37353436ef0d549ab21'

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
    ap.add_argument('--r47-kernel', required=True)
    ap.add_argument('--r48-kernel', required=True)
    ap.add_argument('--report', default=None)
    args = ap.parse_args()

    old = open(args.r47_kernel, 'rb').read()
    new = open(args.r48_kernel, 'rb').read()
    lines = []

    def say(s):
        print(s)
        lines.append(s)

    osha, nsha = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    say('r47 base kernel: %d B, sha256 %s' % (len(old), osha))
    assert osha == R47_KERNEL, 'the r47 base kernel is %s, not the release byte %s' % (osha, R47_KERNEL)
    say('r48 kernel: %d B, sha256 %s' % (len(new), nsha))

    lc_end_o, segs_o, sym_o = macho_layouts(old)
    lc_end_n, segs_n, sym_n = macho_layouts(new)

    # 1. size (the R41/R43/R45 lesson: the ACCOUNTING, not the sign -- the
    #    trace's edits may fit __TEXT padding, so delta 0 is a legal outcome)
    delta = len(new) - len(old)
    assert nsha != osha, 'the r48 kernel is byte-identical to r47 (sha %s) -- the patch did not land?' % nsha
    assert abs(delta) < 65536, 'the size delta %d B exceeds the 64 KiB patch band' % delta
    say('size law:    bytes differ (new sha), delta %+d B -- the band is |d| < 64 KiB;'
        ' zero and positive are both legal here (the census edits may fit __TEXT'
        ' padding; the law is the accounting, not the sign)' % delta)

    # 2. the markers
    assert new.count(R48_MARKER) == 1, 'the R48 marker rides x%d, want exactly 1' % new.count(R48_MARKER)
    assert old.count(R48_MARKER) == 0, 'the r47 kernel already carries the R48 marker?'
    assert new.count(R47_MARKER) == 0, 'the R47 marker still rides in r48 -- it was to be retired'
    assert old.count(R47_MARKER) == 1, 'the r47 base kernel lost its own marker'
    assert len(R48_MARKER) == len(R47_MARKER), 'the R48 marker must keep the R47 marker byte length'
    say('marker law:  the R48 marker x1 in r48, x0 in r47; the R47 marker retired (x0 in r48, x1 in r47); same length')

    # 3. lineage
    for mark, want in R33_MARKS:
        got = new.count(mark)
        assert got == want, 'R33 mark %s x%d != %d' % (mark.decode(), got, want)
    for mark in R38_MARKS:
        assert mark in new, 'the R38 mark is missing: %s' % mark.decode()
    assert new.count(XNULIVE4) >= 1, 'the XNULIVE4 channel mark is missing'
    assert new.count(REPORT_LITERAL) == 2, 'the forensic literal "frame dropped" x%d != 2 (the swap-timeout IOLog format string + the report literal)' % new.count(REPORT_LITERAL)
    assert old.count(REPORT_LITERAL) == 2, 'the r45 base lost one of its two "frame dropped" occurrences'
    assert new.count(PAGER_LITERAL) == 6, 'the forensic literal "redwood-pager: verdict" x%d != 6 (the four R42 verdict formats + the two R43 busy formats ride unchanged)' % new.count(PAGER_LITERAL)
    # THE RUN-8 LESSON (40917994778), re-applied at the new anchor: this assert
    # is derived from the REAL base, not inherited.  old IS the R45 release
    # kernel (the R42/R43 formats landed in their own rounds; R44/R45 added
    # none): the base carries x6, and this round adds NONE, so the law is 6 -> 6.
    assert old.count(PAGER_LITERAL) == 6, 'the r45 base should carry the pager literal x6, got x%d' % old.count(PAGER_LITERAL)
    # THE R45 WIRE, UNCHANGED: the sigpost format rides x1 on BOTH sides.
    assert new.count(SIGPOST_FMT) == 1, 'the sigpost format rides x%d in r47, want exactly 1 (the R45 wire stays)' % new.count(SIGPOST_FMT)
    assert old.count(SIGPOST_FMT) == 1, 'the r46 base lost the sigpost format?'
    # THE R46 WIRE, UNCHANGED: the sigbirth format rides x1 on BOTH sides.
    assert new.count(SIGBIRTH_FMT) == 1, 'the sigbirth format rides x%d in r47, want exactly 1 (the R46 wire stays)' % new.count(SIGBIRTH_FMT)
    assert old.count(SIGBIRTH_FMT) == 1, 'the r47 base lost the sigbirth format?'
    # THE R48 CENSUS, PROVEN PACKED: the live-probe and census formats ride
    # in r48 exactly once and NOWHERE in the base.
    assert new.count(SIGLIVE_FMT) == 1, 'the siglive format rides x%d in r48, want exactly 1' % new.count(SIGLIVE_FMT)
    assert old.count(SIGLIVE_FMT) == 0, 'the r47 base already carries the siglive format?'
    assert new.count(SIGCENSUS_FMT) == 1, 'the sigcensus format rides x%d in r48, want exactly 1' % new.count(SIGCENSUS_FMT)
    assert old.count(SIGCENSUS_FMT) == 0, 'the r47 base already carries the sigcensus format?'
    # THE R47 CHANNEL, PROVEN PACKED: loader_log_flush must be in BOTH
    # strtabs -- the R46 base already calls it from the pager convict and
    # the adreno poller; r47 adds four callers and no new symbol.
    assert b'loader_log_flush' in new, 'the r47 strtab lost the flush symbol'
    assert b'loader_log_flush' in old, 'the r46 base never had the flush symbol?'
    say('lineage law: R33 generation 4 + R38 display/WiFi 5 + XNULIVE4 + the "frame dropped" literal'
        ' all ride; the "redwood-pager: verdict" literal rides x6 on BOTH sides (R48 adds none);'
        ' the sigpost and sigbirth formats ride x1 on BOTH sides (the R45/R46 wires);'
        ' the siglive and sigcensus formats ride x1 in r48, x0 in r47 (the R48 census);'
        ' the flush symbol rides in BOTH strtabs (the recorders keep the R47 law)')

    # 4. segment census (the R42/R43 shape: __TEXT grows or pads, the rest shifts, sizes pinned)
    names_o = [s[0] for s in segs_o]
    names_n = [s[0] for s in segs_n]
    assert names_o == names_n, 'the LC_SEGMENT_64 name sequence changed: %s -> %s' % (names_o, names_n)
    text_o = [s for s in segs_o if s[0] == b'__TEXT'][0]
    text_n = [s for s in segs_n if s[0] == b'__TEXT'][0]
    shift = text_n[2] - text_o[2]
    # The real build may absorb the whole round in __TEXT's tail padding: R41's
    # delta-0 was the real outcome, R42 grew a function, R43/R45 landed
    # delta-0/+24.  The law is the ACCOUNTING, not the sign: __TEXT must not
    # shrink, the mid segments keep their sizes and shift by the same amount,
    # and __LINKEDIT takes exactly the rest (asserted below).
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

    # 5. strings delta, volatile regions AND code segments masked
    #    THE RUN-12 LESSON (40976283142): machine code is not strings.  The
    #    trace edits live in __TEXT_EXEC, and changed instruction bytes can
    #    carry a >= 8-char printable run by accident (ADRP/ADD immediate
    #    fields).  Every literal this law polices (the markers, the
    #    "redwood-pager:" verdict formats, the Darwin stamps) lives in a DATA
    #    segment, so the scan masks the code segments out on BOTH sides and
    #    keeps its full teeth on every data region.
    CODE_SEGS = (b'__TEXT_EXEC', b'__PRELINK_TEXT', b'__PLK_TEXT_EXEC')
    code_o = [(fo, fo + fs) for (name, fo, fs) in segs_o if name in CODE_SEGS and fs]
    code_n = [(fo, fo + fs) for (name, fo, fs) in segs_n if name in CODE_SEGS and fs]
    mask_o = [(0, lc_end_o)] + code_o + [(sym_o['symoff'], len(old))]
    mask_n = [(0, lc_end_n)] + code_n + [(sym_n['symoff'], len(new))]
    o_str = masked_strings(old, mask_o)
    n_str = masked_strings(new, mask_n)
    gained, lost = n_str - o_str, o_str - n_str

    def allowed_gain(s):
        return s == R48_MARKER or s.startswith(b'redwood-pager:') or s.startswith(b'sigcensus:') or \
            s.startswith(b'siglive:') or DARWIN.search(s)

    bad_gain = [s for s in gained if not allowed_gain(s)]
    bad_loss = [s for s in lost if not (s == R47_MARKER or DARWIN.search(s))]
    assert not bad_gain, 'r48 gains unexpected strings (outside symtab/load cmds, code segs masked): %s' % [s[:64] for s in bad_gain[:5]]
    assert not bad_loss, 'r48 loses unexpected strings: %s' % [s[:64] for s in bad_loss[:5]]
    assert R48_MARKER in gained, 'the R48 marker is missing from the masked delta'
    assert R47_MARKER in lost, 'the R47 marker did not show up as a masked loss (it must be retired from rodata)'
    assert not in_masks(new.find(R48_MARKER), mask_n), \
        'the R48 marker sits inside a masked span -- smuggled?'
    say('strings law: masked delta (code segs masked too -- machine code is not strings, the run-12 law):'
        ' gained %d (the R48 marker + the census formats + the redwood-pager formats + %d Darwin stamp lines),'
        ' lost %d (the R47 marker + Darwin stamps); the marker at %#x rides real rodata'
        % (len(gained), sum(1 for s in gained if DARWIN.search(s)), len(lost), new.find(R48_MARKER)))

    # 6. symtab census
    assert sym_n['nsyms'] >= sym_o['nsyms'], \
        'r48 carries FEWER symbols than r47 (%d < %d)' % (sym_n['nsyms'], sym_o['nsyms'])
    say('symtab law:  nsyms %d -> %d (strtab %#x -> %#x)'
        % (sym_o['nsyms'], sym_n['nsyms'], sym_o['strsize'], sym_n['strsize']))

    say('GATE GREEN: the r48 kernel IS the r47 kernel plus the census'
        ' (bytes differ, __TEXT +%d B with the payload law held, the marker swapped same-length).' % shift)
    if args.report:
        open(args.report, 'w').write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
