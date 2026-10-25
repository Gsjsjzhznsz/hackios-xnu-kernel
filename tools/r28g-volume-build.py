#!/usr/bin/env python3
"""tools/r28g-volume-build.py -- the R28g md1 volume: the R28d one, plus fw/adsp.mbn.

R28g is the T2 control round: the driver's step 19, asked for with
adreno_test_adsp=1, hands TrustZone the ADSP's own .mdt (the audio DSP's
metadata image, pas id 1 -- the one image Android's own PIL starts through
this very channel at every boot) instead of the zap's, over the same
preemptible two-parameter call, so the r28d refusal's last untested variable
-- the file -- is the only thing that moves.

The file has to travel in fw/ of the small image (md1), which the rounds
cannot rebuild the upstream way (userland/build.sh needs the whole iOS
userland tree): since R13 the volume's bytes are carried release to release,
and R24 changed them by same-length surgery inside the zap's extent.  This
round ADDS a file, so it does it the honest way instead: read the volume with
the upstream reader (tools/apfs/apfs.py -- verified against 7-Zip over the
whole iOS root volume upstream), lay it out as a directory tree byte for
byte, put fw/adsp.mbn (this tree's fw-gpu-raphael/adsp.mbn, the phone's own
NON-HLOS ADSP.MDT) beside the three files that are already there, and make
the volume again with the upstream builder (tools/apfs/mkapfs.py, the same
-z --plain fw the R13 volume was made with, so the four fw files ride as
single in-place extents exactly as they always have).  Deterministic: the
same inputs give the same image byte for byte.

Every claim is then checked by a reader that did not make the image (the
upstream apfs.py again, plus the kernel's own conventions through
tools/r24b_volume_audit.py at the caller's side):

  build   <old-volume> <adsp.mbn> <out-volume> [--apfs-tools DIR]
  verify  <old-volume> <new-volume> <adsp.mbn> [--apfs-tools DIR]

verify proves, and build runs on its own output before answering:
  1. every entry the old volume carries is byte-identical in the new one
     (the zap keeps its R24 production-chain swap, the GMU and the SQE keep
     their bytes, the plists and the binaries keep theirs);
  2. the one new entry is fw/adsp.mbn, exactly the source file's bytes;
  3. every fw/ file sits in ONE piece (a single extent, in place, as --plain
     fw lays them out and redwood_image.c reads them);
  4. the volume carries the Xiaomi chain and no test chain (R24's law, which
     the adsp's own chain satisfies too).
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ADSP_SHA = 'f5881fc760e863a8622bc32ab596bdd3423065a7d35ed4a9fc58854d337b2fa1'
ADSP_LEN = 7820
FW_NAMES = ('a640_zap.mbn', 'a640_gmu.bin', 'a630_sqe.fw', 'adsp.mbn')
DEFAULT_TOOLS = os.environ.get('APFS_TOOLS') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    '..', 'hackios-xnu-userland', 'tools', 'apfs')


def sha(b):
    return hashlib.sha256(b).hexdigest()


def load_tools(tools):
    tools = os.path.abspath(tools)
    for f in ('apfs.py', 'mkapfs.py'):
        if not os.path.exists(os.path.join(tools, f)):
            raise SystemExit(f'r28g-volume-build: {f} is not in {tools} '
                             f'(clone oosexgang/hackios-xnu-userland, or set APFS_TOOLS)')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import apfs                     # noqa: E402  (after sys.path)
    return tools, apfs


def read_all(apfs, path):
    """path -> (bytes, mode, d_type, link-target) for every entry, as the
    upstream reader sees it (decmpfs decompressed, stream sizes not extent
    sizes).  Directories carry b'' and their mode."""
    c = apfs.Container(path)
    v = apfs.pick_volume(c, None)
    out = {}
    for p, name, fid, dt in apfs.walk(v, apfs.ROOT_DIR_INO_NUM):
        ino = v.inode(fid)
        if ino is None:
            raise SystemExit(f'{path}: no inode for {p}')
        if dt == 4:
            out[p] = (b'', ino.mode, 4, None)
        elif ino.is_link:
            out[p] = (ino.symlink_target_bytes() or b'', ino.mode, 10, ino.symlink_target_bytes())
        elif ino.is_reg:
            out[p] = (ino.contents(), ino.mode, 8, None)
        else:
            raise SystemExit(f'{path}: {p} is neither directory, file nor link')
    c.close()
    return out


def fw_extent(apfs, path, name):
    """The one fact the driver cares about: fw/NAME is one piece where it
    lies.  Returns the extent list, or None if there is no such file."""
    c = apfs.Container(path)
    v = apfs.pick_volume(c, None)
    hit = v.lookup(f'/fw/{name}')
    if hit is None:
        c.close()
        return None
    ino = v.inode(hit[0])
    ex = v.extents(ino.private_id) if ino._dstream else []
    c.close()
    return ex


def materialize(tree, root):
    """Write the tree out as a directory (modes included), and return the
    manifest text (mode, uid, gid, path[, target]) so mkapfs.py takes owner
    and mode from it for every entry."""
    manifest = []
    for p in sorted(tree):                  # sorted: every parent before its children
        rel = p.lstrip('/')
        dst = os.path.join(root, rel)
        data, mode, dt, target = tree[p]
        if dt == 4:
            os.makedirs(dst, exist_ok=True)
            os.chmod(dst, mode & 0o7777)
        elif dt == 10:
            os.symlink(target.decode('utf-8'), dst)
        else:
            with open(dst, 'wb') as f:
                f.write(data)
            os.chmod(dst, mode & 0o7777)
        line = '%o\t0\t0\t%s' % (mode, rel)
        if dt == 10:
            line += '\t' + target.decode('utf-8')
        manifest.append(line)
    return '\n'.join(manifest) + '\n'


def do_build(old, adsp_path, out, tools):
    tools, apfs = load_tools(tools)
    src = open(adsp_path, 'rb').read()
    if len(src) != ADSP_LEN or sha(src) != ADSP_SHA:
        raise SystemExit(f'{adsp_path}: not this phone\'s ADSP.MDT ({len(src)} bytes, '
                          f'sha {sha(src)[:16]}...; want {ADSP_LEN}, {ADSP_SHA})')
    old_tree = read_all(apfs, old)
    if '/fw/adsp.mbn' in old_tree:
        raise SystemExit(f'{old}: already carries fw/adsp.mbn')
    for name in FW_NAMES[:3]:
        if f'/fw/{name}' not in old_tree:
            raise SystemExit(f'{old}: no fw/{name} (not the md1 volume this round knows)')
    if b'Generated Test Attestation CA' in open(old, 'rb').read():
        raise SystemExit(f'{old}: still carries the TEST-SIGNED zap (the R24 swap is missing)')

    new_tree = dict(old_tree)
    new_tree['/fw/adsp.mbn'] = (src, 0o100644, 8, None)

    tmp = tempfile.mkdtemp(prefix='r28g-volume-')
    try:
        root = os.path.join(tmp, 'root')
        os.makedirs(root)
        manifest = os.path.join(tmp, 'manifest')
        with open(manifest, 'w') as f:
            f.write(materialize(new_tree, root))
        mk = os.path.join(tools, 'mkapfs.py')
        r = subprocess.run([sys.executable, mk, '-z', '--plain', 'fw', '-o', os.path.abspath(out),
                            'redwood=' + root + ',manifest=' + manifest],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(r.stdout.decode('utf-8', 'replace').rstrip())
        if r.returncode != 0:
            raise SystemExit(f'r28g-volume-build: mkapfs.py failed ({r.returncode})')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f'r28g-volume-build: {out}: {os.path.getsize(out):,} B, sha256 {sha(open(out, "rb").read())}')
    do_verify(old, out, adsp_path, tools, quiet_intro=True)


def do_verify(old, new, adsp_path, tools, quiet_intro=False):
    tools, apfs = load_tools(tools)
    if not quiet_intro:
        print(f'r28g-volume-build: verify {new} against {old}')
    src = open(adsp_path, 'rb').read()
    old_tree = read_all(apfs, old)
    new_tree = read_all(apfs, new)

    # 1) every old entry byte-identical in the new one
    changed = [p for p in old_tree if old_tree[p] != new_tree.get(p)]
    if changed:
        raise SystemExit(f'{new}: these entries differ from {old}: {changed}')
    print(f'  [ok] all {len(old_tree)} entries of the old volume are byte-identical in the new one')

    # 2) exactly one new entry: fw/adsp.mbn, exactly the source's bytes
    added = [p for p in new_tree if p not in old_tree]
    if added != ['/fw/adsp.mbn']:
        raise SystemExit(f'{new}: the new entries are {added} (want exactly ["/fw/adsp.mbn"])')
    if new_tree['/fw/adsp.mbn'][0] != src:
        raise SystemExit(f'{new}: fw/adsp.mbn is not the source file\'s bytes')
    print(f'  [ok] the one new entry is /fw/adsp.mbn, {len(src)} bytes, sha256 {sha(src)[:16]}... '
          f'(the source file\'s own)')

    # 3) every fw file in one piece, in place
    blob = open(new, 'rb').read()
    for name in FW_NAMES:
        ex = fw_extent(apfs, new, name)
        if not ex or len(ex) != 1 or ex[0][0] != 0:
            raise SystemExit(f'{new}: fw/{name} is not a single in-place extent ({ex})')
        logical, length, phys = ex[0]
        size = len(new_tree[f'/fw/{name}'][0])
        if blob[phys * 4096:phys * 4096 + size] != new_tree[f'/fw/{name}'][0]:
            raise SystemExit(f'{new}: fw/{name} does not read back in place')
        print(f'  [ok] fw/{name}: one extent, {size:,} B at block {phys} '
              f'(in place, as --plain fw lays it and redwood_image.c reads it)')

    # 4) the chains, R24's law
    if b'Xiaomi Attestation CA' not in blob:
        raise SystemExit(f'{new}: does not carry the Xiaomi chain')
    if b'Generated Test Attestation CA' in blob:
        raise SystemExit(f'{new}: still carries the TEST-SIGNED zap')
    print('  [ok] the Xiaomi chain is in, the test chain is out (the adsp\'s chain is Xiaomi\'s too)')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('mode', choices=('build', 'verify'))
    ap.add_argument('old')
    ap.add_argument('second')
    ap.add_argument('third', nargs='?')
    ap.add_argument('--apfs-tools', default=DEFAULT_TOOLS)
    a = ap.parse_args()
    if a.mode == 'build':
        do_build(a.old, a.second, a.third, a.apfs_tools)
    else:
        do_verify(a.old, a.second, a.third, a.apfs_tools)


if __name__ == '__main__':
    main()
