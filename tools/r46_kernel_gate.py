#!/usr/bin/env python3
"""tools/r46_kernel_gate.py -- the R46 kernel's gate, against the R45 release kernel.

R46 is the sigbirth round.  The R45 device verdict (6354 logdump lines) fired
ZERO shots -- zero sigpost lines under the recorder proven live, enabled and
sitting at bsd_exception() -- while the wave GREW to 144 deaths (lsd x108 +
analyticsd x28 + backboardd x8, a fresh victim set every boot) AND
SpringBoard composed its first real frames: flip 2/3 latched (CoreAnimation's
IOSurface 4 'BGRA' 1125 x 2436 upload through redwood-gl -> render_update ->
raphael-display), the white apple -- a stage R43/R44 never reached.
Silence at the funnel means nothing crossed it: no signal was ever DELIVERED.
And the base source kills the R45 terminal theory: proc_terminate() sends
only memorystatus_on_terminate(p)'s verdict (SIGKILL/SIGTERM) -- no
user-supplied signal number rides proc_info.  The one birth funnel that
explains BOTH silences (usertrap AND sigpost) is the ux_handler: the task's
own exception-handler thread (the logdump's "exc handler[own pid]" is
literal) translating a real fault via ux_exception() (EXC_BAD_ACCESS with
anything but KERN_INVALID_ADDRESS -> SIGBUS) and posting it with
threadsignal(.., TRUE) -- a path that never calls psignal, never produces a
bsd_exception(EXC_SOFT_SIGNAL) delivery, and never reaches exception_triage.

R46's one knife: the recorder moves UPSTREAM to the BIRTHS.  A new
sigbirth_trace() in files/osfmk/arm64/user_exception_trace.c -- one throttled
line per signal born: target, signal, code, the birth site (the caller's
return address, unslid) and who is running -- wired via redwood.patch at the
four variable-signal funnels: psignal (kill(2), the resource policy,
proc_terminate's own verdict), psignal_with_reason, psignal_uthread
(pthread_kill) and threadsignal (the ux_handler's own mouth,
handle_ux_exception+0x10c in the R45 autopsy).  psignal_locked / try_thread /
vfork / thread_with_reason stay unhooked (compile-time-constant signal
numbers, none can carry the wave).  A FRESH (name, signal) budget (first 4
per key, then every 16th -- the R42 partition law) so a launchd SIGCHLD
storm cannot eat the wave's lines; the same systrace switch; the R44/R45
wires (usertrap at exception_triage_thread, sigpost at bsd_exception) stay.
The marker swaps same-length (56 B).  The sigbirth printf format is the
round's ONE new data string (x1 in r46, x0 in the base); the sigpost format
now rides x1 on BOTH sides (the R45 wire, unchanged).

The gate laws keep the R45 shape (the ACCOUNTING, not the sign), re-pointed
at the R45 release kernel:

  1. size:     the bytes MUST differ; the delta stays under 64 KiB; zero or
               positive are both legal (R41/R43/R45 rode __TEXT padding;
               four new call sites plus the ledger function may do either).
  2. marker:   the R46 marker (SAME byte length as the R45 marker's) is in
               r46 exactly once and in the base zero times; the R45 marker
               is retired (x0 in r46, x1 in the base).
  3. lineage:  every R33 generation marker, every R38 display/Wi-Fi mark, the
               XNULIVE4 channel mark and the "frame dropped" literal ride in
               r46; the forensic literal "redwood-pager: verdict" rides x6
               on BOTH sides (R46 adds none); the sigpost format rides x1 on
               BOTH sides (the R45 wire, unchanged); the sigbirth format
               rides x1 in r46 and x0 in the base -- the birth wire is in
               the binary, not just in the diff.
  4. segments: the LC_SEGMENT_64 name sequence is identical; __TEXT must not
               shrink and stays under 16 KiB of growth; every OTHER payload
               segment keeps its file size and shifts by exactly the __TEXT
               growth; __LINKEDIT absorbs the rest of the file delta.
  5. strings:  computed with the volatile regions AND THE CODE SEGMENTS
               MASKED OUT (the run-12 lesson: machine code is not strings):
               every string r46 gains is the R46 marker, a "sigbirth:"
               format, a "redwood-pager:" format or a Darwin version line;
               every string it loses is the R45 marker or a Darwin line.
               The marker must live OUTSIDE the masked spans.
  6. symtab:   r46 carries at least as many symbols as the R45 release
               (sigbirth_trace is one new global function symbol).

usage:
  python3 tools/r46_kernel_gate.py --r45-kernel base/r45-kernel.bin \
      --r46-kernel xnu-src/.../mach.development.bcm2837 \
      [--report release/r46-kernel-gate.txt]
"""
import argparse
import hashlib
import re
import struct
import sys

R46_MARKER = b'R46: the signal birth ledger names every hand that feeds'
R45_MARKER = b'R45: the signal funnel writes who died and what was sent'
R46_MARKER_LEN_LAW = len(R46_MARKER) == len(R45_MARKER)
assert R46_MARKER_LEN_LAW, 'the R46 marker must keep the R45 marker byte length'
REPORT_LITERAL = b'frame dropped'
SIGPOST_FMT = b'sigpost: %s[%d] exception %d code 0x%llx 0x%llx\n'
SIGBIRTH_FMT = b'sigbirth: %s[%d] signal %d code 0x%llx from 0x%llx by %s[%d]\n'
PAGER_LITERAL = b'redwood-pager: verdict'

R45_KERNEL = 'd38974bfe3239297f0a03b4280072b4681817d3e9053f314cbf10e0f86309bd0'

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
    ap.add_argument('--r45-kernel', required=True)
    ap.add_argument('--r46-kernel', required=True)
    ap.add_argument('--report', default=None)
    args = ap.parse_args()

    old = open(args.r45_kernel, 'rb').read()
    new = open(args.r46_kernel, 'rb').read()
    lines = []

    def say(s):
        print(s)
        lines.append(s)

    osha, nsha = hashlib.sha256(old).hexdigest(), hashlib.sha256(new).hexdigest()
    say('r45 base kernel: %d B, sha256 %s' % (len(old), osha))
    assert osha == R45_KERNEL, 'the r45 base kernel is %s, not the release byte %s' % (osha, R45_KERNEL)
    say('r46 kernel: %d B, sha256 %s' % (len(new), nsha))

    lc_end_o, segs_o, sym_o = macho_layouts(old)
    lc_end_n, segs_n, sym_n = macho_layouts(new)

    # 1. size (the R41/R43/R45 lesson: the ACCOUNTING, not the sign -- the
    #    trace's edits may fit __TEXT padding, so delta 0 is a legal outcome)
    delta = len(new) - len(old)
    assert nsha != osha, 'the r46 kernel is byte-identical to r45 (sha %s) -- the patch did not land?' % nsha
    assert abs(delta) < 65536, 'the size delta %d B exceeds the 64 KiB patch band' % delta
    say('size law:    bytes differ (new sha), delta %+d B -- the band is |d| < 64 KiB;'
        ' zero and positive are both legal here (the sigbirth edits may fit __TEXT'
        ' padding; the law is the accounting, not the sign)' % delta)

    # 2. the markers
    assert new.count(R46_MARKER) == 1, 'the R46 marker rides x%d, want exactly 1' % new.count(R46_MARKER)
    assert old.count(R46_MARKER) == 0, 'the r45 kernel already carries the R46 marker?'
    assert new.count(R45_MARKER) == 0, 'the R45 marker still rides in r46 -- it was to be retired'
    assert old.count(R45_MARKER) == 1, 'the r45 base kernel lost its own marker'
    say('marker law:  the R46 marker x1 in r46, x0 in r45; the R45 marker retired (x0 in r46, x1 in r45)')

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
    assert new.count(SIGPOST_FMT) == 1, 'the sigpost format rides x%d in r46, want exactly 1 (the R45 wire stays)' % new.count(SIGPOST_FMT)
    assert old.count(SIGPOST_FMT) == 1, 'the r45 base lost the sigpost format?'
    # THE R46 RECORDER, PROVEN PACKED: the sigbirth line's format string rides
    # x1 in r46 and x0 in the base -- the birth wire is in the binary, not
    # just in the diff.  (The R45 verdict in reverse: a recorder whose format
    # never packed would be a no-op on device.)
    assert new.count(SIGBIRTH_FMT) == 1, 'the sigbirth format rides x%d in r46, want exactly 1' % new.count(SIGBIRTH_FMT)
    assert old.count(SIGBIRTH_FMT) == 0, 'the r45 base already carries the sigbirth format?'
    say('lineage law: R33 generation 4 + R38 display/WiFi 5 + XNULIVE4 + the "frame dropped" literal'
        ' all ride; the "redwood-pager: verdict" literal rides x6 on BOTH sides (R46 adds none);'
        ' the sigpost format rides x1 on BOTH sides (the R45 wire);'
        ' the sigbirth format rides x1 in r46, x0 in the base (the birth wire, packed)')

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
        return s == R46_MARKER or s.startswith(b'sigbirth:') or s.startswith(b'redwood-pager:') or DARWIN.search(s)

    bad_gain = [s for s in gained if not allowed_gain(s)]
    bad_loss = [s for s in lost if not (s == R45_MARKER or DARWIN.search(s))]
    assert not bad_gain, 'r45 gains unexpected strings (outside symtab/load cmds, code segs masked): %s' % [s[:64] for s in bad_gain[:5]]
    assert not bad_loss, 'r45 loses unexpected strings: %s' % [s[:64] for s in bad_loss[:5]]
    assert R46_MARKER in gained, 'the R46 marker is missing from the masked delta'
    assert R45_MARKER in lost, 'the R45 marker did not show up as a masked loss (it must be retired from rodata)'
    assert not in_masks(new.find(R46_MARKER), mask_n), \
        'the R46 marker sits inside a masked span -- smuggled?'
    say('strings law: masked delta (code segs masked too -- machine code is not strings, the run-12 law):'
        ' gained %d (the R46 marker + the sigbirth format + the redwood-pager formats + %d Darwin stamp lines),'
        ' lost %d (the R45 marker + Darwin stamps); the marker at %#x rides real rodata'
        % (len(gained), sum(1 for s in gained if DARWIN.search(s)), len(lost), new.find(R46_MARKER)))

    # 6. symtab census
    assert sym_n['nsyms'] >= sym_o['nsyms'], \
        'r46 carries FEWER symbols than r45 (%d < %d)' % (sym_n['nsyms'], sym_o['nsyms'])
    say('symtab law:  nsyms %d -> %d (strtab %#x -> %#x)'
        % (sym_o['nsyms'], sym_n['nsyms'], sym_o['strsize'], sym_n['strsize']))

    say('GATE GREEN: the r46 kernel IS the r45 kernel plus the sigbirth ledger'
        ' (bytes differ, __TEXT +%d B with the payload law held, the marker swapped same-length).' % shift)
    if args.report:
        open(args.report, 'w').write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
