#!/usr/bin/env python3
"""
Pack the redwood UEFI (with the hook that starts the XNU loader) and an optional payload
into an Android boot image for `fastboot boot`.

  kernel = gzip(BootShim + UEFI FD + ["XNUP" container]) + DTB

The boot shim copies the FD to its base and the payload to PAYLOAD_BASE.

The UEFI pieces are taken from a Mu-Silicium tree built here (Mu-Silicium/, see uefi/README.md)
or, when there is none, from prebuilt/uefi/.

usage: mkxnuimg.py [-p payload.bin] [-o out.img]

Another device (common/devices/<name>.conf, read by common/build.sh) sets these in the environment:
  UEFI_TARGET   Mu-Silicium device name (default redwood)    UEFI_FD, DTB   files to use instead
  FD_BASE, FD_SIZE, PAYLOAD_BASE                              addresses from its UEFI port's memory map
"""
import argparse, gzip, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MU = os.path.join(HERE, 'Mu-Silicium')
PREBUILT = os.path.join(HERE, 'prebuilt', 'uefi')
def env_int(name, default):
    v = os.environ.get(name, '')
    return int(v, 0) if v else default
TARGET = os.environ.get('UEFI_TARGET') or 'redwood'
FD_BASE, FD_SIZE = env_int('FD_BASE', 0x9FD00000), env_int('FD_SIZE', 0x200000)
PAYLOAD_BASE, PAYLOAD_MAX = env_int('PAYLOAD_BASE', 0xF0000000), 0x04000000
# Android boot header version. 1 = upstream behaviour (redwood: gzip(kernel)+DTB in the
# kernel segment). 2 = raphael's law: its ABL refuses v1 with a black screen (verified t8/t13/t19),
# accepts v2 with an independent DTB segment at BOOTIMG_DTB_ADDR, file truncated exactly at
# the DTB's end - the chain proven byte-identical against the golden raphael boot image.
HDR = env_int('BOOTIMG_HEADER_VERSION', 1)
DTB_ADDR = env_int('BOOTIMG_DTB_ADDR', 0x1F00000)

ap = argparse.ArgumentParser()
ap.add_argument('-p', '--payload')
ap.add_argument('-o', '--output', default=os.path.join(HERE, 'xnu-redwood.img'))
ap.add_argument('--mode', default='RELEASE')
args = ap.parse_args()

fd_path = os.environ.get('UEFI_FD') or os.path.join(MU, f'Build/{TARGET}Pkg/{args.mode}_CLANGPDB/FV/SILICIUM_UEFI.fd')
if os.path.isfile(fd_path) and os.path.isdir(os.path.join(MU, 'BootShim')):
    shim_dir = os.path.join(MU, 'BootShim')
    for f in ('BootShim.elf', 'BootShim.bin'):
        try: os.unlink(os.path.join(shim_dir, f))
        except FileNotFoundError: pass
    subprocess.check_call(['make', 'REQUIRES_KERNEL_HEADER=1', f'FD_BASE={FD_BASE:#x}', f'FD_SIZE={FD_SIZE:#x}',
                           f'CFLAGS=--target=aarch64-linux-gnu -DPAYLOAD_BASE={PAYLOAD_BASE:#x}'],
                          cwd=shim_dir, stdout=subprocess.DEVNULL)
    parts = {'shim': os.path.join(shim_dir, 'BootShim.bin'), 'fd': fd_path,
             'dtb': os.path.join(MU, f'Resources/DTBs/{TARGET}.dtb'), 'ramdisk': os.path.join(MU, 'Resources/ramdisk'),
             'mkbootimg': os.path.join(MU, 'Resources/Scripts/mkbootimg.py')}
else:
    parts = {'shim': os.path.join(PREBUILT, 'BootShim.bin'), 'fd': os.path.join(PREBUILT, 'SILICIUM_UEFI.fd'),
             'dtb': os.path.join(PREBUILT, 'redwood.dtb'), 'ramdisk': os.path.join(PREBUILT, 'ramdisk'),
             'mkbootimg': os.path.join(PREBUILT, 'mkbootimg.py')}
    if os.environ.get('UEFI_FD'): parts['fd'] = os.environ['UEFI_FD']
shim = open(parts['shim'], 'rb').read()
fd = open(parts['fd'], 'rb').read()
assert len(fd) == FD_SIZE, hex(len(fd))

blob = shim + fd
if args.payload:
    data = open(args.payload, 'rb').read()
    # the payload file is a complete "XNUP" container (see xnuloader/mkpayload.py)
    assert data[:4] == b'XNUP' and len(data) <= PAYLOAD_MAX - 0x1000, 'bad or oversized payload'
    blob += data
    blob += b'\0' * (-len(blob) % 16)
    print(f'payload: {len(data)} bytes, bytesum={sum(data) & 0xffffffff:08x}')

if os.environ.get('DTB'): parts['dtb'] = os.environ['DTB']
dtb = open(parts['dtb'], 'rb').read()
kernel = args.output + '.kernel~'
if HDR == 2:
    # ---- raphael (v2): kernel segment = gzip(blob) only; DTB is its own segment; the file
    # stops at the DTB's end. The exact mkbootimg argument set is the proven one (golden-image
    # byte-identical round trip), not tunable guesses: base 0, pagesize 4096, console=null.
    open(kernel, 'wb').write(gzip.compress(blob, 6))
    try:
        subprocess.check_call([sys.executable, parts['mkbootimg'], '--kernel', kernel, '--ramdisk', parts['ramdisk'],
                               '--dtb', parts['dtb'], '--dtb_offset', hex(DTB_ADDR),
                               '--header_version', '2', '--base', '0', '--pagesize', '4096',
                               '--cmdline', 'console=null', '--os_version', '11.0.0',
                               '--os_patch_level', '2021-10', '-o', args.output])
    finally:
        os.unlink(kernel)
    img = open(args.output, 'rb').read()
    import struct as _s
    _h = _s.unpack_from('<10I', img, 8)          # v2 layout: kernel_size, kernel_addr, rd_size, rd_addr, ...
    k_size, r_size = _h[0], _h[2]
    page = _s.unpack_from('<I', img, 36)[0]
    dtb_size = _s.unpack_from('<I', img, 1648)[0]
    k_off = page
    r_off = (k_off + k_size + page - 1) // page * page
    d_off = (r_off + r_size + page - 1) // page * page
    end = d_off + dtb_size
    assert img[:8] == b'ANDROID!' and _s.unpack_from('<Q', img, 1652)[0] == DTB_ADDR
    # mkbootimg pads the final page; the raphael law is the file stops exactly at the DTB's end:
    if len(img) > end:
        open(args.output, 'wb').write(img[:end])
        img = img[:end]
    assert len(img) == end, 'v2 truncation law'
    print(f'{args.output}: {len(img)} bytes (shim {len(shim)}, uncompressed {len(blob)}, v2 hdr, DTB @{DTB_ADDR:#x} {dtb_size}B)')
else:
    open(kernel, 'wb').write(gzip.compress(blob, 6) + dtb)
    try:
        subprocess.check_call([sys.executable, parts['mkbootimg'], '--kernel', kernel, '--ramdisk', parts['ramdisk'],
                               '--header_version', '1', '-o', args.output])
    finally:
        os.unlink(kernel)
    print(f'{args.output}: {os.path.getsize(args.output)} bytes (shim {len(shim)}, uncompressed {len(blob)}, v1 hdr)')
