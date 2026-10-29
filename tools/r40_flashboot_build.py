#!/usr/bin/env python3
"""tools/r40_flashboot_build.py -- build the R40 flashboot: the R39 bytes plus fw/.

R39's own release note promised this round: "固件入卷后无需再改 cmdline" -- the WiFi
chain (wifi=1 wifi_bdf=bd_f11cn.bin) waits for its inputs in fw/ of the RAM disks, and
the kernel reads them from md1 first, then md0 (redwood_image.c).  The md1 cannot take
them (the loader's payload window is 64 MB; the modem image alone is 81.6 MB), but the
RESIDENT image -- the staged flashboot, md0 -- can: "oem ramdisk" + "stage" gives it a
4 GB budget (loader.c max-download-size), and choose_window lays it out with room to
spare.  So the firmware travels in the flashboot, the one asset this project had never
had a reason to touch since R13.

The build is deliberately USER-SIDE: the firmware and the NV snapshots are device-
private extractions (docs/RAPHAEL-WIFI.md: "禁止提交、发布这些文件"), so they never
enter the repository or a release.  The user runs this script next to their own
fw-raphael/ (upstream README 3.2-3.4 layout) against the round's flashboot asset.

Two of the inputs are not extractions at all and are generated here, byte for byte as
the reference's validated flow specifies (README 3.4):

  firmware-5.bin  the ath10k interface descriptor: the "QCA-ATH10K" header, then
                  entries (1, 0x5bbee4a4), (2, 40 00 0c), (5, 4), (6, 3) -- the real
                  WLAN program is wlanmdsp.mbn, loaded through MPSS;
  0-rmtfs-ram     0x310000 zero bytes, the scratch the RMTFS server hands the modem.

The four NV snapshots (nv-modemst1, nv-modemst2, nv-fsg, nv-fsc) are OPTIONAL here:
without them the chain walks its steps and stops at RMTFS with a named line (the L2
tree pre-registers it); with them it may go all the way.  Everything present is
verified by size law (8 MiB per partition, 256 KiB for fsc) before it is stored.

What the script does, in order:
  1. extract the R39 flashboot's tree (tools/apfs/apfs.py, read-only; /sbin/launchd
     is the R13-lineage device-validated piece and must ride byte for byte)
  2. collect fw/: every file the directory provides, plus the two generated ones,
     each checked against its own size law (modem.mdt <= one 16 KiB page and ELF32;
     bd_*.bin <= 128 KiB; 0-rmtfs-ram == 0x310000; nv-* == 8 MiB / 256 KiB)
  3. rebuild with tools/apfs/mkapfs.py (-z --plain fw: the firmware stays as it is,
     in one piece, exactly where redwood_image.c expects to find it) and prove the
     round trip: every file re-reads byte for byte
  4. emit the manifest (every path: mode, size, sha256 -- the content law of this
     container, since container bytes are host-dependent and pin nothing)

usage:
  python3 tools/r40_flashboot_build.py --base raphael-r39-flashboot.apfs \
      --fw fw-raphael/ --apfs-dir /path/to/userland/tools/apfs \
      --out raphael-r40-flashboot.apfs --manifest r40-flashboot-manifest.json

outputs: the new container, its manifest, and a verdict line per check.
"""
import argparse
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys

# the R39 flashboot's own identity (the r39 release's SHA256SUMS)
R39_FLASHBOOT = '14485e7c5ce125ef516168445c2500ac04ca5e40a908c2aef516b2d92c24ba0c'

# size law (upstream README 3.3: "前三个完整分区为 8 MiB，fsc 为 256 KiB")
NV_SIZES = {'nv-modemst1': 8388608, 'nv-modemst2': 8388608, 'nv-fsg': 8388608, 'nv-fsc': 262144}
RMTFS_RAM_SIZE = 0x310000


def sha(data):
    return hashlib_sha256(data)


def hashlib_sha256(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def load_apfs(apfs_dir):
    spec = importlib.util.spec_from_file_location('r40_apfs', os.path.join(apfs_dir, 'apfs.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def extract_volume(apfs, container, outdir):
    """Materialize the first volume's tree on disk; return {relpath: record}."""
    c = apfs.Container(container)
    vols = c.volumes()
    assert vols, 'no volume in ' + container
    vol = vols[0]
    manifest = {}

    def walk(oid, rel):
        ino = vol.inode(oid)
        for name, fid, _dt in vol.dir_entries(oid):
            child = vol.inode(fid)
            path = os.path.join(rel, name) if rel else name
            if child.is_dir:
                host = os.path.join(outdir, path)
                os.makedirs(host, exist_ok=True)
                os.chmod(host, child.mode & 0o7777)
                manifest[path] = {'kind': 'dir', 'mode': oct(child.mode & 0o7777)}
                walk(fid, path)
            elif child.is_link:
                target = child.symlink_target_bytes()
                host = os.path.join(outdir, path)
                if os.path.lexists(host):
                    os.unlink(host)
                os.symlink(os.fsdecode(target), host)
                manifest[path] = {'kind': 'link', 'target': target.decode('utf-8', 'surrogateescape')}
            elif child.is_reg:
                data = child.contents()
                host = os.path.join(outdir, path)
                os.makedirs(os.path.dirname(host), exist_ok=True)
                with open(host, 'wb') as f:
                    f.write(data)
                os.chmod(host, child.mode & 0o7777)
                manifest[path] = {'kind': 'file', 'mode': oct(child.mode & 0o7777),
                                  'size': len(data), 'sha256': sha(data)}

    walk(apfs.ROOT_DIR_INO_NUM, '')
    return manifest


def build_container(mkapfs_py, tree, out, slack):
    subprocess.check_call([sys.executable, mkapfs_py, '-z', '--plain', 'fw', '--time', '0',
                           '--slack', str(slack), '-o', out, 'Boot=' + tree])


def make_firmware5():
    """The ath10k interface descriptor, byte for byte as the reference's README 3.4."""
    def entry(kind, data):
        return struct.pack('<II', kind, len(data)) + data + bytes(-len(data) % 4)

    header = b'QCA-ATH10K\0'.ljust(12, b'\0')
    metadata = header + entry(1, struct.pack('<I', 0x5bbee4a4))
    metadata += entry(2, bytes.fromhex('40 00 0c'))
    metadata += entry(5, struct.pack('<I', 4))
    metadata += entry(6, struct.pack('<I', 3))
    return metadata


def collect_fw(fw_dir, work_fw):
    """Gather the firmware set; generate the two helpers; enforce the size laws."""
    os.makedirs(work_fw, exist_ok=True)
    names = sorted(os.listdir(fw_dir)) if os.path.isdir(fw_dir) else []
    if not names:
        sys.exit('FAIL: ' + fw_dir + ' is empty or missing')
    stored = []
    for name in names:
        src = os.path.join(fw_dir, name)
        if not os.path.isfile(src):
            continue
        data = open(src, 'rb').read()
        if name == 'modem.mdt':
            assert len(data) <= 16384, 'modem.mdt larger than one page: %d' % len(data)
            assert data[:5] == b'\x7fELF\x01', 'modem.mdt is not an ELF32 header'
        elif name.startswith('bd_') and name.endswith('.bin'):
            assert len(data) <= 128 * 1024, name + ' over the 128 KiB board-data law'
        elif name in NV_SIZES:
            assert len(data) == NV_SIZES[name], '%s is %d B, the law says %d' % (name, len(data), NV_SIZES[name])
        elif name == 'firmware-5.bin' or name == '0-rmtfs-ram':
            continue            # generated below, never taken from the directory
        elif name.startswith('modem.b') or name == 'wlanmdsp.mbn':
            assert len(data) > 0, name + ' is empty'
        else:
            print('  (skipped, not a wifi input: %s)' % name)
            continue
        with open(os.path.join(work_fw, name), 'wb') as f:
            f.write(data)
        stored.append((name, len(data)))

    # the two generated helpers (README 3.4)
    with open(os.path.join(work_fw, 'firmware-5.bin'), 'wb') as f:
        f.write(make_firmware5())
    stored.append(('firmware-5.bin', len(make_firmware5())))
    with open(os.path.join(work_fw, '0-rmtfs-ram'), 'wb') as f:
        f.write(bytes(RMTFS_RAM_SIZE))
    stored.append(('0-rmtfs-ram', RMTFS_RAM_SIZE))

    # the round's law: what the wifi chain's first gate asks for
    have = set(n for n, _ in stored)
    for must in ('modem.mdt', 'wlanmdsp.mbn'):
        assert must in have, 'the wifi gate needs ' + must
    bdf = [n for n in have if n.startswith('bd_')]
    assert bdf, 'the BDF (wifi_bdf=) is missing: bd_f11cn.bin expected'
    missing_nv = [n for n in NV_SIZES if n not in have]
    if missing_nv:
        print('  NOTE: no NV snapshots here (%s) -- the chain will walk to RMTFS and stop' % ', '.join(missing_nv))
        print('        with a named line.  Root backup per README 3.3 closes it next round.')
    total = sum(s for _, s in stored)
    print('fw/: %d files, %d B (%.1f MB)' % (len(stored), total, total / 1048576))
    return stored, missing_nv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='the R39 flashboot container (raphael-r39-flashboot.apfs)')
    ap.add_argument('--fw', required=True, help='the device-private firmware directory (fw-raphael/)')
    ap.add_argument('--apfs-dir', required=True, help='tools/apfs (apfs.py + mkapfs.py)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--work', default=None)
    ap.add_argument('--slack', type=int, default=512, help='free blocks to leave (default 512 = 2 MB)')
    args = ap.parse_args()

    base_sha = hashlib_sha256(open(args.base, 'rb').read())
    print('base: %s sha256 %s' % (args.base, base_sha))
    if base_sha != R39_FLASHBOOT:
        print('  NOTE: not the r39 release byte -- continuing (the tree law below still applies)')

    work = args.work or '.r40-flashboot-work'
    shutil.rmtree(work, ignore_errors=True)
    tree = os.path.join(work, 'tree')
    os.makedirs(tree)

    apfs = load_apfs(args.apfs_dir)
    base_manifest = extract_volume(apfs, args.base, tree)
    base_files = [p for p, r in base_manifest.items() if r['kind'] == 'file']
    print('base tree: %d entries, %d files' % (len(base_manifest), len(base_files)))
    assert 'sbin/launchd' in base_manifest, 'the stage0 launchd is missing from the base'
    assert base_manifest['sbin/launchd']['kind'] == 'file', 'sbin/launchd is not a file'
    for must in ('Cryptexes', 'dev', 'flash', 'redwood', 'sbin'):
        assert base_manifest.get(must, {}).get('kind') == 'dir', 'base structure: ' + must
    assert 'fw' not in base_manifest, 'the base already carries fw/ -- nothing to add?'
    launchd_sha = base_manifest['sbin/launchd']['sha256']

    stored, missing_nv = collect_fw(args.fw, os.path.join(tree, 'fw'))

    out = args.out
    build_container(os.path.join(args.apfs_dir, 'mkapfs.py'), tree, out, args.slack)

    # the round trip: re-extract what was built; every file must read back byte for byte
    tree2 = os.path.join(work, 'tree2')
    os.makedirs(tree2)
    new_manifest = extract_volume(apfs, out, tree2)
    problems = []
    for p, r in base_manifest.items():
        if new_manifest.get(p) != r:
            problems.append('BASE CHANGED: ' + p)
    for name, size in stored:
        path = 'fw/' + name
        rec = new_manifest.get(path)
        if rec is None or rec.get('kind') != 'file':
            problems.append('MISSING: ' + path)
        elif rec['size'] != size:
            problems.append('SIZE: ' + path)
        else:
            with open(os.path.join(tree2, path), 'rb') as f:
                if hashlib_sha256(f.read()) != rec['sha256']:
                    problems.append('SHA: ' + path)
    assert not problems, 'round trip failed:\n  ' + '\n  '.join(problems[:10])

    out_size = os.path.getsize(out)
    print('flashboot: %s (%d B, %.1f MB), sha256 %s' % (
        out, out_size, out_size / 1048576, hashlib_sha256(open(out, 'rb').read())))
    print('round trip: every file reads back byte for byte (%d fw + %d base)' % (len(stored), len(base_files)))
    assert launchd_sha == new_manifest['sbin/launchd']['sha256'], 'launchd moved'

    with open(args.manifest, 'w') as f:
        json.dump({'base': {'path': args.base, 'sha256': base_sha},
                   'flashboot': {'path': out, 'sha256': hashlib_sha256(open(out, 'rb').read()),
                                 'size': out_size},
                   'fw': {('fw/' + n): {'size': s} for n, s in stored},
                   'nv_missing': missing_nv,
                   'note': 'content law: container bytes are host-dependent, pin the per-file shas'},
                  f, indent=1)
    print('manifest: ' + args.manifest)
    print('OK: the R40 flashboot is built; stage it exactly as the r39 one (oem ramdisk -> stage -> oem run)')


if __name__ == '__main__':
    main()
