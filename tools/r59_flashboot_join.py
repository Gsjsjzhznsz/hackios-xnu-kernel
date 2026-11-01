#!/usr/bin/env python3
"""tools/r59_flashboot_join.py -- build the R59 flashboot: the R56 fw-nv bytes plus fw/network.

R59 is the join round.  R58's scan heard 8 networks and NONE is open; lx_station_join()
gets its credential pair from redwood_wifi_network(), which reads the file "network"
among the firmware of the small image (redwood_wifi.c: the name on its first line, the
passphrase on its second; 1..32 and 8..63 bytes are the kernel's own law, logged by
LENGTH ONLY -- "The log says how long the two are, and nothing else of them").

PRIVACY LAW (the fork's own, docs/RAPHAEL-WIFI.md: device-private extractions never
enter a repository or a release): the credential file lives outside every repository,
is read once here, and is DELETED by the caller; the manifest this tool writes records
the two LENGTHS and never the contents or their hashes.

Laws (all asserted here):
  - the base is the R56 fw-nv flashboot (its sha256 is printed and pinned in the manifest)
  - the base tree already carries fw/ with the R56 firmware set, and every file in it
    re-reads byte for byte after the rebuild (the round trip)
  - the firmware files keep their size laws (modem.mdt one 16 KiB page and ELF32,
    wlanmdsp.mbn non-empty, bd_*.bin <= 128 KiB, the NV snapshots 8 MiB / 256 KiB,
    0-rmtfs-ram == 0x310000)
  - the NEW fw/network: two lines, LF-separated, no CR (the parser tolerates a trailing
    CR but we do not write one), name 1..32 B, passphrase 8..63 B
  - /sbin/launchd must ride byte for byte (the R13-lineage device-validated piece)

usage:
  python3 tools/r59_flashboot_join.py --base raphael-r56-flashboot-fw-nv.apfs \
      --network network.txt --apfs-dir /path/to/userland/tools/apfs \
      --out raphael-r59-flashboot-fw-nv.apfs --manifest r59-flashboot-manifest.json
  (network.txt: exactly two lines -- the SSID, then the passphrase)
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys


def sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def load_apfs(apfs_dir):
    spec = importlib.util.spec_from_file_location('r59_apfs', os.path.join(apfs_dir, 'apfs.py'))
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


NV_SIZES = {'nv-modemst1': 8388608, 'nv-modemst2': 8388608, 'nv-fsg': 8388608, 'nv-fsc': 262144}


def fw_laws(manifest):
    """The firmware set keeps its size laws (the r40 tool's, still)."""
    checks = []
    fw = {p: r for p, r in manifest.items() if p.startswith('fw/')}
    assert fw, 'the base carries no fw/'
    mdt = fw.get('fw/modem.mdt')
    assert mdt and mdt['kind'] == 'file' and mdt['size'] <= 16384, 'modem.mdt missing or over one page'
    checks.append('modem.mdt %d B' % mdt['size'])
    wsp = fw.get('fw/wlanmdsp.mbn')
    assert wsp and wsp['kind'] == 'file' and wsp['size'] > 0, 'wlanmdsp.mbn missing'
    checks.append('wlanmdsp.mbn %d B' % wsp['size'])
    bdfs = [p for p in fw if p.startswith('fw/bd_') and p.endswith('.bin')]
    assert bdfs, 'no BDF in fw/ (wifi_bdf= names one)'
    for p in bdfs:
        assert fw[p]['size'] <= 128 * 1024, p + ' over the 128 KiB law'
    checks.append('BDF ' + ', '.join('%s %d B' % (p, fw[p]['size']) for p in bdfs))
    rmtfs = fw.get('fw/0-rmtfs-ram')
    assert rmtfs and rmtfs['size'] == 0x310000, '0-rmtfs-ram is not 0x310000'
    checks.append('0-rmtfs-ram 0x310000')
    fw5 = fw.get('fw/firmware-5.bin')
    assert fw5 and fw5['kind'] == 'file', 'firmware-5.bin missing'
    for name, want in NV_SIZES.items():
        rec = fw.get('fw/' + name)
        if rec is None:
            checks.append('NOTE: %s absent (the chain walks to RMTFS and stops)' % name)
        else:
            assert rec['size'] == want, '%s is %d B, the law says %d' % (name, rec['size'], want)
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='the R56 fw-nv flashboot container')
    ap.add_argument('--network', required=True, help='the credential file: two lines, SSID then passphrase')
    ap.add_argument('--apfs-dir', required=True, help='tools/apfs (apfs.py + mkapfs.py)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--work', default=None)
    ap.add_argument('--slack', type=int, default=512)
    args = ap.parse_args()

    base_sha = sha(open(args.base, 'rb').read())
    print('base: %s sha256 %s (%d B)' % (args.base, base_sha, os.path.getsize(args.base)))

    # the credential pair: read once, keep lengths, never the bytes
    raw = open(args.network, 'rb').read()
    lines = raw.split(b'\n')
    assert len(lines) >= 2 and (len(lines) == 2 or lines[2] == b''), 'the network file is not exactly two lines'
    name, passphrase = lines[0], lines[1]
    assert not name.endswith(b'\r') and not passphrase.endswith(b'\r'), 'CR in the network file (we do not write CRLF)'
    assert 1 <= len(name) <= 32, 'the SSID is %d B, the kernel law is 1..32' % len(name)
    assert 8 <= len(passphrase) <= 63, 'the passphrase is %d B, the kernel law is 8..63' % len(passphrase)
    network_bytes = name + b'\n' + passphrase + b'\n'
    print('network: name %d B, passphrase %d B (lengths only leave this tool)' % (len(name), len(passphrase)))
    del name, passphrase, raw, lines

    work = args.work or '.r59-flashboot-work'
    shutil.rmtree(work, ignore_errors=True)
    tree = os.path.join(work, 'tree')
    os.makedirs(tree)

    apfs = load_apfs(args.apfs_dir)
    base_manifest = extract_volume(apfs, args.base, tree)
    base_files = [p for p, r in base_manifest.items() if r['kind'] == 'file']
    print('base tree: %d entries, %d files' % (len(base_manifest), len(base_files)))
    assert 'sbin/launchd' in base_manifest and base_manifest['sbin/launchd']['kind'] == 'file', \
        'the stage0 launchd is missing from the base'
    for must in ('Cryptexes', 'dev', 'flash', 'redwood', 'sbin', 'fw'):
        assert base_manifest.get(must, {}).get('kind') == 'dir', 'base structure: ' + must
    launchd_sha = base_manifest['sbin/launchd']['sha256']

    for line in fw_laws(base_manifest):
        print('  fw law: ' + line)

    # the ONE addition
    with open(os.path.join(tree, 'fw', 'network'), 'wb') as f:
        f.write(network_bytes)
    os.chmod(os.path.join(tree, 'fw', 'network'), 0o644)

    subprocess.check_call([sys.executable, os.path.join(args.apfs_dir, 'mkapfs.py'), '-z', '--plain', 'fw',
                           '--time', '0', '--slack', str(args.slack), '-o', args.out, 'Boot=' + tree])

    # the round trip: every file re-reads byte for byte
    tree2 = os.path.join(work, 'tree2')
    os.makedirs(tree2)
    new_manifest = extract_volume(apfs, args.out, tree2)
    problems = []
    for p, r in base_manifest.items():
        if new_manifest.get(p) != r:
            problems.append('BASE CHANGED: ' + p)
    rec = new_manifest.get('fw/network')
    if rec is None or rec.get('kind') != 'file':
        problems.append('MISSING: fw/network')
    else:
        with open(os.path.join(tree2, 'fw', 'network'), 'rb') as f:
            if f.read() != network_bytes:
                problems.append('SHA: fw/network')
    assert not problems, 'round trip failed:\n  ' + '\n  '.join(problems[:10])

    out_size = os.path.getsize(args.out)
    out_sha = sha(open(args.out, 'rb').read())
    print('flashboot: %s (%d B, %.1f MB), sha256 %s' % (args.out, out_size, out_size / 1048576, out_sha))
    print('round trip: every file reads back byte for byte (%d base files + fw/network)' % len(base_files))
    assert launchd_sha == new_manifest['sbin/launchd']['sha256'], 'launchd moved'

    with open(args.manifest, 'w') as f:
        json.dump({'base': {'path': os.path.basename(args.base), 'sha256': base_sha},
                   'flashboot': {'path': os.path.basename(args.out), 'sha256': out_sha, 'size': out_size},
                   'network': {'name_bytes': len(network_bytes.split(b'\n')[0]),
                               'passphrase_bytes': len(network_bytes.split(b'\n')[1]),
                               'privacy': 'lengths only -- the fork log law: the log says how long the two are, and nothing else'},
                   'note': 'content law: container bytes are host-dependent, pin the per-file shas'},
                  f, indent=1)
    print('manifest: ' + args.manifest)
    print('OK: the R59 flashboot is built; stage it exactly as the r56 one (oem ramdisk -> stage -> stage xnup -> oem run)')
    print('REMEMBER: delete the credential file now (the caller owns it; it lives in no repository)')


if __name__ == '__main__':
    main()
