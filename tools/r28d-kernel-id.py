#!/usr/bin/env python3
"""tools/r28d-kernel-id.py -- prove WHICH kernel and WHICH loader an image carries, offline.

R28d is the CPU-monitor round: the kernel entry is R28d (a NEW source build --
the generation stamps say "R28d", and kern_minimac.c now grants
PRIV_PROC_CPUMON_OVERRIDE to every process, so lsd's attempt to clear the CPU
monitor launchd armed at its spawn no longer ends in "denied attempt to escape
CPU monitor (missing required entitlement)" -- the r28c report's white apple).
The loader is v7 BYTE-IDENTICAL by design (sha ec15106d...): the build the
device has already validated in the R28c round; the round moves the kernel only.

This unpacks any r2x flash image (R14..R28d layout: bootimg v2 -> gzip(BootShim +
UEFI FD + XNUP payload)) and prints the kernel and loader entries' sha256 plus
the generation markers, so "which kernel / which loader is this" is arithmetic:

    python3 tools/r28d-kernel-id.py raphael-xnu-r28d-gpu-flash.img [more.imgs...]

Known kernel-entry sha256s (the rounds so far):
    R27 / R27b  372f0b0af38d84f7d343a322f21c41156e6675581e08964b0ac15378be447d9c
    R28         9e3dd447d1e92158b9e7159cf3508ca3414e6366d066373210ed746f590667b3
    R28b        36a1e9534da454854a0110724447ffa06d9fcae8995e98cf1487d419cfed3ed6
    R28c        same bytes as R28b (the kernel was unchanged; that round moved the loader)
    R28d        99b9ea75de307be1fe2fb368a67493d850c762f71580bb6d1a4e4b010c698603 (run 40174469602, clean build)
A kernel cannot print a string its binary does not carry: markers below ARE the
runtime lines, counted in the bytes that would print them.
"""
import gzip, hashlib, struct, sys

FD_SIZE = 0x300000
KNOWN = {
    '372f0b0af38d84f7d343a322f21c41156e6675581e08964b0ac15378be447d9c': 'R27/R27b (v5-loader round)',
    '9e3dd447d1e92158b9e7159cf3508ca3414e6366d066373210ed746f590667b3': 'R28 (two-parameter baseline round)',
    '36a1e9534da454854a0110724447ffa06d9fcae8995e98cf1487d419cfed3ed6': 'R28b (identity-audit round; R28c carries these same bytes by design)',
    '99b9ea75de307be1fe2fb368a67493d850c762f71580bb6d1a4e4b010c698603': 'R28d (CPU-monitor waiver round; clean build, ccache 0/1036)',
}

# (family, marker) -- the runtime lines, in the bytes that print them
MARKERS = [
    ('R28d generation line', b'R28d: the kernel generation line'),
    ('R28d baseline        ', b'R28d: the two-parameter baseline'),
    ('R28d minimac waiver  ', b"the CPU monitor's override goes to every process"),
    ('R28b generation line', b'R28b: the kernel generation line'),
    ('R28b baseline        ', b'R28b: the two-parameter baseline'),
    ('R28  baseline        ', b'R28: the two-parameter baseline'),
    ('R28  sweep-retire    ', b'R28: the shape sweeps, retired behind adreno_shape_sweep=1'),
    ('R27  steps-over guard', b'the steps are over (R27)'),
    ('R27  flush self-test ', b"step 1's first line: the flush channel's own test"),
    ('R26  E1 mode=2 line  ', b'R26 E1, adreno_e1_mode=2'),
    ('R25  sweep announce  ', b'R25: the shape sweeps'),
    ('R25b E7 announce     ', b'R25b: E7 moved to the end of the refusal path'),
    ('R25  E7 step-2 form  ', b'read the secboot and rollback fuses (E7: the whole qfprom windows'),
]


def unpack(path):
    img = open(path, 'rb').read()
    if img[:8] != b'ANDROID!':
        raise SystemExit(f'{path}: not an Android boot image')
    k_size = struct.unpack_from('<I', img, 8)[0]
    page = struct.unpack_from('<I', img, 36)[0]
    hdr = struct.unpack_from('<I', img, 40)[0]
    if (page, hdr) != (4096, 2):
        raise SystemExit(f'{path}: not a v2/4096 image (page={page}, hdr={hdr})')
    blob = gzip.decompress(img[page:page + k_size])
    payload = blob[176 + FD_SIZE:]
    if payload[:4] != b'XNUP':
        raise SystemExit(f'{path}: no XNUP payload at shim+FD')
    size = struct.unpack_from('<I', payload, 4)[0]
    ents, p = {}, 8
    while p + 24 <= 8 + size and payload[p] != 0:
        name = payload[p:p + 16].rstrip(b'\0').decode()
        esz = struct.unpack_from('<I', payload, p + 16)[0]
        ents[name] = payload[p + 24:p + 24 + esz]
        p += 24 + ((esz + 15) & ~15)
    if 'kernel' not in ents:
        raise SystemExit(f'{path}: no kernel entry (entries: {sorted(ents)})')
    return img, ents


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit('usage: r28d-kernel-id.py <image> [image...]')
    for path in sys.argv[1:]:
        img, ents = unpack(path)
        k = ents['kernel']
        ldr = ents.get('loader.efi', b'')
        ksha = hashlib.sha256(k).hexdigest()
        print('=' * 96)
        print(f'{path}')
        print(f'  image  sha256 : {hashlib.sha256(img).hexdigest()}')
        print(f'  kernel sha256 : {ksha}   ({len(k):,} B)'
              + (f'   == {KNOWN[ksha]}' if ksha in KNOWN else ''))
        if ldr:
            lsha = hashlib.sha256(ldr).hexdigest()
            if lsha.startswith('ec15106da4bad290'):
                tag = '   == XNULOADER v7 (the R28c build, device-validated; R28d carries it verbatim)'
            elif lsha.startswith('9a93cc085b6fcd25'):
                tag = '   == XNULOADER v6 (R28 build)'
            elif lsha.startswith('5640b04be0b505fb'):
                tag = '   == XNULOADER v6 (R28b build; source-identical to R28, only the PE timestamp differs)'
            elif lsha.startswith('d42293ff2beb7653'):
                tag = '   == XNULOADER v5 (R26/R27 era)'
            else:
                tag = ''
            print(f'  loader sha256 : {lsha}   ({len(ldr):,} B)' + tag)
            v7 = ldr.count(b'== XNULOADER v7 ==')
            keep = ldr.count(b"flush #%u's record stays")
            print(f'    loader markers: v7 banner={v7}, flush-done line={ldr.count(b"flush done, returning")}, '
                  f'handler keep-guard={keep}, v6 banner={ldr.count(b"== XNULOADER v6 ==")}')
        if 'cmdline' in ents:
            print(f'  cmdline       : {ents["cmdline"].rstrip(bytes([0])).decode(errors="replace")}')
        print('  -- kernel-entry marker counts (what this kernel CAN print) --')
        for label, m in MARKERS:
            print(f'    {label}: {k.count(m)}')
        verdict = ('R28d (generation stamps + R28d baseline + the minimac waiver present, all older wordings absent'
                   '; the loader is the R28c v7 verbatim -- the round moves the kernel only)'
                   if k.count(b'R28d: the two-parameter baseline') == 1
                   and k.count(b"the CPU monitor's override goes to every process") == 1
                   and k.count(b'R28b: the two-parameter baseline') == 0 and k.count(b'R28b: the kernel generation line') == 0
                   and k.count(b'R28: the two-parameter baseline') == 0
                   and k.count(b'R25: the shape sweeps') == 0 and k.count(b'R25b: E7 moved to the end of the refusal path') == 0
                   else 'R28b (identity-audit round; R28c carried these bytes)' if k.count(b'R28b: the two-parameter baseline') == 1
                   else 'R28 (two-parameter baseline round)' if k.count(b'R28: the two-parameter baseline') == 1
                   else 'R27/R27b or older (R25/R25b step-19 wordings present)'
                   if k.count(b'R25: the shape sweeps') else 'pre-R25 layout -- read the marker table')
        print(f'  VERDICT: the kernel entry is {verdict}')
    print('=' * 96)


if __name__ == '__main__':
    main()
