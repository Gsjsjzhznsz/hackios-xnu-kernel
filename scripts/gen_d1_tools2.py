#!/usr/bin/env python3
"""Generate tools/d1-kernel-id.py from tools/r61-kernel-id.py (phase 2 of the
D1 generation; the flash_build and kernel_gate are gen_d1_tools.py's).

The d1 id tool: R61 joins the KNOWN table (its sha pinned, its own verdict
branch -- it is the D1 gate BASE), and the new-kernel branch demands the D1
marker x1 with the R61 marker retired, then tells the D1 story."""
import pathlib
import py_compile

TOOLS = pathlib.Path('/home/z/my-project/repo_hackios/tools')
R61_MARKER = 'R61: the association answer read aloud in hex, all bytes'
D1_MARKER = 'D1: the stalled command buffers read aloud, word by word'


def sub1(text, old, new, what):
    n = text.count(old)
    assert n == 1, '%s: %d hits (want exactly 1)' % (what, n)
    return text.replace(old, new)


src = (TOOLS / 'r61-kernel-id.py').read_text()

src = sub1(src, '''"""tools/r61-kernel-id.py -- identify the kernel inside the boot images (rounds R33..R60).

R60 is the association-retry round.  R59's live forensics settled the wall:
the SAE commit+confirm exchange COMPLETED (the AP answered status 0 to both),
the PMK and PMKID were derived, the association request carried the textbook
WPA3 RSN (AKM 8, MFPR|MFPC, PMKID) -- and the access point still answered
ASSOCIATION REFUSED status 53, its own "try again later" while the PMKSA it
just committed settles.  The R59 loop retried NO-ANSWER only, so one refusal
ended the join.  R60 retries the refusal (5 attempts, 200 ms apart).  This
tool names the R60 kernel and every predecessor.''',
           '''"""tools/d1-kernel-id.py -- identify the kernel inside the boot images (rounds R33..R61).

D1 is the GPU stall forensics round.  D0b's capture convicted the wall: the
SMMU refused the 3D core at 0x3500 (status 0x80000402, syndrome 0x20002,
own-walk L2[0]=0) 12 ms after swap 30 hit its 500 ms gate; the CP idle-stopped
(ring 1299==1299); not one SpringBoard frame reached the panel; and the 0x3500
shape stood three generations running (R39 shutdown, R41 reset, D0b runtime).
D1 arms the one exit every stall reports through: redwood_gpu_report() hands
the CP's IB1 registers to RedwoodGPU.cpp's submission ledger, which names the
submission whose buffer the core stopped in, notes the pool reuse, reads the
96 words around the stop from the kernel's own map, and hunts every 0x3500 and
near-NULL pointer operand against the ranges the submission itself named.
This tool names the D1 kernel, the R61 base, and every predecessor.''' , 'id doc head')

src = sub1(src, """usage:
  python3 tools/r54-kernel-id.py img [--expect-loader-sha SHA]""",
           """usage:
  python3 tools/d1-kernel-id.py img [--expect-loader-sha SHA]""", 'id doc usage')

src = sub1(src, "R60_SHA = 'e0570b2adfa46400f38834aab1fa95dc4f838dd18cd700368817535bf8c106a7'",
           "R60_SHA = 'e0570b2adfa46400f38834aab1fa95dc4f838dd18cd700368817535bf8c106a7'\n"
           "R61_SHA = '8ef12fefbb2edc1cafdb0bb38bca4d0bb6adffee1771fdd333efc9c77b7b5ae7'", 'id R61_SHA const')

src = sub1(src, "    R60_SHA: 'R60 (the association-retry round, run 41439630833; the R61 gate BASE)',\n}",
           "    R60_SHA: 'R60 (the association-retry round, run 41439630833; the R61 gate BASE)',\n"
           "    R61_SHA: 'R61 (the association-answer round, run 41626845673; the D1 gate BASE)',\n}", 'id KNOWN R61 entry')

src = sub1(src, "R61_MARKER = b'%s'" % R61_MARKER,
           "R61_MARKER = b'%s'\nD1_MARKER = b'%s'" % (R61_MARKER, D1_MARKER), 'id D1_MARKER const')

src = sub1(src, """            print('  VERDICT: the kernel entry is R60 (the association-retry round, run 41439630833; the R61 gate BASE)')
        else:""",
           """            print('  VERDICT: the kernel entry is R60 (the association-retry round, run 41439630833; the R61 gate BASE)')
        elif ksha == R61_SHA:
            assert k.count(R61_MARKER) == 1 and k.count(R60_MARKER) == 0 and k.count(R54_MARKER) == 0, \\
                'the sha says R61 but the markers disagree'
            assert k.count(b'sigcensus: decode ') == 1 and k.count(b'redwood-exec: decode ') == 2, \\
                'the sha says R61 but the decode wires disagree'
            print('  VERDICT: the kernel entry is R61 (the association-answer round, run 41626845673; the D1 gate BASE)')
        else:""", 'id R61 sha branch inserted')

src = sub1(src, """            assert k.count(R61_MARKER) == 1, 'a new kernel without the R61 marker is nobody'
            assert k.count(R60_MARKER) == 0, 'a new kernel still carrying the retired R60 marker'""",
           """            assert k.count(D1_MARKER) == 1, 'a new kernel without the D1 marker is nobody'
            assert k.count(R61_MARKER) == 0, 'a new kernel still carrying the retired R61 marker'
            assert k.count(R60_MARKER) == 0, 'a new kernel still carrying the retired R60 marker'""", 'id new-kernel marker asserts')

src = sub1(src, """                print('  VERDICT: the kernel entry is R61 (the evidence round: R60\\'s retry ran -- three asks'
                      ' printed (the stale label; the loop is five), the access point answered one status-53'
                      ' refusal (67 bytes of elements, AID 11475) then went SILENT: the wall sits at the'
                      ' association-request construction or in the answer\\'s own semantics; R61 reads the'
                      ' answer\\'s elements out in hex (sixteen to a line), prints the honest ask count, and'
                      ' each ask announces itself; the R60 retry law, the decode census and the archaeology'
                      ' stay on top; the R44..R54 wires all stay; every R33 marker and every R38 display/Wi-Fi'
                      ' mark rides'
                      ' -- the copyin\\'d template IS dyld\\'s static bytes, every pointer field still wears its'
                      ' chained-fixup rebase encoding, and the upstream slide arithmetic walks a chain fragment'
                      ' into the address, broken = real + 0xFF70000000000000 -- so R54 decodes BEFORE the'
                      ' prefetch: target = field & 0xFFFFFFFFF, dyld_base = aii_addr - infos_target, the'
                      ' prefetch walks the REAL page; the census counts the decodes; the archaeology stays'
                      ' on top; the R44..R52 wires all stay; every R33 marker and every R38 display/Wi-Fi mark'
                      ' rides'
                      + (", and it is the flash build's own bytes" if mine else '')
                      + "; the round's shape is the r54_kernel_gate's verdict)")""",
           """                print('  VERDICT: the kernel entry is D1 (the GPU stall forensics round: D0b convicted'
                      ' the wall at the SpringBoard render storm -- the SMMU refused the 3D core at 0x3500'
                      ' (status 0x80000402, syndrome 0x20002, own-walk L2[0]=0) 12 ms after swap 30 hit its'
                      ' 500 ms gate, the CP idle-stopped (ring 1299==1299), and not one SpringBoard frame'
                      ' reached the panel; the 0x3500 shape stood three generations running -- R39 shutdown,'
                      ' R41 reset, D0b runtime).  D1 arms the one exit every stall reports through:'
                      ' redwood_gpu_report() hands the CP\\'s IB1 registers to RedwoodGPU.cpp\\'s submission'
                      ' ledger, which names the submission whose buffer the core stopped in, notes the pool'
                      ' reuse (the same place was an earlier submission\\'s command buffer too), reads the 96'
                      ' words around the stop from the kernel\\'s own map, and hunts every 0x3500 and'
                      ' near-NULL pointer operand against the ranges the submission itself named; the R60'
                      ' retry law, the decode census and the archaeology stay on top; the R44..R54 wires all'
                      ' stay; every R33 marker and every R38 display/Wi-Fi mark rides'
                      + (", and it is the flash build's own bytes" if mine else '')
                      + "; the round's shape is the d1_kernel_gate's verdict)")""", 'id D1 verdict text')

src = sub1(src, '''    ap.add_argument('--expect-loader-sha', default=None, help="the R54 loader sha (the flash build re-packs it)")''',
           '''    ap.add_argument('--expect-loader-sha', default=None, help="the R54..R61 loader sha (the flash build re-packs it)")''', 'id loader help')
src = sub1(src, '''    ap.add_argument('--expect-kernel-sha', default=None, help="the R54 kernel sha (from the flash build)")''',
           '''    ap.add_argument('--expect-kernel-sha', default=None, help="the D1 kernel sha (from the flash build)")''', 'id kernel help')

(TOOLS / 'd1-kernel-id.py').write_text(src)
print('tools/d1-kernel-id.py written (%d lines)' % len(src.splitlines()))

import py_compile
for t in ('d1_flash_build.py', 'd1_kernel_gate.py', 'd1-kernel-id.py'):
    py_compile.compile(str(TOOLS / t), doraise=True)
    print('py_compile OK: %s' % t)
