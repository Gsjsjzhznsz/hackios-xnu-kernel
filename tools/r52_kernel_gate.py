#!/usr/bin/env python3
"""tools/r52_kernel_gate.py -- the six release laws for the r52 kernel, measured
at the R51 release kernel:

  1. size:     the bytes MUST differ; the delta stays under 64 KiB; zero or
               positive are both legal (R41/R43/R45/R47/R48/R49/R50/R51 rode
               __TEXT padding and __LINKEDIT; the archaeology may do either).
  2. marker:   the R52 marker (SAME byte length as the R51 marker's) is in
               r52 exactly once and in the base zero times; the R51 marker
               is retired (x0 in r52, x1 in the base).
  3. lineage:  every R33 generation marker, every R38 display/Wi-Fi mark, the
               XNULIVE4 channel mark and the "frame dropped" literal ride in
               r52; the forensic literal "redwood-pager: verdict" rides x6
               on BOTH sides (R52 adds no NEW pager verdict format); the
               R49 hologram's context format rides x1 on BOTH sides (the
               R49 wire stays); the "r49-deliver: " format rides x1 on BOTH
               sides (the R49 funnel wire stays); the sigpost format rides
               x1 on BOTH sides (the R45 wire); the sigbirth format rides x1
               on BOTH sides (the R46 wire); the R48 census literals
               (siglive: / sigcensus: births) ride x1 on BOTH sides (the R48
               wires stay) and the sigcensus uf-ok tail rides x1 on BOTH
               sides (the R51 split stays); the R50 register hologram stays:
               the redwood-fault literal rides x2 on BOTH sides and the
               registers format x1 on BOTH sides; the R51 prefault hologram
               stays: its format rides x1 on BOTH sides and the clamp counter
               symbol links on BOTH sides; the R52 archaeology is NEW: the
               infos-hex head format, the hex line format, the two channel
               fingerprint formats and the r52_infos_hex_sets symbol ride
               ONLY in r52 -- and the flush channel is proven packed: the
               strtab of BOTH kernels carries the loader_log_flush symbol.
  4. segments: the LC_SEGMENT_64 name sequence is identical; __TEXT must not
               shrink and stays under 32 KiB of growth (the R52 lesson: __TEXT
               growth is PAGE-QUANTIZED -- the archaeology rode a full 16 KiB
               page jump, the band is an audit window not a size budget; two
               quanta); every OTHER payload
               segment keeps its file size and shifts by exactly the __TEXT
               growth; __LINKEDIT absorbs the rest of the file delta.
  5. strings:  computed with the volatile regions AND THE CODE SEGMENTS
               MASKED OUT (the run-12 lesson: machine code is not strings):
               every string r52 gains is the R52 marker, an archaeology
               format, a channel fingerprint, or a Darwin version line;
               every string it loses is the R51 marker (the only retired
               cstring this round) or a layout-run form.  The two pointer-run
               artifacts the R49-fix froze (XNULIVE2's neighbour and the
               (4(... table) are LAYOUT shadows -- any form is allowed on
               BOTH sides, gated by prefix, and their forms must exist
               somewhere in each kernel (the byte-archaeology law,
               form-agnostic).
               The marker must live OUTSIDE the masked spans.
  6. symtab:   r52 carries at least as many symbols as the R51 release
               (the archaeology counter is one new data symbol -- none may
               vanish).

usage:
  python3 tools/r52_kernel_gate.py --base-kernel base/r51-kernel.bin \
      --r52-kernel xnu-src/.../mach.development.bcm2837 \
      [--report release/r52-kernel-gate.txt]
"""

import argparse
import hashlib
import re
import struct
import sys

R51_MARKER = b'R51: the prefault names the math behind every exec fault'
R52_MARKER = b'R52: the archaeology reads the bytes that dyld delivered'
SIGLIVE_FMT = b'siglive: birth '
SIGCENSUS_FMT = b'sigcensus: births '
SIGCENSUS2_FMT = b'sigcensus: uf-ok '
HOLOGRAM_FMT = b'redwood-fault: '
CONTEXT_FMT = b'redwood-fault: verdict %d context: '
REGISTERS_FMT = b'redwood-fault: verdict %d registers: '
DELIVER_FMT = b'r49-deliver: '
EXEC_FMT = b'redwood-exec: prefault '
INFOS_HEX_FMT = b'redwood-exec: infos-hex '
INFOS_LN_FMT = b'redwood-exec: infos['
CHAN_FMT = b'sigcensus: chan='
R52_SYMBOL = b'r52_infos_hex_sets'
R46_MARKER = b'R46: the signal birth ledger names every hand that feeds'
R45_MARKER = b'R45: the signal funnel writes who died and what was sent'
R46_MARKER_LEN_LAW = len(R46_MARKER) == len(R45_MARKER)
assert R46_MARKER_LEN_LAW, 'the R46 marker must keep the R45 marker byte length'
REPORT_LITERAL = b'frame dropped'
RUN_PREFIXES = (b'XNULIVE2', b'(4((((((((((00((')
SIGPOST_FMT = b'sigpost: %s[%d] exception %d code 0x%llx 0x%llx\n'
SIGBIRTH_FMT = b'sigbirth: %s[%d] signal %d code 0x%llx from 0x%llx by %s[%d]\n'
PAGER_LITERAL = b'redwood-pager: verdict'
CLAMP_SYMBOL = b'r51_madvise_readonly_pages'

R51_KERNEL = '1b862e1e12ae84319558dc6be7b77119718c7840a9b81fd3727096cf7fdea589'

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
    ap.add_argument('--r52-kernel', required=True)
    ap.add_argument('--report', default=None)
    args = ap.parse_args()

    old = open(args.base_kernel, 'rb').read()
    new = open(args.r52_kernel, 'rb').read()
    lines = []

    def say(s):
        print(s)
        lines.append(s)

    osha, nsha = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    say('r51 base kernel: %d B, sha256 %s' % (len(old), osha))
    assert osha == R51_KERNEL, 'the r51 base kernel is %s, not the release byte %s' % (osha, R51_KERNEL)
    say('r52 kernel: %d B, sha256 %s' % (len(new), nsha))

    lc_end_o, segs_o, sym_o = macho_layouts(old)
    lc_end_n, segs_n, sym_n = macho_layouts(new)

    # 1. size (the R41/R43/R45 lesson: the ACCOUNTING, not the sign -- the
    #    round's edits may fit __TEXT padding, so delta 0 is a legal outcome)
    delta = len(new) - len(old)
    assert nsha != osha, 'the r52 kernel is byte-identical to r51 (sha %s) -- the patch did not land?' % nsha
    assert abs(delta) < 65536, 'the size delta %d B exceeds the 64 KiB patch band' % delta
    say('size law:    bytes differ (new sha), delta %+d B -- the band is |d| < 64 KiB;'
        ' zero and positive are both legal here (the archaeology edits ride in place and'
        ' the fingerprints may fit __TEXT padding; the law is the accounting, not the sign)' % delta)

    # 2. the markers
    assert new.count(R52_MARKER) == 1, 'the R52 marker rides x%d, want exactly 1' % new.count(R52_MARKER)
    assert old.count(R52_MARKER) == 0, 'the r51 kernel already carries the R52 marker?'
    assert new.count(R51_MARKER) == 0, 'the R51 marker still rides in r52 -- it was to be retired'
    assert old.count(R51_MARKER) == 1, 'the r51 base kernel lost its own marker'
    assert len(R52_MARKER) == len(R51_MARKER), 'the R52 marker must keep the R51 marker byte length'
    say('marker law:  the R52 marker x1 in r52, x0 in r51; the R51 marker retired (x0 in r52, x1 in r51); same length')

    # 3. lineage
    for mark, want in R33_MARKS:
        got = new.count(mark)
        assert got == want, 'R33 mark %s x%d != %d' % (mark.decode(), got, want)
    for mark in R38_MARKS:
        assert mark in new, 'the R38 mark is missing: %s' % mark.decode()
    assert new.count(XNULIVE4) >= 1, 'the XNULIVE4 channel mark is missing'
    assert new.count(REPORT_LITERAL) == 2, 'the forensic literal "frame dropped" x%d != 2 (the swap-timeout IOLog format string + the report literal)' % new.count(REPORT_LITERAL)
    assert old.count(REPORT_LITERAL) == 2, 'the r51 base lost one of its two "frame dropped" occurrences'
    assert new.count(PAGER_LITERAL) == 6, 'the forensic literal "redwood-pager: verdict" x%d != 6 (R52 adds no new pager verdict format)' % new.count(PAGER_LITERAL)
    assert old.count(PAGER_LITERAL) == 6, 'the r51 base should carry the pager literal x6, got x%d' % old.count(PAGER_LITERAL)
    # THE R45 WIRE, UNCHANGED: the sigpost format rides x1 on BOTH sides.
    assert new.count(SIGPOST_FMT) == 1, 'the sigpost format rides x%d in r52, want exactly 1 (the R45 wire stays)' % new.count(SIGPOST_FMT)
    assert old.count(SIGPOST_FMT) == 1, 'the r51 base lost the sigpost format?'
    # THE R46 WIRE, UNCHANGED: the sigbirth format rides x1 on BOTH sides.
    assert new.count(SIGBIRTH_FMT) == 1, 'the sigbirth format rides x%d in r52, want exactly 1 (the R46 wire stays)' % new.count(SIGBIRTH_FMT)
    assert old.count(SIGBIRTH_FMT) == 1, 'the r51 base lost the sigbirth format?'
    # THE R48 WIRES, UNCHANGED: the live-probe and census-head formats ride
    # x1 on BOTH sides (they landed in R48 and stay).
    assert new.count(SIGLIVE_FMT) == 1, 'the siglive format rides x%d in r52, want exactly 1 (the R48 wire stays)' % new.count(SIGLIVE_FMT)
    assert old.count(SIGLIVE_FMT) == 1, 'the r51 base lost the siglive format?'
    assert new.count(SIGCENSUS_FMT) == 1, 'the sigcensus births format rides x%d in r52, want exactly 1 (the R48 wire stays)' % new.count(SIGCENSUS_FMT)
    assert old.count(SIGCENSUS_FMT) == 1, 'the r51 base lost the sigcensus births format?'
    # THE R49 WIRES, UNCHANGED: the funnel re-mount format and the verdict
    # CONTEXT format ride x1 on BOTH sides (they landed in R49 and stay).
    assert new.count(DELIVER_FMT) == 1, 'the r49-deliver format rides x%d in r52, want exactly 1 (the R49 wire stays)' % new.count(DELIVER_FMT)
    assert old.count(DELIVER_FMT) == 1, 'the r51 base lost the r49-deliver format?'
    assert new.count(CONTEXT_FMT) == 1, 'the redwood-fault CONTEXT format rides x%d in r52, want exactly 1 (the R49 wire stays)' % new.count(CONTEXT_FMT)
    assert old.count(CONTEXT_FMT) == 1, 'the r51 base lost the redwood-fault context format?'
    # THE R50 REGISTER HOLOGRAM, UNCHANGED: the redwood-fault literal rides
    # x2 on BOTH sides and the registers format x1 on BOTH sides (R52 adds
    # no new redwood-fault format).
    assert new.count(HOLOGRAM_FMT) == 2, 'the redwood-fault literal rides x%d in r52, want exactly 2 (context + registers, unchanged)' % new.count(HOLOGRAM_FMT)
    assert old.count(HOLOGRAM_FMT) == 2, 'the r51 base should carry the redwood-fault literal x2, got x%d' % old.count(HOLOGRAM_FMT)
    assert new.count(REGISTERS_FMT) == 1, 'the registers format rides x%d in r52, want exactly 1 (the R50 hologram stays)' % new.count(REGISTERS_FMT)
    assert old.count(REGISTERS_FMT) == 1, 'the r51 base lost the registers format?'
    # THE R51 KNIVES, UNCHANGED: the prefault hologram format rides x1 on
    # BOTH sides now (it landed in R51 and stays), the census tail x1 on
    # BOTH sides (the split), and the clamp counter symbol links on BOTH.
    assert new.count(EXEC_FMT) == 1, 'the R51 prefault hologram format rides x%d in r52, want exactly 1 (the R51 wire stays)' % new.count(EXEC_FMT)
    assert old.count(EXEC_FMT) == 1, 'the r51 base lost its own prefault hologram format?'
    assert new.count(SIGCENSUS2_FMT) == 1, 'the sigcensus uf-ok tail rides x%d in r52, want exactly 1 (the R51 split stays)' % new.count(SIGCENSUS2_FMT)
    assert old.count(SIGCENSUS2_FMT) == 1, 'the r51 base lost its own census tail?'
    assert CLAMP_SYMBOL in new, 'the r52 strtab lost the clamp counter symbol (the R51 hint clamp did not stay linked)'
    assert CLAMP_SYMBOL in old, 'the r51 base carries no clamp counter symbol?'
    # THE R52 KNIVES, PROVEN PACKED: the archaeology formats are new (the
    # head line, the hex line), the channel fingerprints are new (two
    # formats), and the archaeology counter symbol links only in r52.
    assert new.count(INFOS_HEX_FMT) == 1, 'the R52 infos-hex head format rides x%d in r52, want exactly 1' % new.count(INFOS_HEX_FMT)
    assert old.count(INFOS_HEX_FMT) == 0, 'the r51 base already carries the infos-hex head?'
    assert new.count(INFOS_LN_FMT) == 1, 'the R52 hex line format rides x%d in r52, want exactly 1' % new.count(INFOS_LN_FMT)
    assert old.count(INFOS_LN_FMT) == 0, 'the r51 base already carries the hex line format?'
    assert new.count(CHAN_FMT) == 2, 'the channel fingerprint formats ride x%d in r52, want exactly 2 (log + direct)' % new.count(CHAN_FMT)
    assert old.count(CHAN_FMT) == 0, 'the r51 base already carries the channel fingerprints?'
    assert R52_SYMBOL in new, 'the r52 strtab lost the archaeology counter symbol (the knife did not link)'
    assert R52_SYMBOL not in old, 'the r51 base carries the archaeology counter symbol?'
    # THE R47 CHANNEL, PROVEN PACKED: loader_log_flush must be in BOTH
    # strtabs -- the R46 base already calls it from the pager convict and
    # the adreno poller; every round since adds callers and no new symbol.
    assert b'loader_log_flush' in new, 'the r52 strtab lost the flush symbol'
    assert b'loader_log_flush' in old, 'the r51 base never had the flush symbol?'
    assert b'redwood_user_fault_deliver' in new, 'the r52 strtab lost the deliver symbol (the R49 wire did not stay linked)'
    assert b'redwood_user_fault_exit' in new, 'the r52 strtab lost the exit-census symbol (the R49 wire did not stay linked)'
    assert b'redwood_fault_seen' in old, 'the r51 base lost the floor-census symbol?'
    assert b'redwood_fault_seen' in new, 'the r52 strtab lost the floor-census symbol (the R48 wire did not stay linked)'
    say('lineage law: R33 generation 4 + R38 display/WiFi 5 + XNULIVE4 + the "frame dropped" literal'
        ' all ride; the "redwood-pager: verdict" literal rides x6 on BOTH sides (R52 adds no pager format);'
        ' the sigpost and sigbirth formats ride x1 on BOTH sides (the R45/R46 wires);'
        ' the siglive and sigcensus births formats ride x1 on BOTH sides (the R48 wires);'
        ' the r49-deliver and redwood-fault CONTEXT formats ride x1 on BOTH sides (the R49 wires);'
        ' the redwood-fault literal rides x2 and the registers format x1 on BOTH sides (the R50 hologram stays);'
        ' the prefault hologram format, the census uf-ok tail and the clamp symbol ride x1 on BOTH sides (the R51 knives stay);'
        ' the archaeology formats x1 + the channel fingerprints x2 + the counter symbol ride in r52 only (the R52 knives);'
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
    # delta-0/+24, R49 rode +672 of __LINKEDIT, R50 +0, R51 +80 of __LINKEDIT.
    # The law is the ACCOUNTING, not the sign: __TEXT must not shrink, the mid
    # segments keep their sizes and shift by the same amount, and __LINKEDIT
    # takes the rest.
    assert shift >= 0, '__TEXT shrank %d B -- nothing in this round can shrink it' % shift
    # THE R52 QUANTUM: __TEXT growth is PAGE-QUANTIZED -- R52's archaeology +
    # fingerprints landed a 16384 B page-aligned jump (16 KiB exactly), the
    # first round to move a whole page.  The band is an AUDIT window, not a
    # size budget: 32 KiB pins it at two quanta; the payload law below still
    # forces every other segment to keep its size and __LINKEDIT to absorb
    # the remainder, so nothing can hide in the wider band.
    assert shift < 32768, '__TEXT grew %d B, past the 32 KiB forensics band' % shift
    moved = []
    for (name_o, fo_o, fs_o), (name_n, fo_n, fs_n) in zip(segs_o, segs_n):
        if name_o == b'__TEXT':
            continue
        if name_o == b'__LINKEDIT':
            if fo_n != fo_o + shift:
                moved.append((name_o.decode(), 'fileoff', fo_o, fo_n, 'want +shift'))
            continue
        # zero-size segments (the __LAST tail mark) carry no file bytes and
        # no layout semantics -- the R52 quantum (shift 16384) exposed them
        if fs_o == 0 and fs_n == 0:
            continue
        if fs_n != fs_o or fo_n != fo_o + shift:
            moved.append((name_o.decode(), fo_o, fs_o, fo_n, fs_n))
    assert not moved, 'payload segments did not follow the __TEXT growth law: %s' % moved[:4]
    link_o = [s for s in segs_o if s[0] == b'__LINKEDIT'][0]
    link_n = [s for s in segs_n if s[0] == b'__LINKEDIT'][0]
    assert link_n[2] - link_o[2] == delta - shift, \
        'file delta %d != __TEXT %d + __LINKEDIT %d -- something else grew' % (delta, shift, link_n[2] - link_o[2])
    say('segment law: %d segments, name sequence identical; __TEXT +%d B (< 32 KiB, page-quantized -- the R52 quantum); every other'
        ' payload segment keeps its size and shifts by exactly +%d; __LINKEDIT +%d B == the rest'
        % (len(segs_n), shift, shift, link_n[2] - link_o[2]))

    # 5. strings delta, volatile regions AND code segments masked
    #    THE RUN-12 LESSON (40976283142): machine code is not strings.  The
    #    round's edits live in __TEXT_EXEC, and changed instruction bytes can
    #    carry a >= 8-char printable run by accident (ADRP/ADD immediate
    #    fields).  Every literal this law polices (the markers, the verdict
    #    formats, the Darwin stamps) lives in a DATA region, so the scan masks
    #    the code segments out on BOTH sides and keeps its full teeth on every
    #    data region.
    #    THE R49-FIX LAW, FORM-AGNOSTIC FOR R52: the two pointer-run artifacts
    #    (XNULIVE2's neighbour, the (4(... table) are LAYOUT shadows -- an
    #    embedded pointer's VALUE moves with every data shift, and the R52
    #    round moves the layout again (new cstrings).  Their strings are
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
        return s == R52_MARKER or s.startswith(b'redwood-exec:') or \
            s.startswith(b'redwood-fault:') or \
            s.startswith(b'redwood-pager:') or s.startswith(b'sigcensus:') or \
            s.startswith(b'siglive:') or s.startswith(b'r49-deliver:') or \
            run_like(s) or DARWIN.search(s)

    def allowed_loss(s):
        return s == R51_MARKER or run_like(s) or \
            s.startswith(b'redwood-pager:') or s.startswith(b'sigcensus:') or \
            s.startswith(b'redwood-exec:') or \
            DARWIN.search(s)

    bad_gain = [s for s in gained if not allowed_gain(s)]
    bad_loss = [s for s in lost if not allowed_loss(s)]
    assert not bad_gain, 'r52 gains unexpected strings (outside symtab/load cmds, code segs masked): %s' % [s[:64] for s in bad_gain[:5]]
    assert not bad_loss, 'r52 loses unexpected strings: %s' % [s[:64] for s in bad_loss[:5]]
    assert R52_MARKER in gained, 'the R52 marker is missing from the masked delta'
    assert R51_MARKER in lost, 'the R51 marker did not show up as a masked loss (it must be retired from rodata)'
    # the archaeology formats are PREFIXES of their printable runs (each is
    # its own cstring, NUL-bounded like the rest) -- the run-1 lesson
    # (41159150831), now the general law: a prefix does not sit in the gained
    # SET as itself; match the run by its head.
    assert any(s.startswith(INFOS_HEX_FMT) for s in gained), \
        'the R52 infos-hex head format is missing from the masked delta'
    assert any(s.startswith(INFOS_LN_FMT) for s in gained), \
        'the R52 hex line format is missing from the masked delta'
    # the channel fingerprints: two cstrings, both must ride the delta
    assert any(s.startswith(b'sigcensus: chan=log') for s in gained), \
        'the chan=log fingerprint is missing from the masked delta'
    assert any(s.startswith(b'sigcensus: chan=direct') for s in gained), \
        'the chan=direct fingerprint is missing from the masked delta'
    # R52 adds nothing and retires nothing else: the R51 formats must NOT
    # show up as losses (they stay verbatim), and the R51 base's own strings
    # must all ride (no retirement happened outside the marker)
    assert any(s.startswith(EXEC_FMT) for s in n_str) and any(s.startswith(EXEC_FMT) for s in o_str), \
        'the R51 prefault hologram format must ride BOTH sides verbatim'
    assert any(s.startswith(SIGCENSUS2_FMT) for s in n_str) and any(s.startswith(SIGCENSUS2_FMT) for s in o_str), \
        'the R51 census tail format must ride BOTH sides verbatim'
    # the form-agnostic survival check: each side still carries a form of
    # both pointer-run artifacts (they may have moved, they may not vanish)
    assert any(run_like(s) and s.startswith(b'XNULIVE2') for s in n_str), \
        'the r52 kernel lost every XNULIVE2 run form'
    assert any(run_like(s) and s.startswith(b'XNULIVE2') for s in o_str), \
        'the r51 base lost every XNULIVE2 run form?'
    assert any(run_like(s) and s.startswith(b'(4(') for s in n_str), \
        'the r52 kernel lost every pointer-table run form'
    assert any(run_like(s) and s.startswith(b'(4(') for s in o_str), \
        'the r51 base lost every pointer-table run form?'
    assert not in_masks(new.find(R52_MARKER), mask_n), \
        'the R52 marker sits inside a masked span -- smuggled?'
    say('strings law: masked delta (code segs masked too -- machine code is not strings, the run-12 law):'
        ' gained %d (the R52 marker + the archaeology formats + the channel fingerprints + %d Darwin stamp lines + %d layout-run forms),'
        ' lost %d (the R51 marker + %d Darwin stamp lines + %d layout-run forms); the marker at %#x rides real rodata'
        % (len(gained), sum(1 for s in gained if DARWIN.search(s)), sum(1 for s in gained if run_like(s)),
           len(lost), sum(1 for s in lost if DARWIN.search(s)), sum(1 for s in lost if run_like(s)),
           new.find(R52_MARKER)))

    # 6. symtab census
    assert sym_n['nsyms'] >= sym_o['nsyms'], \
        'r52 carries FEWER symbols than r51 (%d < %d)' % (sym_n['nsyms'], sym_o['nsyms'])
    say('symtab law:  nsyms %d -> %d (strtab %#x -> %#x)'
        % (sym_o['nsyms'], sym_n['nsyms'], sym_o['strsize'], sym_n['strsize']))

    say('GATE GREEN: the r52 kernel IS the r51 kernel plus the archaeology (the copyin bytes,'
        ' printed whole) and the channel fingerprints (bytes differ, __TEXT +%d B with the payload'
        ' law held, the marker swapped same-length).' % shift)
    if args.report:
        open(args.report, 'w').write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
