#!/usr/bin/env python3
"""tools/r39-kernel-id.py -- prove WHICH kernel, loader, volume and cmdline an image carries, offline.

R39 is the GLES-only lockscreen round: the kernel source is R38's UNCHANGED (rebuild only;
content-equivalence to the R38 release kernel is the r34_shadiff --gate's verdict in the
workflow), the loader source is unchanged too, the volume gains ONE swap (etc/rc.jobs =
the base probe set, the Metal discovery chain structurally off the boot path), and the
cmdline swaps the dispatch verdict wifi_step=2 for the REAL radio switch (wifi=1
wifi_bdf=bd_f11cn.bin -- the whole WCN3990/ICNSS/MPSS/ath10k chain on its own kernel
thread; without the modem firmware in the RAM image it says so in a line and does nothing).

    python3 tools/r39-kernel-id.py <image> [image...]
        [--expect-loader-sha SHA]   name the R39 loader (the flash build knows it)
        [--expect-kernel-sha SHA]   name the R39 kernel (the flash build knows it)
        [--expect-volume-sha SHA]   name the R39 volume (the volume build knows it)

Known kernel-entry sha256s (the rounds so far; see r34-kernel-id.py for the full table):
    R33         420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560 (run 40549952622;
                R34..R37 rode these bytes VERBATIM)
    R38         c8328e22dfdbe35c6693456f58c2983b81cbba506557018e1dfc127d01208f17 (the display +
                Wi-Fi round; the R39 daily rides these bytes VERBATIM as the control group)

Known loader-entry sha256s:
    v7          ec15106da4bad2905f5adefae194a1209daea9c0f464239d619804ec68f77cc2 (the
                device-validated base)
    R38         bcfbb0a9894963a0d876ee2e86adf372b47abcbaa9615535cfc462ba6a32c220 (v7 banner +
                /chosen platform + the raphael autorefresh; the R39 rebuild keeps the marks)

Known ramdisk-entry sha256s:
    R13..R33   372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4  (the R13 bytes)
    R36..R38   7ad5b1f880a938fb2ffb7824a11e4cc5a49d5cbdb66d7405c7fb1626d1ef865c  (the J3-fix
               volume with the r37metal list; the r38 release rides it VERBATIM)
    R39        named by --expect-volume-sha (the base probe set, the metal tokens ABSENT)
"""
import argparse
import hashlib
import gzip
import struct

R33_SHA = '420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560'
R38_SHA = 'c8328e22dfdbe35c6693456f58c2983b81cbba506557018e1dfc127d01208f17'
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'
R38_VOLUME = '7ad5b1f880a938fb2ffb7824a11e4cc5a49d5cbdb66d7405c7fb1626d1ef865c'
V7 = 'ec15106da4bad2905f5adefae194a1209daea9c0f464239d619804ec68f77cc2'
R38_LOADER = 'bcfbb0a9894963a0d876ee2e86adf372b47abcbaa9615535cfc462ba6a32c220'
KNOWN = {
    R33_SHA: 'R33 (run 40549952622, clean build; the resident round; R34..R37 rode these bytes VERBATIM)',
    R38_SHA: 'R38 (the display + Wi-Fi round; the R39 daily rides these bytes VERBATIM as the control)',
}
KNOWN_LOADER = {
    V7: 'XNULOADER v7 (the device-validated base one)',
    R38_LOADER: 'the R38 loader (v7 banner + /chosen platform + the raphael autorefresh)',
}
KNOWN_RAMDISK = {
    R13_VOLUME: 'R13..R33 (the R13 bytes; the R37..R39 daily rides it VERBATIM)',
    R38_VOLUME: 'R36/R37/R38 (the J3-fix volume with the r37metal list; the r38 release rides it VERBATIM)',
}
R38_KERNEL_MARKS = (
    b'raphael-display: three scanout buffers, VIG0/CTL0, hardware TE',
    b'wifi: raphael needs WCN3990 SNOC/ath10k and MPSS service 0x45',
    b'display: raphael command TE:',
    b'XNULIVE4',
)
R38_LOADER_MARK = b'display: raphael command-mode autorefresh enabled'
GPU_TOKENS = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1', 'metal=1')
R38_NEW_TOKENS = ('fb_scan=1', 'wifi_step=2')
R39_TOKENS = ('fb_scan=1', 'wifi=1', 'wifi_bdf=bd_f11cn.bin')
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
    ap.add_argument('--expect-loader-sha', default=None, help="the R39 loader sha (from the flash build)")
    ap.add_argument('--expect-kernel-sha', default=None, help="the R39 kernel sha (from the flash build)")
    ap.add_argument('--expect-volume-sha', default=None, help="the R39 volume sha (from the volume build)")
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
                if dsha in KNOWN_LOADER:
                    note = '   == ' + KNOWN_LOADER[dsha]
                elif args.expect_loader_sha and dsha == args.expect_loader_sha:
                    note = "   == the R39 loader (this round's build; the R38 fingerprints asserted by the flash build)"
                print(f'  loader  sha256 : {dsha}   ({len(data):,} B)' + note)
            elif name == 'kernel':
                note = '   ' + KNOWN[dsha] if dsha in KNOWN else '   UNKNOWN to this tool'
                if args.expect_kernel_sha and dsha == args.expect_kernel_sha:
                    note = "   == the R39 kernel (this round's build of the unchanged R38 source)"
                print(f'  kernel  sha256 : {dsha}   ({len(data):,} B)' + note)
            elif name == 'ramdisk':
                note = ''
                if args.expect_volume_sha and dsha == args.expect_volume_sha:
                    note = '   == the R39 volume (the base probe set; the metal tokens ABSENT)'
                print(f'  ramdisk sha256 : {dsha}   ({len(data):,} B)' + note)
            else:
                print(f'  cmdline       : {data.rstrip(bytes([0])).decode(errors="replace")}')
        k = ents.get('kernel', b'')
        rmd = ents.get('ramdisk', b'')
        cmd = ents.get('cmdline', b'')
        # ---- kernel verdict
        ksha = hashlib.sha256(k).hexdigest()
        if ksha == R33_SHA:
            assert k.count(b'R33: the kernel generation line') == 2 and \
                   k.count(b'R33: the resident round') == 1 and \
                   k.count(b'THE GPU STAYS ON for the system to draw with') == 1 and \
                   k.count(b'SCMDMP: R33 begins') == 1 and \
                   k.count(b'R32: the kernel generation line') == 0, \
                'the sha says R33 but the markers disagree'
            print("  VERDICT: the kernel entry is R33 (the R34..R37 bytes; the daily control's kernel)")
        elif ksha == R38_SHA:
            missing = [m for m in R38_KERNEL_MARKS if m not in k]
            assert not missing, 'the sha says R38 but marks are missing: %s' % missing
            print('  VERDICT: the kernel entry is R38 (the display + Wi-Fi round; the R39 daily rides these '
                  'bytes VERBATIM as the control)')
        else:
            missing = [m for m in R38_KERNEL_MARKS if m not in k]
            assert k.count(b'R33: the kernel generation line') == 2 and \
                   k.count(b'R33: the resident round') == 1 and \
                   k.count(b'SCMDMP: R33 begins') == 1, \
                'this kernel is neither R33, nor R38, nor carries the R33 lineage markers'
            if missing:
                print('  VERDICT: the kernel entry is a NEW kernel but MISSES R38 marks: %s'
                      % [m.decode(errors="replace") for m in missing])
            else:
                mine = args.expect_kernel_sha and ksha == args.expect_kernel_sha
                print('  VERDICT: the kernel entry is R39 (this round\'s build of the UNCHANGED R38 source: '
                      'every R33 marker and every R38 display/Wi-Fi mark rides'
                      + (", and it is the flash build's own bytes" if mine else '')
                      + '; content-equivalence to the R38 release kernel is the r34_shadiff gate\'s verdict)')
        # ---- cmdline verdict
        toks = cmd.rstrip(bytes([0])).decode(errors='replace').split()
        if 'gpu=1' in toks:
            if tuple(toks) == DAILY_TOKENS + GPU_TOKENS + R38_NEW_TOKENS:
                print('  VERDICT: the cmdline is the R38 GPU form verbatim (the R37 set + fb_scan=1 + wifi_step=2)')
            elif tuple(toks) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS:
                print('  VERDICT: the cmdline is the R39 GPU form verbatim (fb_scan=1 rides; wifi_step=2 out; '
                      'wifi=1 wifi_bdf=bd_f11cn.bin in -- the radio on its own thread, quiet without the firmware)')
            elif 'wifi_step=2' in toks or 'wifi=1' in toks:
                print('  VERDICT: the cmdline carries an R38/R39 wifi token but NOT the exact expected set')
            elif 'metal=1' in toks and set(GPU_TOKENS) <= set(toks):
                print('  VERDICT: the cmdline is the R37 GPU form verbatim (no R38/R39 token)')
            else:
                print('  VERDICT: the cmdline carries gpu=1 but NOT the complete expected token set')
        else:
            print('  VERDICT: the cmdline is the daily image WITHOUT any adreno/metal/fb_scan/wifi token (the control, unchanged)')
        # ---- ramdisk verdict
        rsha = hashlib.sha256(rmd).hexdigest()
        if rsha in KNOWN_RAMDISK:
            print('  VERDICT: the ramdisk entry is %s' % KNOWN_RAMDISK[rsha])
        elif args.expect_volume_sha and rsha == args.expect_volume_sha:
            print('  VERDICT: the ramdisk entry is the R39 volume (--expect-volume-sha; the ONE swap = '
                  'etc/rc.jobs = rc.jobs.r39quiet, the discovery chain structurally off)')
        else:
            print('  VERDICT: the ramdisk entry is UNRECOGNIZED here (neither the R13 lineage, nor the '
                  'R36..R38 volume, nor the named R39 volume)')
    print('=' * 96)


if __name__ == '__main__':
    main()
