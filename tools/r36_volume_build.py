#!/usr/bin/env python3
"""tools/r36_volume_build.py -- build the R36 J3-fix md1 volume from the R35 GL+Metal bytes.

R36 is the J3-closing round.  The kernel is R33's own bytes (verbatim, no new code), the
GPU image's cmdline rides R35's set EXACTLY (metal=1 included -- no new token this round),
and the volume's ONE change is sbin/init: the R35 device log proved Metal's discovery
composes /System/Library/Extensions/<MetalPluginName>.bundle on the ROOT and the link that
rode in this image's own /redwood/System/Library/Extensions was never reached by it
(metal_props: "Metal would open /System/Library/Extensions/RedwoodMetal.bundle: NOT THERE";
metal_test enumerated the root's Extensions and found only Apple's AGX bundles;
MTLCreateSystemDefaultDevice() = nil).  The fix (userland pin 6f04967: init a5ef0c3 +
rc.jobs.r36metal) union-binds the image's Extensions dir over the iOS volume's
(MNT_RDONLY | MNT_UNION -- the shape /jb-bin over /flash/bin has ridden since R28i), so the
bundle link stands beside Apple's own at the exact path the framework composes.

Everything else rides R35's volume bytes verbatim: the GL pieces (libOSMesa.8.dylib,
gl_test, u2f_test), GL=1's libredwood-gl.dylib, the RedwoodMetal bundle, the Extensions
link, metal_props/metal_test/metal_drv_test, etc/inject.conf WITH the backboardd line.
The GL+Metal pieces are NOT rebuilt this round -- they are IN the R35 lineage this script
starts from (userland a5ef0c3 touches only init.c; the pins are byte-identical for them).

What the script does, in order:
  1. extract the R35 volume's tree (tools/apfs/apfs.py, read-only)
  2. assert the R35 lineage is really here (GLM files, the link, inject.conf's line, sbin/init)
  3. call tools/r36_init_build.sh (R36_OLD_INIT = the extracted R35-lineage sbin/init, whose
     absence of the fix strings is asserted there) -- the new init lands in r36-init/init
  4. swap sbin/init and etc/rc.jobs; everything else stays byte for byte
  5. rebuild with tools/apfs/mkapfs.py and prove the round trip: every file re-reads byte
     for byte, and every R35-lineage file except the two swapped is identical to the base

usage:
  python3 tools/r36_volume_build.py --base base/r35-md1-root.apfs \
      --apfs-dir tools/apfs --rc-jobs userland/rc.jobs.r36metal \
      --out r36-md1-root.apfs --manifest r36-volume-manifest.json [--work DIR]

outputs: the new container, its manifest (every path: mode, size, sha256 -- a published
release asset), the extracted old init (kept under work/ only), and a verdict line per check.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys

# the two replacements (paths inside the volume); nothing is added, nothing is linked
INIT_PATH = 'sbin/init'
RC_JOBS_PATH = 'etc/rc.jobs'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_apfs(apfs_dir):
    spec = importlib.util.spec_from_file_location('r36_apfs', os.path.join(apfs_dir, 'apfs.py'))
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
                raise SystemExit('r36_volume_build: %s: neither file, dir nor link -- refusing' % path)
    walk(apfs.ROOT_DIR_INO_NUM, '')
    return manifest


def build_container(mkapfs_py, tree, out, slack):
    subprocess.check_call([sys.executable, mkapfs_py, '-z', '--plain', 'fw', '--time', '0',
                           '--slack', str(slack), '-o', out, 'redwood=' + tree])


def diff_manifests(base, new, changed_expect):
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
        if p not in base_files:
            problems.append('UNEXPECTED ADDITION: ' + p)
    for p, r in base.items():
        if r['kind'] == 'link' and new.get(p) != r:
            problems.append('LINK CHANGED: ' + p)
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='the R35 GL+Metal md1 container (r35-md1-root.apfs)')
    ap.add_argument('--apfs-dir', required=True, help='tools/apfs (apfs.py + mkapfs.py)')
    ap.add_argument('--rc-jobs', required=True, help='the rc.jobs.r36metal file (userland pin tree)')
    ap.add_argument('--init-script', default='tools/r36_init_build.sh',
                    help='the init builder (default tools/r36_init_build.sh)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--work', default=None)
    ap.add_argument('--slack', type=int, default=512, help='free blocks to leave (default 512 = 2 MB)')
    args = ap.parse_args()

    # ---- the init FIRST: the builder does the userland checkout at this round's pin, and the
    # checkout is what lays tools/apfs (userland's tools/ beside this tree's) down for the
    # extract below -- the same arrangement r35_gl_metal_build.sh's step ordering relied on.
    # No R36_OLD_INIT here: the witness assertion happens further down, once the R35-lineage
    # init has been extracted from the base volume itself.
    print('== r36_volume_build ==')
    # this script runs from the kernel tree root (CI: cd hackios; the --rc-jobs path and
    # xnu-env.sh are both relative to it) -- the builder inherits that cwd and takes '.' as TREE
    subprocess.check_call(['bash', args.init_script, '.'])
    new_init = open('r36-init/init', 'rb').read()

    apfs = load_apfs(args.apfs_dir)
    work = args.work or args.out + '.work'
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    base_tree = os.path.join(work, 'base_tree')
    os.makedirs(base_tree)

    base = extract_volume(apfs, args.base, base_tree)
    base_files = sum(1 for r in base.values() if r['kind'] == 'file')
    print('base: %d records (%d files) from %s' % (len(base), base_files, args.base))

    # ---- the R35 lineage must actually be here: GLM six, the link, the inject line, the init
    GLM_PRESENT = ('lib/libredwood-gl.dylib', 'lib/RedwoodMetal.bundle/RedwoodMetal',
                   'lib/RedwoodMetal.bundle/Info.plist', 'bin/metal_props', 'bin/metal_test',
                   'bin/metal_drv_test', 'bin/gl_test', 'bin/u2f_test', 'lib/libOSMesa.8.dylib')
    for p in GLM_PRESENT:
        assert p in base and base[p]['kind'] == 'file', 'the base is not the R35 GL+Metal volume: ' + p
    LINK = 'System/Library/Extensions/RedwoodMetal.bundle'
    assert LINK in base and base[LINK].get('kind') == 'link' and \
        base[LINK].get('target') == '/redwood/lib/RedwoodMetal.bundle', \
        'the base is not the R35 GL+Metal volume: the Extensions link is missing or wrong'
    with open(os.path.join(base_tree, 'etc/inject.conf'), 'rb') as f:
        assert b'/redwood/lib/libredwood-gl.dylib backboardd' in f.read(), \
            'the base is not the R35 GL+Metal volume: inject.conf lacks the backboardd line'
    assert INIT_PATH in base and base[INIT_PATH]['kind'] == 'file', 'the base has no sbin/init'
    print('lineage check: the base carries the R35 GL+Metal pieces (9 files, the Extensions link, '
          'the inject.conf backboardd line, sbin/init)')

    # ---- the init: the fix strings must be "in the new, NOT in the R35-lineage one"
    old_init_b = open(os.path.join(base_tree, INIT_PATH), 'rb').read()
    assert sha(new_init) != sha(old_init_b), 'the built init is byte-identical to the R35 one -- nothing changed?'
    FIX_DESC = b"our Metal plug-in beside iOS's own, where Metal's discovery opens it"
    FIX_FAIL = b'bindfs union %s on %s: %s'
    assert FIX_DESC in new_init and FIX_FAIL in new_init, \
        'the built init does not carry the union fix strings -- not the fixed init'
    assert FIX_DESC not in old_init_b and FIX_FAIL not in old_init_b, \
        'the R35-lineage sbin/init ALREADY carries the fix strings -- wrong base?'
    print('init: R35-lineage %d B (sha %s...) -> R36 %d B (sha %s...)'
          % (len(old_init_b), sha(old_init_b)[:16], len(new_init), sha(new_init)[:16]))

    # ---- selftest: the extracted tree rebuilds to a container whose every file re-reads byte for byte
    st_tree = os.path.join(work, 'selftest_tree')
    st_out = os.path.join(work, 'selftest.apfs')
    shutil.copytree(base_tree, st_tree, symlinks=True)
    build_container(os.path.join(args.apfs_dir, 'mkapfs.py'), st_tree, st_out, args.slack)
    st = extract_volume(apfs, st_out, os.path.join(work, 'selftest_read'))
    bad = [p for p, r in base.items() if r['kind'] == 'file' and st.get(p) != r]
    assert not bad, 'selftest: %d files do not round-trip: %s' % (len(bad), bad[:5])
    print('selftest: the builder and the reader agree on all %d files (byte for byte)' % base_files)

    # ---- the surgery: TWO replacements, everything else verbatim
    with open(args.rc_jobs, 'rb') as f:
        rc = f.read()
    old_rc = base[RC_JOBS_PATH]['sha256']
    with open(os.path.join(base_tree, INIT_PATH), 'wb') as f:
        f.write(new_init)
    os.chmod(os.path.join(base_tree, INIT_PATH), 0o755)
    print('swap  %-44s %10d B  (R35 was sha256 %s...)' % (INIT_PATH, len(new_init), sha(old_init_b)[:16]))
    with open(os.path.join(base_tree, RC_JOBS_PATH), 'wb') as f:
        f.write(rc)
    os.chmod(os.path.join(base_tree, RC_JOBS_PATH), 0o755)
    print('swap  %-44s %10d B  (was sha256 %s...)' % (RC_JOBS_PATH, len(rc), old_rc[:16]))

    # ---- build and prove the result
    build_container(os.path.join(args.apfs_dir, 'mkapfs.py'), base_tree, args.out, args.slack)
    out_size = os.path.getsize(args.out)
    read = extract_volume(apfs, args.out, os.path.join(work, 'out_read'))
    problems = diff_manifests(base, read, changed_expect={INIT_PATH, RC_JOBS_PATH})
    assert not problems, 'the rebuilt container disagrees: %s' % problems
    assert read[INIT_PATH]['sha256'] == sha(new_init), 'sbin/init did not round-trip'
    assert read[RC_JOBS_PATH]['sha256'] == sha(rc), 'rc.jobs did not round-trip'
    print('prove: %d files byte-identical to the R35 lineage; exactly TWO replaced '
          '(sbin/init = the union fix, etc/rc.jobs = rc.jobs.r36metal); links untouched' % (len(base) - 2,))
    print('volume: %s (%d B, %.1f MB), sha256 %s' % (args.out, out_size, out_size / 1048576, sha(open(args.out, 'rb').read())))

    man = {
        'round': 'R36 J3-fix (the union init)',
        'base': {'path': args.base, 'sha256': sha(open(args.base, 'rb').read())},
        'volume': {'path': args.out, 'sha256': sha(open(args.out, 'rb').read()), 'size': out_size},
        'sbin_init': {'r35_sha256': sha(old_init_b), 'r36_sha256': sha(new_init),
                      'r36_size': len(new_init)},
        'rc_jobs_sha256': sha(rc),
        'files': read,
    }
    with open(args.manifest, 'w') as f:
        json.dump(man, f, indent=1, sort_keys=True)
    print('manifest: %s (%d entries)' % (args.manifest, len(read)))
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
