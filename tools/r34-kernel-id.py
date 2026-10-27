#!/usr/bin/env python3
"""tools/r34-kernel-id.py -- prove WHICH kernel, loader, volume and cmdline an image carries, offline.

R34 is the GL-test round: the kernel is R33's own bytes (420f4b71...) VERBATIM -- the round
changes NO kernel code -- and the GPU image's cmdline is R33's verbatim.  The ONE thing that
moves is the ramdisk entry: out goes the R13-lineage md1 (372c7fcb...), in comes the R34
GL-test volume (Mesa's libOSMesa.8.dylib + gl_test + u2f_test + etc/rc.jobs = rc.jobs.gltest),
built by tools/r34_volume_build.py from those very same R13 bytes.  gl_test draws one triangle
through freedreno over the kernel's RedwoodGPU service and puts it on the panel; its verdict
lines are the round's device record.

    python3 tools/r34-kernel-id.py <image> [image...]
        [--expect-volume-sha SHA]   name the R34 GL-test volume (the flash build knows it)

Known kernel-entry sha256s (the rounds so far; see r33-kernel-id.py for the full table):
    R33         420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560 (run 40549952622,
                clean build; the resident round: the GPU STAYS ON, the service RedwoodGPU hands the
                block to the system; H1 confirmed on the device's own logdump -- SpringBoard soft-
                renders as expected, the GL userland being this round)
    R34         the same bytes BY DESIGN (the GL round ships no kernel change)

Known ramdisk-entry sha256s:
    R13..R33   372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4  (the R13 bytes)
    R34        the GL-test volume (the sha the flash build passes in; its file-name table --
               libOSMesa.8.dylib / gl_test / u2f_test / rc.jobs -- is grepped in the raw bytes
               as an independent fingerprint)
"""
import argparse
import gzip
import hashlib
import struct
import sys

FD_SIZE = 0x300000
R33_SHA = '420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560'
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'
V7 = 'ec15106da4bad2905f5adefae194a1209daea9c0f464239d619804ec68f77cc2'
KNOWN = {
    R33_SHA: 'R33 (run 40549952622, clean build; the resident round; R34 rides these bytes VERBATIM)',
    '05de7d6a1228cc7e273385274cc91c96f2c23dc4cb9589c2c74197a5f2e7ed70': 'R31c (the inverted scmsweep gate)',
    'e0ab7fde98ee7095b8c8908d2d57c4a913cb094aef7bb5f0ad573e529376990e': 'R31b (the u16 fix; the QUE wedge)',
    '8c86a90e45c8098b363cd797bb6ce31ea297e4255cb4d0ef4e91826f341230d2': 'R31 (the u32 phentsize suicide)',
    '4a7088302cea1e81e044f531b4323690c617a1f271997aee7a8fbce0d255fe8f': 'R30 (the swallowed-code recovery)',
    '911a69df7434ee76e004d2e5243b290cf73dc582e2f86f019bf5e986faf89c4e': 'R28m (the M2/N3 round)',
    '916d7719d400bfd69350b69df9fd3a0d082cc5c1cc007920184c58184eb6fe89': 'R28k (the refusal-continues round)',
    '57db84e7400f1a353930e1a7ef4825ffd17ef3d0252c04efd3f77508479d6b82': 'R28j (the sequential T2 round)',
    '3a0d746dbc03136bd23bce1b82f6280b8bfd443b2e34ad8f93cfb3b6669fee53': 'R28i (the T2 ACCEPTED round)',
    '9cbcac8a4d66b1a64ec3e7b723055be367027ec190d9ef89b7c8bb0e106855a3': 'R28g (the lsd SIGBUS round)',
    '99b9ea75de307be1fe2fb368a67493d850c762f71580bb6d1a4e4b010c698603': 'R28d (the CPU-monitor round)',
    '36a1e9534da454854a0110724447ffa06d9fcae8995e98cf1487d419cfed3ed6': 'R28b (identity-audit round)',
    '9e3dd447d1e92158b9e7159cf3508ca3414e6366d066373210ed746f590667b3': 'R28 (the two-parameter baseline)',
    '372f0b0af38d84f7d343a322f21c41156e6675581e08964b0ac15378be447d9c': 'R27/R27b (the v5-loader round)',
}
KNOWN_RAMDISK = {
    R13_VOLUME: 'R13..R33 (the R13 bytes; R33 and the R34 daily ride them VERBATIM)',
}
GL_NAMES = (b'libOSMesa.8.dylib', b'gl_test', b'u2f_test', b'rc.jobs')


def parse(path):
    img = open(path, 'rb').read()
    assert img[:8] == b'ANDROID!', path + ': not an Android boot image'
    page = struct.unpack_from('<I', img, 36)[0]
    k_size = struct.unpack_from('<I', img, 8)[0]
    r_size = struct.unpack_from('<I', img, 16)[0]
    dtb_size = struct.unpack_from('<I', img, 1648)[0]
    k_off = page
    r_off = (k_off + k_size + page - 1) // page * page
    d_off = (r_off + r_size + page - 1) // page * page
    assert d_off + dtb_size == len(img), path + ': the truncation law'
    blob = gzip.decompress(img[k_off:k_off + k_size])
    x = blob.find(b'XNUP')
    assert x > 0, path + ': no payload'
    size = struct.unpack('<I', blob[x + 4:x + 8])[0]
    ents, i = {}, x + 8
    while i + 24 <= x + 8 + size and blob[i] != 0:
        name = blob[i:i + 16].rstrip(b'\0').decode()
        esize = struct.unpack('<I', blob[i + 16:i + 20])[0]
        ents[name] = blob[i + 24:i + 24 + esize]
        i += 24 + ((esize + 15) & ~15)
    return ents


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('images', nargs='+')
    ap.add_argument('--expect-volume-sha', default=None, help='the R34 GL-test volume sha (from the flash build)')
    args = ap.parse_args()

    for path in args.images:
        print('=' * 96)
        ents = parse(path)
        for name in ('loader.efi', 'kernel', 'ramdisk', 'cmdline'):
            if name not in ents:
                continue
            data = ents[name]
            dsha = hashlib.sha256(data).hexdigest()
            if name == 'loader.efi':
                print(f'  loader  sha256 : {dsha}   ({len(data):,} B)'
                      + ('   == XNULOADER v7 (the device-validated one)' if dsha == V7 else ''))
            elif name == 'kernel':
                print(f'  kernel  sha256 : {dsha}   ({len(data):,} B)'
                      + ('   ' + KNOWN[dsha] if dsha in KNOWN else '   UNKNOWN to this tool'))
            elif name == 'ramdisk':
                print(f'  ramdisk sha256 : {dsha}   ({len(data):,} B)')
            else:
                print(f'  cmdline       : {data.rstrip(bytes([0])).decode(errors="replace")}')
        k = ents.get('kernel', b'')
        rmd = ents.get('ramdisk', b'')
        cmd = ents.get('cmdline', b'')
        # ---- kernel verdict: R34 = R33's bytes, VERBATIM
        if hashlib.sha256(k).hexdigest() == R33_SHA:
            assert k.count(b'R33: the kernel generation line') == 2 and \
                   k.count(b'R33: the resident round') == 1 and \
                   k.count(b'THE GPU STAYS ON for the system to draw with') == 1 and \
                   k.count(b'SCMDMP: R33 begins') == 1 and \
                   k.count(b'R32: the kernel generation line') == 0, \
                'the sha says R33 but the markers disagree'
            print('  VERDICT: the kernel entry is R34 (R33\'s bytes VERBATIM: the GL round changes no kernel '
                  'code; the resident keep + the vendor .mdt machinery + the SCMDMP sweep all ride)')
        else:
            print('  VERDICT: the kernel entry is NOT R34\'s expected bytes (the sha table above says which)')
        # ---- cmdline verdict
        toks = cmd.rstrip(bytes([0])).decode(errors='replace').split()
        if 'gpu=1' in toks:
            if {'gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1'} <= set(toks):
                print('  VERDICT: the cmdline is the GPU image WITH the full-step tokens AND the resident '
                      'switch -- R33\'s set, unchanged (R34 adds no token: the round moves the volume, not the boot args)')
            else:
                print('  VERDICT: the cmdline carries gpu=1 but NOT the complete R33 token set')
        else:
            print('  VERDICT: the cmdline is the daily image WITHOUT any adreno token (the control, unchanged)')
        # ---- ramdisk verdict
        rsha = hashlib.sha256(rmd).hexdigest()
        if rsha == R13_VOLUME:
            print('  VERDICT: the ramdisk entry is R13..R33 VERBATIM (correct for the R34 DAILY: the control '
                  'group carries no GL)')
        elif args.expect_volume_sha and rsha == args.expect_volume_sha:
            hits = {n: rmd.count(n) for n in GL_NAMES}
            ok = all(v >= 1 for v in hits.values())
            print('  VERDICT: the ramdisk entry is the R34 GL-test volume (the flash build\'s own sha; '
                  'name-table fingerprint in the raw bytes: ' +
                  ', '.join('%s=%d' % (n.decode(), c) for n, c in hits.items()) +
                  (')' if ok else ' -- A NAME IS MISSING: %s)' % [n for n, c in hits.items() if not c]))
        else:
            print('  VERDICT: the ramdisk entry is UNRECOGNIZED here (neither the R13 lineage nor the '
                  'expected R34 GL-test volume)')
    print('=' * 96)


if __name__ == '__main__':
    main()
