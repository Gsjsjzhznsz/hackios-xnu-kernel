#!/usr/bin/env python3
"""tools/r36-kernel-id.py -- prove WHICH kernel, loader, volume and cmdline an image carries, offline.

R36 is the J3-closing round: the kernel is R33's own bytes (420f4b71...) VERBATIM -- the
round changes NO kernel code -- and the GPU image's cmdline rides R35's set EXACTLY
(metal=1 included; no new token).  The ramdisk entry: out goes the R35 GL+Metal volume,
in comes the R36 J3-fix volume (sbin/init = the union-mount init of userland pin 6f04967,
etc/rc.jobs = rc.jobs.r36metal, every other byte the R35 lineage's), built by
tools/r36_volume_build.py from the R35 bytes.

    python3 tools/r36-kernel-id.py <image> [image...]
        [--expect-volume-sha SHA]   name the expected volume (the flash build knows it)
        [--expect-volume-name STR]  how to SAY that volume in the verdict (the base plate's
                                    check names the R35 volume; the flash build's re-proof
                                    names the R36 J3-fix volume)

Known kernel-entry sha256s (the rounds so far; see r34-kernel-id.py for the full table):
    R33         420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560 (run 40549952622,
                clean build; the resident round: the GPU STAYS ON, the service RedwoodGPU hands the
                block to the system; H1 confirmed on the device's own logdump)
    R34, R35, R36   the same bytes BY DESIGN (the GL round, the Metal round and the J3 round
                ship no kernel change)

Known ramdisk-entry sha256s:
    R13..R33   372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4  (the R13 bytes)
    R34        7e15aaa6bb047fca48ea8b2b4ce7c84b0757deee76b95bbc2f825f6922e46abf  (the GL-test volume;
               the R35 daily rides it VERBATIM)
    R35        a6dcfc919b2752c626bbedc29601b0b1a07bdf58c8ba16cf89c7ed53919d3117  (the GL+Metal volume;
               the R36 daily rides it VERBATIM)
    R36        the J3-fix volume (the sha the flash build passes in; its file-name table --
               libredwood-gl.dylib / RedwoodMetal.bundle / metal_props / metal_test /
               metal_drv_test / libOSMesa.8.dylib / rc.jobs -- is grepped in the raw bytes as an
               independent fingerprint; sbin/init's identity is the volume manifest's business)
"""
import argparse
import gzip
import hashlib
import struct
import sys

FD_SIZE = 0x300000
R33_SHA = '420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560'
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'
R34_VOLUME = '7e15aaa6bb047fca48ea8b2b4ce7c84b0757deee76b95bbc2f825f6922e46abf'
R35_VOLUME = 'a6dcfc919b2752c626bbedc29601b0b1a07bdf58c8ba16cf89c7ed53919d3117'
V7 = 'ec15106da4bad2905f5adefae194a1209daea9c0f464239d619804ec68f77cc2'
KNOWN = {
    R33_SHA: 'R33 (run 40549952622, clean build; the resident round; R34..R36 ride these bytes VERBATIM)',
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
    R13_VOLUME: 'R13..R33 (the R13 bytes)',
    R34_VOLUME: 'R34 (the GL-test volume; the R35 daily rides it VERBATIM)',
    R35_VOLUME: 'R35 (the GL+Metal volume; the R36 daily rides it VERBATIM)',
}
GLM_NAMES = (b'libOSMesa.8.dylib', b'gl_test', b'u2f_test', b'libredwood-gl.dylib',
             b'RedwoodMetal.bundle', b'metal_props', b'metal_test', b'metal_drv_test', b'rc.jobs')
GPU_TOKENS = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1', 'metal=1')
DAILY_TOKENS = ('touchscreen=1', 'buttons=1', 'cpus=8', 'ufs=2', 'bootcpu=7', 'root_apfs=ufs_userdata')


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
    ap.add_argument('--expect-volume-sha', default=None, help='the expected volume sha (from the flash build or the base-plate check)')
    ap.add_argument('--expect-volume-name', default='the expected volume',
                    help='how to say that volume in the verdict ("the R35 GL+Metal volume" for the\n'
                         'base plate; "the R36 J3-fix volume" for the packed round)')
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
        # ---- kernel verdict: R36 = R33's bytes, VERBATIM
        if hashlib.sha256(k).hexdigest() == R33_SHA:
            assert k.count(b'R33: the kernel generation line') == 2 and \
                   k.count(b'R33: the resident round') == 1 and \
                   k.count(b'THE GPU STAYS ON for the system to draw with') == 1 and \
                   k.count(b'SCMDMP: R33 begins') == 1 and \
                   k.count(b'R32: the kernel generation line') == 0, \
                'the sha says R33 but the markers disagree'
            print('  VERDICT: the kernel entry is R36 (R33\'s bytes VERBATIM: the J3 round changes no kernel '
                  'code; the resident keep + the vendor .mdt machinery + the SCMDMP sweep all ride)')
        else:
            print('  VERDICT: the kernel entry is NOT R36\'s expected bytes (the sha table above says which)')
        # ---- cmdline verdict
        toks = cmd.rstrip(bytes([0])).decode(errors='replace').split()
        if 'gpu=1' in toks:
            if 'metal=1' in toks and set(GPU_TOKENS) <= set(toks) and len(toks) == len(DAILY_TOKENS) + len(GPU_TOKENS):
                print('  VERDICT: the cmdline is the R35/R36 GPU set (the resident switch and metal=1 -- '
                      'NO new token: the J3 round changes the volume\'s init, not the cmdline)')
            elif {'gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1'} <= set(toks) and 'metal=1' not in toks:
                print('  VERDICT: the cmdline carries gpu=1 and the complete R33 token set but NOT metal=1 '
                      '(the R34 form: this image asks Metal for nothing)')
            else:
                print('  VERDICT: the cmdline carries gpu=1 but NOT the complete R33 token set')
        else:
            print('  VERDICT: the cmdline is the daily image WITHOUT any adreno/metal token (the control, unchanged)')
        # ---- ramdisk verdict (an explicit --expect takes precedence: the caller names the
        # volume and gets the full name-table fingerprint as evidence, not just the lineage line)
        rsha = hashlib.sha256(rmd).hexdigest()
        if args.expect_volume_sha and rsha == args.expect_volume_sha:
            hits = {n: rmd.count(n) for n in GLM_NAMES}
            ok = all(v >= 1 for v in hits.values())
            print('  VERDICT: the ramdisk entry is %s (the sha the check itself names; '
                  'name-table fingerprint in the raw bytes: ' % args.expect_volume_name +
                  ', '.join('%s=%d' % (n.decode(), c) for n, c in hits.items()) +
                  (')' if ok else ' -- A NAME IS MISSING: %s)' % [n for n, c in hits.items() if not c]))
        elif rsha in KNOWN_RAMDISK:
            print('  VERDICT: the ramdisk entry is %s' % KNOWN_RAMDISK[rsha])
        else:
            print('  VERDICT: the ramdisk entry is UNRECOGNIZED here (neither a known lineage nor '
                  'the expected volume %s)' % args.expect_volume_name)
    print('=' * 96)


if __name__ == '__main__':
    main()
