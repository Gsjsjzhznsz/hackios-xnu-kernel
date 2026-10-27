#!/usr/bin/env python3
"""tools/r34_volume_build.py -- build the R34 GL-test md1 volume from the R13-lineage bytes.

R34 is the GL-test round: the kernel is R33's own bytes (verbatim, no new code), and the
ONLY thing that moves is the md1 volume, which gains what userland/build.sh GL=test puts
in root/lib and root/bin -- and nothing else:

    /lib/libOSMesa.8.dylib    Mesa 25.0.7, one library: the gl* entry points, the shading
                              language compiler, the softpipe and freedreno gallium drivers
                              (freedreno drives the A640 through the kernel's RedwoodGPU
                              service, via tools/mesa/redwood-drm standing in for libdrm)
    /bin/gl_test              one triangle drawn through that library, read back, put on
                              the panel; its verdict lines are the round's device record
    /bin/u2f_test             what the GPU makes of an unsigned integer turned into a float
    /etc/rc.jobs              REPLACED by rc.jobs.gltest (rwtouch wait 14; gl_test 8) --
                              the demo job's command list; every other file keeps its
                              R13-lineage bytes, extracted and rebuilt byte for byte

Why extraction instead of running userland/build.sh: that script's job list is made by
mkjobs.py --root-volume against ios165/rootfs.slt (the listing of THIS phone's iOS
volume), which exists on the maintainer's machine and nowhere else.  The R13-lineage
volume already carries that list, judged and baked.  So: read the container with
tools/apfs/apfs.py (read-only), materialize the tree, add the three files, swap the one,
rebuild with tools/apfs/mkapfs.py (the very builder that made the original), and prove
the round trip: every file re-reads from the new container byte for byte, and every
R13-lineage file is byte-identical to what the base carried.

usage:
  python3 tools/r34_volume_build.py --base base/r33-md1-root.apfs \
      --apfs-dir tools/apfs --rc-jobs userland/rc.jobs.gltest --gl-dir r34-gl \
      --out r34-md1-root.apfs --manifest r34-volume-manifest.json [--work DIR]

outputs: the new container, its manifest (every path: mode, size, sha256 -- the flash
build's law and a published release asset), and a verdict line per check.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import struct
import subprocess
import sys

# the three GL additions, and the one replacement (paths inside the volume)
GL_FILES = {
    'lib/libOSMesa.8.dylib': 'libOSMesa.8.dylib',
    'bin/gl_test': 'gl_test',
    'bin/u2f_test': 'u2f_test',
}
RC_JOBS_PATH = 'etc/rc.jobs'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_apfs(apfs_dir):
    spec = importlib.util.spec_from_file_location('r34_apfs', os.path.join(apfs_dir, 'apfs.py'))
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
            else:
                raise SystemExit('r34_volume_build: %s: neither file, dir nor link -- refusing' % path)
    walk(apfs.ROOT_DIR_INO_NUM, '')
    return manifest


def build_container(mkapfs_py, tree, out, slack):
    subprocess.check_call([sys.executable, mkapfs_py, '-z', '--plain', 'fw', '--time', '0',
                           '--slack', str(slack), '-o', out, 'redwood=' + tree])


def diff_manifests(base, new, added_expect, changed_expect):
    """Every base file byte-identical except the expected changes; nothing else moves."""
    problems = []
    base_files = {p: r for p, r in base.items() if r['kind'] == 'file'}
    new_files = {p: r for p, r in new.items() if r['kind'] == 'file'}
    for p, r in base_files.items():
        if p not in new_files:
            problems.append('MISSING: ' + p)
        elif p in changed_expect:
            continue
        elif new_files[p] != r:
            problems.append('CHANGED: ' + p)
    for p in new_files:
        if p not in base_files and p not in added_expect:
            problems.append('UNEXPECTED ADDITION: ' + p)
    for p, want_sha in added_expect.items():
        r = new_files.get(p)
        if r is None:
            problems.append('GL FILE ABSENT: ' + p)
        elif r['sha256'] != want_sha:
            problems.append('GL FILE WRONG BYTES: ' + p)
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='the R13-lineage md1 container (r33-md1-root.apfs)')
    ap.add_argument('--apfs-dir', required=True, help='tools/apfs (apfs.py + mkapfs.py)')
    ap.add_argument('--rc-jobs', required=True, help='the rc.jobs.gltest file (userland repo)')
    ap.add_argument('--gl-dir', required=True, help='directory with libOSMesa.8.dylib, gl_test, u2f_test')
    ap.add_argument('--out', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--work', default=None)
    ap.add_argument('--slack', type=int, default=512, help='free blocks to leave (default 512 = 2 MB)')
    args = ap.parse_args()

    apfs = load_apfs(args.apfs_dir)
    work = args.work or args.out + '.work'
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    base_tree = os.path.join(work, 'base_tree')
    os.makedirs(base_tree)

    print('== r34_volume_build ==')
    base = extract_volume(apfs, args.base, base_tree)
    base_files = sum(1 for r in base.values() if r['kind'] == 'file')
    print('base: %d records (%d files) from %s' % (len(base), base_files, args.base))

    # ---- selftest: the extracted tree rebuilds to a container whose every file re-reads byte for byte
    st_tree = os.path.join(work, 'selftest_tree')
    st_out = os.path.join(work, 'selftest.apfs')
    shutil.copytree(base_tree, st_tree, symlinks=True)
    build_container(os.path.join(args.apfs_dir, 'mkapfs.py'), st_tree, st_out, args.slack)
    st = extract_volume(apfs, st_out, os.path.join(work, 'selftest_read'))
    bad = [p for p, r in base.items() if r['kind'] == 'file' and st.get(p) != r]
    assert not bad, 'selftest: %d files do not round-trip: %s' % (len(bad), bad[:5])
    print('selftest: the builder and the reader agree on all %d files (byte for byte)' % base_files)

    # ---- the surgery: one replacement, three additions, everything else verbatim
    gl = {}
    for vol_path, src_name in GL_FILES.items():
        src = os.path.join(args.gl_dir, src_name)
        data = open(src, 'rb').read()
        gl[vol_path] = sha(data)
        dst = os.path.join(base_tree, vol_path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(data)
        os.chmod(dst, 0o755)
        print('add   %-28s %10d B  sha256 %s' % (vol_path, len(data), gl[vol_path][:16] + '...'))
    rc = open(args.rc_jobs, 'rb').read()
    old_rc = base[RC_JOBS_PATH]['sha256']
    with open(os.path.join(base_tree, RC_JOBS_PATH), 'wb') as f:
        f.write(rc)
    os.chmod(os.path.join(base_tree, RC_JOBS_PATH), 0o755)
    print('swap  %-28s %10d B  (was sha256 %s...)' % (RC_JOBS_PATH, len(rc), old_rc[:16]))

    # ---- build and prove the result
    build_container(os.path.join(args.apfs_dir, 'mkapfs.py'), base_tree, args.out, args.slack)
    out_size = os.path.getsize(args.out)
    read = extract_volume(apfs, args.out, os.path.join(work, 'out_read'))
    problems = diff_manifests(base, read, added_expect=gl, changed_expect={RC_JOBS_PATH})
    assert not problems, 'the rebuilt container disagrees: %s' % problems
    new_rc = read[RC_JOBS_PATH]['sha256']
    assert new_rc == sha(rc), 'rc.jobs did not round-trip'
    print('prove: %d files byte-identical to the R13 lineage; %d GL additions in place; rc.jobs = the gltest list'
          % (len(base) - 1, len(gl)))
    print('volume: %s (%d B, %.1f MB), sha256 %s' % (args.out, out_size, out_size / 1048576, sha(open(args.out, 'rb').read())))

    man = {
        'round': 'R34 GL-test',
        'base': {'path': args.base, 'sha256': sha(open(args.base, 'rb').read())},
        'volume': {'path': args.out, 'sha256': sha(open(args.out, 'rb').read()), 'size': out_size},
        'rc_jobs_sha256': new_rc,
        'gl_files': {p: read[p] for p in gl},
        'files': read,
    }
    with open(args.manifest, 'w') as f:
        json.dump(man, f, indent=1, sort_keys=True)
    print('manifest: %s (%d entries)' % (args.manifest, len(read)))
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
