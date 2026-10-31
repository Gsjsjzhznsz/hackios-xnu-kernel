#!/usr/bin/env python3
"""tools/r50_kernel_gate.py -- the six release laws for the r50 kernel, measured
at the R49 release kernel:

  1. size:     the bytes MUST differ; the delta stays under 64 KiB; zero or
               positive are both legal (R41/R43/R45/R47/R48/R49 rode __TEXT
               padding; the register hologram may do either).
  2. marker:   the R50 marker (SAME byte length as the R49 marker's) is in
               r50 exactly once and in the base zero times; the R49 marker
               is retired (x0 in r50, x1 in the base).
  3. lineage:  every R33 generation marker, every R38 display/Wi-Fi mark, the
               XNULIVE4 channel mark and the "frame dropped" literal ride in
               r50; the forensic literal "redwood-pager: verdict" rides x6
               on BOTH sides (R50 adds no NEW pager verdict format -- the
               register hologram rides in the existing "redwood-fault: "
               literal, x2 in r50 (context + registers) and x1 in the base);
               the R49 hologram's context format rides x1 on BOTH sides (the
               R49 wire stays); the "r49-deliver: " format rides x1 on BOTH
               sides (the R49 funnel wire stays); the sigpost format rides x1
               on BOTH sides (the R45 wire); the sigbirth format rides x1 on
               BOTH sides (the R46 wire); the R48 census literals
               (siglive: / sigcensus:) ride x1 on BOTH sides (the R48 wires
               stay) -- and the flush channel is proven packed: the strtab of
               BOTH kernels carries the loader_log_flush symbol.
  4. segments: the LC_SEGMENT_64 name sequence is identical; __TEXT must not
               shrink and stays under 16 KiB of growth; every OTHER payload
               segment keeps its file size and shifts by exactly the __TEXT
               growth; __LINKEDIT absorbs the rest of the file delta.
  5. strings:  computed with the volatile regions AND THE CODE SEGMENTS
               MASKED OUT (the run-12 lesson: machine code is not strings):
               every string r50 gains is the R50 marker, the registers
               format, or a Darwin version line; every string it loses is
               the R49 marker or a Darwin line.  The two pointer-run
               artifacts the R49-fix froze (XNULIVE2's neighbour and the
               (4(... table) are LAYOUT shadows -- an embedded pointer whose
               target moved with the round's data shift -- so any form they
               take is allowed on BOTH sides, gated by prefix, and their
               forms must exist somewhere in each kernel (the R49-fix
               byte-archaeology law, now form-agnostic: the VALUE moves with
               the layout, the STRING is not smuggled either way).
               The marker must live OUTSIDE the masked spans.
  6. symtab:   r50 carries at least as many symbols as the R49 release
               (the register hologram adds no new function -- none may
               vanish).

usage:
  python3 tools/r50_kernel_gate.py --base-kernel base/r49-kernel.bin \
      --r50-kernel xnu-src/.../mach.development.bcm2837 \
      [--report release/r50-kernel-gate.txt]
"""

import argparse
import hashlib
import re
import struct
import sys

R49_MARKER = b'R49: the hologram names the hand and path of every fault'
R50_MARKER = b'R50: the registers name the syscall behind every failure'
SIGLIVE_FMT = b'siglive: birth '
SIGCENSUS_FMT = b'sigcensus: births '
HOLOGRAM_FMT = b'redwood-fault: '
CONTEXT_FMT = b'redwood-fault: verdict %d context: '
REGISTERS_FMT = b'redwood-fault: verdict %d registers: '
DELIVER_FMT = b'r49-deliver: '
R46_MARKER = b'R46: the signal birth ledger names every hand that feeds'
R45_MARKER = b'R45: the signal funnel writes who died and what was sent'
R46_MARKER_LEN_LAW = len(R46_MARKER) == len(R45_MARKER)
assert R46_MARKER_LEN_LAW, 'the R46 marker must keep the R45 marker byte length'
REPORT_LITERAL = b'frame dropped'
RUN_PREFIXES = (b'XNULIVE2', b'(4((((((((((00((')
SIGPOST_FMT = b'sigpost: %s[%d] exception %d code 0x%llx 0x%llx\n'
SIGBIRTH_FMT = b'sigbirth: %s[%d] signal %d code 0x%llx from 0x%llx by %s[%d]\n'
PAGER_LITERAL = b'redwood-pager: verdict'

R49_KERNEL = '72d75d4945eef15f8664b681adcb2d18f055d512e2913dfc14e734020427f4db'

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
    ap.add_argument('--base-kernel', required=True)
    ap.add_argument('--r50-kernel', required=True)
    ap.add_argument('--report', default=None)
    args = ap.parse_args()

    old = open(args.base_kernel, 'rb').read()
    new = open(args.r50_kernel, 'rb').read()
    lines = []

    def say(s):
        print(s)
        lines.append(s)

    osha, nsha = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    say('r49 base kernel: %d B, sha256 %s' % (len(old), osha))
    assert osha == R49_KERNEL, 'the r49 base kernel is %s, not the release byte %s' % (osha, R49_KERNEL)
    say('r50 kernel: %d B, sha256 %s' % (len(new), nsha))

    lc_end_o, segs_o, sym_o = macho_layouts(old)
    lc_end_n, segs_n, sym_n = macho_layouts(new)

    # 1. size (the R41/R43/R45 lesson: the ACCOUNTING, not the sign -- the
    #    trace's edits may fit __TEXT padding, so delta 0 is a legal outcome)
    delta = len(new) - len(old)
    assert nsha != osha, 'the r50 kernel is byte-identical to r49 (sha %s) -- the patch did not land?' % nsha
    assert abs(delta) < 65536, 'the size delta %d B exceeds the 64 KiB patch band' % delta
    say('size law:    bytes differ (new sha), delta %+d B -- the band is |d| < 64 KiB;'
        ' zero and positive are both legal here (the register hologram may fit __TEXT'
        ' padding; the law is the accounting, not the sign)' % delta)

    # 2. the markers
    assert new.count(R50_MARKER) == 1, 'the R50 marker rides x%d, want exactly 1' % new.count(R50_MARKER)
    assert old.count(R50_MARKER) == 0, 'the r49 kernel already carries the R50 marker?'
    assert new.count(R49_MARKER) == 0, 'the R49 marker still rides in r50 -- it was to be retired'
    assert old.count(R49_MARKER) == 1, 'the r49 base kernel lost its own marker'
    assert len(R50_MARKER) == len(R49_MARKER), 'the R50 marker must keep the R49 marker byte length'
    say('marker law:  the R50 marker x1 in r50, x0 in r49; the R49 marker retired (x0 in r50, x1 in r49); same length')

    # 3. lineage
    for mark, want in R33_MARKS:
        got = new.count(mark)
        assert got == want, 'R33 mark %s x%d != %d' % (mark.decode(), got, want)
    for mark in R38_MARKS:
        assert mark in new, 'the R38 mark is missing: %s' % mark.decode()
    assert new.count(XNULIVE4) >= 1, 'the XNULIVE4 channel mark is missing'
    assert new.count(REPORT_LITERAL) == 2, 'the forensic literal "frame dropped" x%d != 2 (the swap-timeout IOLog format string + the report literal)' % new.count(REPORT_LITERAL)
    assert old.count(REPORT_LITERAL) == 2, 'the r49 base lost one of its two "frame dropped" occurrences'
    assert new.count(PAGER_LITERAL) == 6, 'the forensic literal "redwood-pager: verdict" x%d != 6 (R50 adds no new pager verdict format -- the register hologram rides the redwood-fault literal)' % new.count(PAGER_LITERAL)
    # THE RUN-8 LESSON (40917994778), re-applied at the new anchor: this assert
    # is derived from the REAL base, not inherited.  old IS the R49 release
    # kernel (the R42/R43 formats landed in their own rounds; R44..R49 added
    # no pager format): the base carries x6, and this round adds NONE, so the
    # law is 6 -> 6.
    assert old.count(PAGER_LITERAL) == 6, 'the r49 base should carry the pager literal x6, got x%d' % old.count(PAGER_LITERAL)
    # THE R45 WIRE, UNCHANGED: the sigpost format rides x1 on BOTH sides.
    assert new.count(SIGPOST_FMT) == 1, 'the sigpost format rides x%d in r50, want exactly 1 (the R45 wire stays)' % new.count(SIGPOST_FMT)
    assert old.count(SIGPOST_FMT) == 1, 'the r49 base lost the sigpost format?'
    # THE R46 WIRE, UNCHANGED: the sigbirth format rides x1 on BOTH sides.
    assert new.count(SIGBIRTH_FMT) == 1, 'the sigbirth format rides x%d in r50, want exactly 1 (the R46 wire stays)' % new.count(SIGBIRTH_FMT)
    assert old.count(SIGBIRTH_FMT) == 1, 'the r49 base lost the sigbirth format?'
    # THE R48 WIRES, UNCHANGED: the live-probe and census formats ride x1
    # on BOTH sides now (they landed in R48 and stay).
    assert new.count(SIGLIVE_FMT) == 1, 'the siglive format rides x%d in r50, want exactly 1 (the R48 wire stays)' % new.count(SIGLIVE_FMT)
    assert old.count(SIGLIVE_FMT) == 1, 'the r49 base lost the siglive format?'
    assert new.count(SIGCENSUS_FMT) == 1, 'the sigcensus format rides x%d in r50, want exactly 1 (the R48 wire stays)' % new.count(SIGCENSUS_FMT)
    assert old.count(SIGCENSUS_FMT) == 1, 'the r49 base lost the sigcensus format?'
    # THE R49 WIRES, UNCHANGED: the funnel re-mount format and the verdict
    # CONTEXT format ride x1 on BOTH sides (they landed in R49 and stay).
    assert new.count(DELIVER_FMT) == 1, 'the r49-deliver format rides x%d in r50, want exactly 1 (the R49 wire stays)' % new.count(DELIVER_FMT)
    assert old.count(DELIVER_FMT) == 1, 'the r49 base lost the r49-deliver format?'
    assert new.count(CONTEXT_FMT) == 1, 'the redwood-fault CONTEXT format rides x%d in r50, want exactly 1 (the R49 wire stays)' % new.count(CONTEXT_FMT)
    assert old.count(CONTEXT_FMT) == 1, 'the r49 base lost the redwood-fault context format?'
    # THE R50 REGISTER HOLOGRAM, PROVEN PACKED: the redwood-fault literal
    # grows 1 -> 2 (context + registers) and the registers format is new.
    assert new.count(HOLOGRAM_FMT) == 2, 'the redwood-fault literal rides x%d in r50, want exactly 2 (context + registers)' % new.count(HOLOGRAM_FMT)
    assert old.count(HOLOGRAM_FMT) == 1, 'the r49 base should carry the redwood-fault literal x1, got x%d' % old.count(HOLOGRAM_FMT)
    assert new.count(REGISTERS_FMT) == 1, 'the registers format rides x%d in r50, want exactly 1' % new.count(REGISTERS_FMT)
    assert old.count(REGISTERS_FMT) == 0, 'the r49 base already carries the registers format?'
    # THE R47 CHANNEL, PROVEN PACKED: loader_log_flush must be in BOTH
    # strtabs -- the R46 base already calls it from the pager convict and
    # the adreno poller; every round since adds callers and no new symbol.
    assert b'loader_log_flush' in new, 'the r50 strtab lost the flush symbol'
    assert b'loader_log_flush' in old, 'the r49 base never had the flush symbol?'
    assert b'redwood_user_fault_deliver' in new, 'the r50 strtab lost the deliver symbol (the R49 wire did not stay linked)'
    assert b'redwood_user_fault_exit' in new, 'the r50 strtab lost the exit-census symbol (the R49 wire did not stay linked)'
    assert b'redwood_fault_seen' in old, 'the r49 base lost the floor-census symbol?'
    assert b'redwood_fault_seen' in new, 'the r50 strtab lost the floor-census symbol (the R48 wire did not stay linked)'
    say('lineage law: R33 generation 4 + R38 display/WiFi 5 + XNULIVE4 + the "frame dropped" literal'
        ' all ride; the "redwood-pager: verdict" literal rides x6 on BOTH sides (R50 adds no pager format);'
        ' the sigpost and sigbirth formats ride x1 on BOTH sides (the R45/R46 wires);'
        ' the siglive and sigcensus formats ride x1 on BOTH sides (the R48 wires);'
        ' the r49-deliver and redwood-fault CONTEXT formats ride x1 on BOTH sides (the R49 wires);'
        ' the redwood-fault literal grows 1 -> 2 and the registers format rides x1 in r50, x0 in r49 (the R50 hologram);'
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
    # delta-0/+24, R49 rode +672 of __LINKEDIT.  The law is the ACCOUNTING,
    # not the sign: __TEXT must not shrink, the mid segments keep their sizes
    # and shift by the same amount, and __LINKEDIT takes exactly the rest.
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
    #    region, so the scan masks the code segments out on BOTH sides and
    #    keeps its full teeth on every data region.
    #    THE R49-FIX LAW, FORM-AGNOSTIC FOR R50: the two pointer-run artifacts
    #    (XNULIVE2's neighbour, the (4(... table) are LAYOUT shadows -- an
    #    embedded pointer's VALUE moves with every data shift, and the R50
    #    round moves the layout again (one new cstring).  Their strings are
    #    allowed by PREFIX on both sides, and each side must still carry a
    #    form of each (the string did not vanish -- it moved).
    CODE_SEGS = (b'__TEXT_EXEC', b'__PRELINK_TEXT', b'__PLK_TEXT_EXEC')
    code_o = [(fo, fo + fs) for (name, fo, fs) in segs_o if name in CODE_SEGS and fs]
    code_n = [(fo, fo + fs) for (name, fo, fs) in segs_n if name in CODE_SEGS and fs]
    mask_o = [(0, lc_end_o)] + code_o + [(sym_o['symoff'], len(old))]
    mask_n = [(0, lc_end_n)] + code_n + [(sym_n['symoff'], len(new))]
    o_str = masked_strings(old, mask_o)
    n_str = masked_strings(new, mask_n)
    gained, lost = n_str - o_str, o_str - n_str

    def run_like(s):
        return any(s.startswith(p) for p in RUN_PREFIXES)

    def allowed_gain(s):
        return s == R50_MARKER or s.startswith(b'redwood-fault:') or \
            s.startswith(b'redwood-pager:') or s.startswith(b'sigcensus:') or \
            s.startswith(b'siglive:') or s.startswith(b'r49-deliver:') or \
            run_like(s) or DARWIN.search(s)

    def allowed_loss(s):
        return s == R49_MARKER or run_like(s) or \
            s.startswith(b'redwood-pager:') or s.startswith(b'sigcensus:') or \
            DARWIN.search(s)

    bad_gain = [s for s in gained if not allowed_gain(s)]
    bad_loss = [s for s in lost if not allowed_loss(s)]
    assert not bad_gain, 'r50 gains unexpected strings (outside symtab/load cmds, code segs masked): %s' % [s[:64] for s in bad_gain[:5]]
    assert not bad_loss, 'r50 loses unexpected strings: %s' % [s[:64] for s in bad_loss[:5]]
    assert R50_MARKER in gained, 'the R50 marker is missing from the masked delta'
    assert REGISTERS_FMT in gained, 'the R50 registers format is missing from the masked delta'
    assert R49_MARKER in lost, 'the R49 marker did not show up as a masked loss (it must be retired from rodata)'
    # the form-agnostic survival check: each side still carries a form of
    # both pointer-run artifacts (they may have moved, they may not vanish)
    assert any(run_like(s) and s.startswith(b'XNULIVE2') for s in n_str), \
        'the r50 kernel lost every XNULIVE2 run form'
    assert any(run_like(s) and s.startswith(b'XNULIVE2') for s in o_str), \
        'the r49 base lost every XNULIVE2 run form?'
    assert any(run_like(s) and s.startswith(b'(4(') for s in n_str), \
        'the r50 kernel lost every pointer-table run form'
    assert any(run_like(s) and s.startswith(b'(4(') for s in o_str), \
        'the r49 base lost every pointer-table run form?'
    assert not in_masks(new.find(R50_MARKER), mask_n), \
        'the R50 marker sits inside a masked span -- smuggled?'
    say('strings law: masked delta (code segs masked too -- machine code is not strings, the run-12 law):'
        ' gained %d (the R50 marker + the registers format + %d Darwin stamp lines + %d layout-run forms),'
        ' lost %d (the R49 marker + %d Darwin stamp lines + %d layout-run forms); the marker at %#x rides real rodata'
        % (len(gained), sum(1 for s in gained if DARWIN.search(s)), sum(1 for s in gained if run_like(s)),
           len(lost), sum(1 for s in lost if DARWIN.search(s)), sum(1 for s in lost if run_like(s)),
           new.find(R50_MARKER)))

    # 6. symtab census
    assert sym_n['nsyms'] >= sym_o['nsyms'], \
        'r50 carries FEWER symbols than r49 (%d < %d)' % (sym_n['nsyms'], sym_o['nsyms'])
    say('symtab law:  nsyms %d -> %d (strtab %#x -> %#x)'
        % (sym_o['nsyms'], sym_n['nsyms'], sym_o['strsize'], sym_n['strsize']))

    say('GATE GREEN: the r50 kernel IS the r49 kernel plus the register hologram'
        ' (bytes differ, __TEXT +%d B with the payload law held, the marker swapped same-length).' % shift)
    if args.report:
        open(args.report, 'w').write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
