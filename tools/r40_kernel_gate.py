#!/usr/bin/env python3
"""tools/r40_kernel_gate.py -- the R40 kernel's gate, against the R39 release kernel.

R40 changes ONE kernel file (MiniIOMobileFramebuffer.cpp): the first dropped frame now
calls redwood_gpu_report("frame dropped") -- the R39 autopsy's own verdict on its own
forensic gap (a 500 ms swap wait sits below gpuWait's 1000 ms report bar, so the stalled
core died silently: CP_RB_RPTR stuck at 1488 of 2439, RBBM_INT_0_STATUS 0, nothing on
the log but the drops).  The build also carries the round's used-marker string.

So the r34_shadiff --gate content-equivalence verdict (identical size, diffs confined
to the UUID + version-timestamp clusters) CANNOT apply: this round's kernel is SUPPOSED
to differ, by exactly one patch.  The gate proves the difference IS one patch:

  1. size:    r40 > r39, and the delta stays under 64 KiB (the patch is ~a dozen lines)
  2. marker:  the R40 marker is in r40 exactly once and in r39 zero times
  3. lineage: every R33 generation marker and every R38 display/Wi-Fi mark rides in r40
  4. strings: every string r40 gains over r39 is either the R40 marker or a Darwin
              version line (the wall-clock build stamp); every string it loses is a
              Darwin version line -- NO other text moves
  5. bytes:   the differing runs cluster into <= 32 regions and <= 256 KiB total (the
              patch's code+rodata plus the build-meta clusters; a wrong build blows
              every bound at once)

usage:
  python3 tools/r40_kernel_gate.py --r39-kernel base/r39-kernel.bin \
      --r40-kernel xnu-src/.../mach.development.bcm2837 \
      [--report release/r40-kernel-gate.txt]
"""
import argparse
import hashlib
import re
import sys

R40_MARKER = b'R40: the first dropped frame buys the full core snapshot'

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


def strings(data, minimum=8):
    out = set()
    for run in re.findall(rb'[\x20-\x7e]{%d,}' % minimum, data):
        out.add(run)
    return out


def diff_runs(a, b):
    runs, n, i = [], min(len(a), len(b)), 0
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            runs.append((i, j - i))
            i = j
        else:
            i += 1
    if len(a) != len(b):
        runs.append((n, abs(len(a) - len(b))))
    return runs


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

    # 4. strings delta
    o_str, n_str = strings(old), strings(new)
    gained, lost = n_str - o_str, o_str - n_str
    bad_gain = [s for s in gained if s != R40_MARKER and not DARWIN.search(s)]
    bad_loss = [s for s in lost if not DARWIN.search(s)]
    assert not bad_gain, 'r40 gains unexpected strings: %s' % [s[:64] for s in bad_gain[:5]]
    assert not bad_loss, 'r40 loses unexpected strings: %s' % [s[:64] for s in bad_loss[:5]]
    say('strings law: gained %d (the R40 marker + %d Darwin stamp lines), lost %d (all Darwin stamps)'
        % (len(gained), len(gained) - 1, len(lost)))

    # 5. byte clusters
    runs = diff_runs(old, new)
    total = sum(size for _, size in runs)
    assert len(runs) <= 32, 'the diffs cluster into %d regions, over the 32 bound' % len(runs)
    assert total <= 262144, 'the differing bytes total %d, over the 256 KiB bound' % total
    say('cluster law: %d differing regions, %d B total (bounds 32 / 256 KiB)' % (len(runs), total))
    for at, size in runs[:8]:
        say('  run at %#x, %d B' % (at, size))

    say('GATE GREEN: the r40 kernel IS the r39 kernel plus one forensic patch.')
    if args.report:
        with open(args.report, 'w') as f:
            f.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
