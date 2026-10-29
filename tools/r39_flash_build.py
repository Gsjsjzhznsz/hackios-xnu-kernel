#!/usr/bin/env python3
"""tools/r39_flash_build.py -- pack the r39 GPU image: the R38 bytes with THREE entries swapped.

R39 is the GLES-only lockscreen round.  R38's device log (4803 lines) proved the display
round WORKS (the kernel's TE scanout latched flips 1..3 and CA's frames 1..4 finished
cleanly through it) and killed the props-on window for good: whether the first Metal walker
lives (R37: J1 established) or dies at the frontier (R36/R38: one SIGBUS poisons every
page-in after -- lsd x78, backboardd dead on touch, the white Apple), is not ours to
choose.  So the discovery chain leaves the boot path STRUCTURALLY: the volume's demo job
runs the base probe set (r39_volume_build), and the cmdline's dispatch verdict wifi_step=2
makes way for the REAL radio switch -- wifi=1 brings the whole WCN3990/ICNSS/MPSS/ath10k
chain up on its own kernel thread beside the system's start (it needs no daemon, and
without the modem firmware in the RAM image it says so in a line and does nothing).

The THREE things that move, all in the GPU image's payload:
  - the loader.efi entry: rebuilt from the unchanged R38 loader source (the v7 banner, the
    raphael autorefresh line and the /chosen platform write are fingerprint-gated; the
    bytes may differ from the R38 build only if the toolchain does)
  - the kernel entry: rebuilt from the UNCHANGED R38 kernel source (R39 changes no kernel
    line); content-equivalence to the R38 release kernel is the r34_shadiff --gate's
    verdict, run by the workflow -- here only the marker set is asserted
  - the cmdline entry: the R38 token set MINUS wifi_step=2 PLUS ' wifi=1 wifi_bdf=bd_f11cn.bin'
  - the ramdisk entry: the R39 volume (r39_volume_build's output) -- the round's ONE volume
    swap (etc/rc.jobs); asserted against --expect-volume-sha

The daily image: R38's, byte for byte (this round's control group).

Laws asserted here, from the bytes alone:
  - the base is an Android bootimg v2 whose layout obeys the truncation law
  - the base loader/kernel are EXACTLY the r38 release's (bcfbb0a9... / c8328e22...)
  - the daily base IS the r38 release daily byte for byte and carries NO gpu token
  - the GPU base carries the R38 token set verbatim (fb_scan=1 wifi_step=2 last)
  - the new cmdline is exactly base - wifi_step=2 + wifi=1 + wifi_bdf=bd_f11cn.bin
  - the packed payload stays inside the loader's 64 MB window
  - the output re-proves every law from its own bytes

usage:
  python3 tools/r39_flash_build.py --daily base/raphael-xnu-r38-flash.img \
      --gpu base/raphael-xnu-r38-gpu-flash.img --loader xnuloader/loader.efi \
      --kernel xnu-src/.../mach.development.bcm2837 --volume r39-md1-root.apfs \
      [--expect-volume-sha SHA] \
      --out-daily raphael-xnu-r39-flash.img --out-gpu raphael-xnu-r39-gpu-flash.img
"""
import argparse
import gzip
import hashlib
import struct
import sys

V7 = 'ec15106da4bad2905f5adefae194a1209daea9c0f464239d619804ec68f77cc2'   # the device-validated loader (the DAILY control's)
R33 = '420f4b717b5dd0fe2f0b236bdbf2c686da6a2dd0169b3c9985de8e600e1e3560'  # R33's kernel (the DAILY control's)
R38_LOADER = 'bcfbb0a9894963a0d876ee2e86adf372b47abcbaa9615535cfc462ba6a32c220'  # the r38 release loader (the BASE; R39 rebuilds it)
R38_KERNEL = 'c8328e22dfdbe35c6693456f58c2983b81cbba506557018e1dfc127d01208f17'  # the r38 release kernel; R39 rebuilds from the SAME source
R38_VOLUME = '7ad5b1f880a938fb2ffb7824a11e4cc5a49d5cbdb66d7405c7fb1626d1ef865c'  # the R37/R38 round's volume (the BASE ramdisk)
R13_VOLUME = '372c7fcb7730726a8fb806eec7251e0e75a4188f64db6fbaedcf5b7663672dd4'  # the R13-lineage md1 (the DAILY images' ramdisk)
R38_GPU = 'e06d1432c3c537a95f62635fb73c8219f769f0f90c5dc140ce6b65fd8c3669b0'
R38_DAILY = 'b0f9f6df4ea13ad9d45048e7ff15e33d7b2fcb67204d01964b1014517ca8a60b'
GPU_TOKENS = ('gpu=1', 'adreno=22', 'adreno_pause=0', 'adreno_watch=0', 'adreno_keep=1', 'metal=1')
R38_NEW_TOKENS = ('fb_scan=1', 'wifi_step=2')
R39_TOKENS = ('fb_scan=1', 'wifi=1', 'wifi_bdf=bd_f11cn.bin')
DAILY_TOKENS = ('touchscreen=1', 'buttons=1', 'cpus=8', 'ufs=2', 'bootcpu=7', 'root_apfs=ufs_userdata')
R38_LOADER_MARK = b'display: raphael command-mode autorefresh enabled'
PAYLOAD_MAX = 0x04000000   # the loader's window: PAYLOAD_BASE..+64 MB


def sha(b):
    return hashlib.sha256(b).hexdigest()


def parse_payload(p):
    assert p[:4] == b'XNUP', 'bad payload magic'
    size = struct.unpack('<I', p[4:8])[0]
    body = p[8:8 + size]
    ents, i = [], 0
    while i < len(body):
        name, esize, _ = struct.unpack('<16sII', body[i:i + 24])
        if name.rstrip(b'\0') == b'':
            break
        ents.append([name.rstrip(b'\0').decode(), body[i + 24:i + 24 + esize]])
        i += 24 + esize + (-esize % 16)
    return ents


def build_payload(ents):
    body = b''
    for name, data in ents:
        body += struct.pack('<16sII', name.encode(), len(data), 0) + data + b'\0' * (-len(data) % 16)
    body += b'\0' * 24
    return struct.pack('<II', 0x50554E58, len(body)) + body


def unpack(path):
    """(img bytes, {entry name: bytes}, geometry) of one flash image."""
    img = open(path, 'rb').read()
    assert img[:8] == b'ANDROID!', path + ': not an Android boot image'
    page = struct.unpack_from('<I', img, 36)[0]
    assert page == 4096, path + ': page size %d' % page
    k_size = struct.unpack_from('<I', img, 8)[0]
    r_size = struct.unpack_from('<I', img, 16)[0]
    dtb_size = struct.unpack_from('<I', img, 1648)[0]
    k_off = page
    r_off = (k_off + k_size + page - 1) // page * page
    d_off = (r_off + r_size + page - 1) // page * page
    assert d_off + dtb_size == len(img), path + ': the truncation law'
    assert img[d_off:d_off + 4] == b'\xd0\x0d\xfe\xed', path + ': dtb magic'
    blob = gzip.decompress(img[k_off:k_off + k_size])
    x = blob.find(b'XNUP')
    assert x > 0, path + ': no payload'
    ents = parse_payload(blob[x:])
    assert [n for n, _ in ents] == ['loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb'], path + ': entry set'
    return img, dict(ents), (r_off, r_size, d_off, dtb_size)


def cmdline_tokens(data):
    return data.rstrip(b'\0').decode().split()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--daily', required=True)
    ap.add_argument('--gpu', required=True)
    ap.add_argument('--loader', required=True, help="this round's loader.efi (xnuloader/build.sh)")
    ap.add_argument('--kernel', required=True, help="this round's kernel (the R38 source, rebuilt)")
    ap.add_argument('--volume', required=True, help="the R39 volume (r39_volume_build's output)")
    ap.add_argument('--expect-volume-sha', default=None, help='the R39 volume sha to assert')
    ap.add_argument('--out-daily', required=True)
    ap.add_argument('--out-gpu', required=True)
    args = ap.parse_args()

    loader = open(args.loader, 'rb').read()
    kernel = open(args.kernel, 'rb').read()
    volume = open(args.volume, 'rb').read()
    assert loader.count(b'== XNULOADER v7 ==') == 1, 'the R39 loader keeps the v7 banner'
    assert R38_LOADER_MARK in loader, 'the R39 loader carries the raphael autorefresh line'
    assert loader.count(b'hackios,platform') == 1, 'the R39 loader writes /chosen platform'
    assert kernel.count(b'R33: the kernel generation line') == 2, 'the R39 kernel keeps the R33 generation markers'
    assert b'raphael-display: three scanout buffers, VIG0/CTL0, hardware TE' in kernel, \
        'the R39 kernel carries the display round'
    assert b'wifi: raphael needs WCN3990 SNOC/ath10k and MPSS service 0x45' in kernel, \
        'the R39 kernel carries the raphael Wi-Fi branch'
    print('R39 loader: %d B, sha256 %s (v7 banner + raphael autorefresh fingerprint)'
          % (len(loader), sha(loader)))
    print('R39 kernel: %d B, sha256 %s (the R38 source rebuilt; equivalence is the shadiff gate)'
          % (len(kernel), sha(kernel)))
    print('R39 volume: %d B, sha256 %s' % (len(volume), sha(volume)))
    if args.expect_volume_sha:
        assert sha(volume) == args.expect_volume_sha, \
            'the R39 volume is %s, expected %s' % (sha(volume), args.expect_volume_sha)

    # ---- the daily base: R38's bytes, every law, then a byte copy (the round's control group)
    d_img, d_ents, _ = unpack(args.daily)
    assert sha(open(args.daily, 'rb').read()) == R38_DAILY, 'daily: the base is not the r38 release daily'
    assert sha(d_ents['loader.efi']) == V7, 'daily: the loader is not v7'
    assert sha(d_ents['kernel']) == R33, 'daily: the kernel is not R33 (420f4b71...)'
    assert sha(d_ents['ramdisk']) == R13_VOLUME, 'daily: the base ramdisk is not the R13 lineage'
    toks = cmdline_tokens(d_ents['cmdline'])
    assert tuple(toks) == DAILY_TOKENS, 'daily: the cmdline is not the daily token set: %s' % toks
    print('daily base: the r38 release daily (loader v7, kernel R33, volume R13-lineage) -- copying byte for byte')
    open(args.out_daily, 'wb').write(d_img)

    # ---- the GPU base: R38's bytes, the full token set, and THREE entries swapped + the new volume
    g_img, g_ents, geo = unpack(args.gpu)
    assert sha(open(args.gpu, 'rb').read()) == R38_GPU, 'gpu: the base is not the r38 release GPU image'
    assert sha(g_ents['loader.efi']) == R38_LOADER, 'gpu: the loader is not the r38 release loader'
    assert sha(g_ents['kernel']) == R38_KERNEL, 'gpu: the kernel is not the r38 release kernel'
    assert sha(g_ents['ramdisk']) == R38_VOLUME, 'gpu: the base ramdisk is not the R37/R38 volume'
    toks = cmdline_tokens(g_ents['cmdline'])
    assert tuple(toks) == DAILY_TOKENS + GPU_TOKENS + R38_NEW_TOKENS, \
        'gpu: the cmdline is not the R38 token set verbatim: %s' % toks
    print('gpu base: loader bcfbb0a9, kernel c8328e22, volume 7ad5b1f8, cmdline = the R38 token set verbatim')

    # the new cmdline: the same tokens minus wifi_step=2, plus wifi=1 + wifi_bdf -- as the payload carries it
    old_cmd = g_ents['cmdline'].rstrip(b'\0')
    new_cmd = old_cmd
    for t in (b' wifi_step=2',):
        assert t in new_cmd, 'the base cmdline lacks %s' % t.decode()
        new_cmd = new_cmd.replace(t, b'')
    for t in R39_TOKENS:
        if t == 'fb_scan=1':
            assert t.encode() in new_cmd, 'fb_scan=1 must ride'
            continue
        new_cmd += b' ' + t.encode()
    new_cmd += b'\0'
    assert tuple(cmdline_tokens(new_cmd)) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS, \
        'the new cmdline is not exactly R38 minus wifi_step=2 plus the R39 wifi tokens: %s' % cmdline_tokens(new_cmd)
    print('new cmdline: %s' % new_cmd.rstrip(b'\0').decode())

    budget = sum(len(loader if n == 'loader.efi' else kernel if n == 'kernel' else new_cmd if n == 'cmdline'
                     else volume if n == 'ramdisk' else g_ents[n]) for n in g_ents)
    assert budget <= PAYLOAD_MAX, 'payload %d B exceeds the loader window %d B' % (budget, PAYLOAD_MAX)
    print('payload budget: %d B of %d B (the loader window)' % (budget, PAYLOAD_MAX))

    order = ['loader.efi', 'kernel', 'ramdisk', 'cmdline', 'usb']
    new_ents = [[n,
                 loader if n == 'loader.efi'
                 else (kernel if n == 'kernel'
                       else (volume if n == 'ramdisk'
                             else (new_cmd if n == 'cmdline' else g_ents[n])))] for n in order]
    blob = gzip.decompress(g_img[4096:4096 + struct.unpack_from('<I', g_img, 8)[0]])
    x = blob.find(b'XNUP')
    blob_new = blob[:x] + build_payload(new_ents)
    blob_new += b'\0' * (-len(blob_new) % 16)
    kern = gzip.compress(blob_new, 6)

    out = bytearray(g_img[:4096])
    struct.pack_into('<I', out, 8, len(kern))
    out += kern + b'\0' * (-len(kern) % 4096)
    r_off, r_size, d_off, dtb_size = geo
    out += g_img[r_off:r_off + r_size] + b'\0' * (-r_size % 4096) + g_img[d_off:d_off + dtb_size]
    open(args.out_gpu, 'wb').write(bytes(out))
    print('packed: %s (%d B)' % (args.out_gpu, len(out)))

    # ---- the output re-proves every law from its own bytes
    o_img, o_ents, _ = unpack(args.out_gpu)
    assert o_ents['loader.efi'] == loader and R38_LOADER_MARK in o_ents['loader.efi']
    assert o_ents['kernel'] == kernel
    assert o_ents['usb'] == g_ents['usb']
    assert o_ents['ramdisk'] == volume, 'the packed ramdisk is not the R39 volume'
    assert tuple(cmdline_tokens(o_ents['cmdline'])) == DAILY_TOKENS + GPU_TOKENS + R39_TOKENS, \
        'the packed cmdline is not the R39 form'
    print('re-prove: usb/DTB byte-identical to the R38 base; loader/kernel/cmdline are the R39 three; '
          'the ramdisk is the R39 volume')
    print('gpu image: %s, sha256 %s' % (args.out_gpu, sha(open(args.out_gpu, 'rb').read())))
    print('daily image: %s, sha256 %s' % (args.out_daily, sha(open(args.out_daily, 'rb').read())))


if __name__ == '__main__':
    main()
