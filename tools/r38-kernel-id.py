#!/usr/bin/env python3
"""tools/r38-kernel-id.py -- prove WHICH kernel, loader, volume and cmdline an image carries, offline.

R38 is the display + Wi-Fi round: the kernel changes for real (the first kernel round since
R33) -- the display round is the reference repo's phone-proven TE scanout (three scanout
buffers, VIG0 latched at the panel's own tear effect, fb_scan=1), and the Wi-Fi stack lands
whole (WCN399x/ICNSS/MPSS/ath10k-SNOC + wifi/lx/ath10k as the WIFI_OBJECT; wifi_step=2 rides
as the dispatch verdict).  The loader is rebuilt too: /chosen "hackios,platform", the raphael
command-mode autorefresh, the front-buffer screenshot.  The volume rides R37's bytes VERBATIM.

    python3 tools/r38-kernel-id.py <image> [image...]
        [--expect-loader-sha SHA]   name the R38 loader (the flash build knows it)
        [--expect-kernel-sha SHA]   name the R38 kernel (the flash build knows it)

Known kernel-entry sha256s (the rounds so far; see r34-kernel-id.py for the full table):
    R33         420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560 (run 40549952622;
                R34..R37 rode these bytes VERBATIM)
    R38         the sha the flash build passes in (built this round from source)

Known ramdisk-entry sha256s:
    R13..R33   372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4  (the R13 bytes)
    R34..R37   7ad5b1f880a938fb2ffb7824a11e4cc5a49d5cbdb66d7405c7fb1626d1ef865c  (the R37 round's
               volume: R36's J3-fix init riding, rc.jobs.r37metal; the R38 GPU image rides it
               VERBATIM -- this round ships no volume change)
"""
import argparse
import gzip
import hashlib
import struct
import sys

R33_SHA = '420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560'
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'
R37_VOLUME = '7ad5b1f880a938fb2ffb7824a11e4cc5a49d5cbdb66d7405c7fb1626d1ef865c'
V7 = 'ec15106da4bad2905f5adefae194a1209daea9c0f464239d619804ec68f77cc2'
KNOWN = {
    R33_SHA: 'R33 (run 40549952622, clean build; the resident round; R34..R37 rode these bytes VERBATIM)',
}
KNOWN_RAMDISK = {
    R13_VOLUME: 'R13..R33 (the R13 bytes; the R37/R38 daily rides it VERBATIM)',
    R37_VOLUME: 'R36/R37 (the J3-fix volume with rc.jobs.r37metal; the R38 GPU image rides it VERBATIM)',
}
R38_KERNEL_MARKS = (
    b'raphael-display: three scanout buffers, VIG0/CTL0, hardware TE',
    b'wifi: raphael needs WCN3990 SNOC/ath10k and MPSS service 0x45',
    b'display: raphael command TE:',
    b'XNULIVE4',
)
R38_LOADER_MARK = b'display: raphael command-mode autorefresh enabled'
GPU_TOKENS = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1', 'metal=1')
NEW_TOKENS = ('fb_scan=1', 'wifi_step=2')
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
    ap.add_argument('--expect-loader-sha', default=None, help='the R38 loader sha (from the flash build)')
    ap.add_argument('--expect-kernel-sha', default=None, help='the R38 kernel sha (from the flash build)')
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
                note = ''
                if dsha == V7:
                    note = '   == XNULOADER v7 (the device-validated base one)'
                elif args.expect_loader_sha and dsha == args.expect_loader_sha:
                    note = '   == the R38 loader (v7 banner + the raphael autorefresh)'
                print(f'  loader  sha256 : {dsha}   ({len(data):,} B)' + note)
            elif name == 'kernel':
                note = '   ' + KNOWN[dsha] if dsha in KNOWN else '   UNKNOWN to this tool'
                if args.expect_kernel_sha and dsha == args.expect_kernel_sha:
                    note = '   == the R38 kernel (this round\'s build)'
                print(f'  kernel  sha256 : {dsha}   ({len(data):,} B)' + note)
            elif name == 'ramdisk':
                print(f'  ramdisk sha256 : {dsha}   ({len(data):,} B)')
            else:
                print(f'  cmdline       : {data.rstrip(bytes([0])).decode(errors="replace")}')
        k = ents.get('kernel', b'')
        rmd = ents.get('ramdisk', b'')
        cmd = ents.get('cmdline', b'')
        # ---- kernel verdict
        if hashlib.sha256(k).hexdigest() == R33_SHA:
            assert k.count(b'R33: the kernel generation line') == 2 and \
                   k.count(b'R33: the resident round') == 1 and \
                   k.count(b'THE GPU STAYS ON for the system to draw with') == 1 and \
                   k.count(b'SCMDMP: R33 begins') == 1 and \
                   k.count(b'R32: the kernel generation line') == 0, \
                'the sha says R33 but the markers disagree'
            print('  VERDICT: the kernel entry is R33 (the R34..R37 bytes; NOT this round\'s kernel)')
        else:
            missing = [m for m in R38_KERNEL_MARKS if m not in k]
            assert k.count(b'R33: the kernel generation line') == 2 and \
                   k.count(b'R33: the resident round') == 1 and \
                   k.count(b'SCMDMP: R33 begins') == 1, \
                'this kernel is neither R33 nor carries the R33 lineage markers'
            if missing:
                print('  VERDICT: the kernel entry is a NEW kernel but MISSES R38 marks: %s'
                      % [m.decode(errors="replace") for m in missing])
            else:
                r38 = args.expect_kernel_sha and hashlib.sha256(k).hexdigest() == args.expect_kernel_sha
                print('  VERDICT: the kernel entry is R38 (the display + Wi-Fi round: every R33 marker rides'
                      + (', and it is the flash build\'s own bytes' if r38 else '')
                      + '; the display round\'s strings and the raphael Wi-Fi branch are in the raw bytes)')
        # ---- cmdline verdict
        toks = cmd.rstrip(bytes([0])).decode(errors='replace').split()
        if 'gpu=1' in toks:
            if set(NEW_TOKENS) <= set(toks) and set(GPU_TOKENS) <= set(toks) and \
                    len(toks) == len(DAILY_TOKENS) + len(GPU_TOKENS) + len(NEW_TOKENS):
                print('  VERDICT: the cmdline is the GPU image WITH the R37 set and EXACTLY the two R38 '
                      'tokens (fb_scan=1 -- the display round\'s TE measurement; wifi_step=2 -- the '
                      'Wi-Fi stack\'s zero-side-effect dispatch verdict)')
            elif NEW_TOKENS[0] in toks or NEW_TOKENS[1] in toks:
                print('  VERDICT: the cmdline carries an R38 token but NOT the exact R37+R38 set')
            elif 'metal=1' in toks and set(GPU_TOKENS) <= set(toks):
                print('  VERDICT: the cmdline is the R37 GPU form verbatim (no R38 token)')
            else:
                print('  VERDICT: the cmdline carries gpu=1 but NOT the complete R37 token set')
        else:
            print('  VERDICT: the cmdline is the daily image WITHOUT any adreno/metal/fb_scan token (the control, unchanged)')
        # ---- ramdisk verdict
        rsha = hashlib.sha256(rmd).hexdigest()
        if rsha in KNOWN_RAMDISK:
            print('  VERDICT: the ramdisk entry is %s' % KNOWN_RAMDISK[rsha])
        else:
            print('  VERDICT: the ramdisk entry is UNRECOGNIZED here (neither the R13 lineage nor the R37 volume)')
    print('=' * 96)


if __name__ == '__main__':
    main()
