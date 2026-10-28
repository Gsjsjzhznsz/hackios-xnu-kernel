#!/usr/bin/env python3
"""tools/r37_volume_build.py -- build the R37 props-off volume from the R36 J3-fix bytes.

R37 is the plague-containment round.  The R36 device log (4502 lines, read in full) proved
the J3 fix itself (Metal's step 3 composes /System/Library/Extensions/RedwoodMetal.bundle
on the ROOT and the union mount answers "it is there") AND one new wound: with the two
discovery properties ON the IOAcceleratorES service and the bundle findable, every process
that asks Metal walks the whole chain -- 64 SIGBUS lines in one boot (metal_test, the
sweep, backboardd x9 at the same window-server instruction, SpringBoard, lsd x22,
mobilegestalt.xpc, analyticsd, runningboardd), the panel stuck on the kernel's own
orange/white/blue GPU test picture because no daemon survived to draw over it.

The reference measured this hazard (docs/metal.md 10.14; rc.ios.metal runs the first
props-on boot with launchd UNBORN and ends its list with "metal_props off -- nothing
outlives this list").  R37 ships that containment: the ONE volume change is etc/rc.jobs --
R36's rc.jobs.r36metal VERBATIM plus "/redwood/bin/metal_props off" and the verdict
"/redwood/bin/metal_props show" that both properties read (none) again.

Everything else rides R36's volume bytes verbatim -- sbin/init is NOT a variable this
round: the union init (a5ef4c3's build, the R36 manifest's df43673a...) rides byte for
byte, which this script ASSERTS against the R36 volume manifest's recorded sha.  No init
build, no theos, no kernel -- the GL+Metal six, the Extensions link, inject.conf's
backboardd line: all IN the R36 lineage this script starts from.

What the script does, in order:
  1. extract the R36 volume's tree (tools/apfs/apfs.py, read-only)
  2. assert the R36 lineage is really here (the GLM six, the link, inject.conf's line,
     sbin/init == the R36 manifest's df43673a... and carrying the union fix strings)
  3. assert the base etc/rc.jobs is R36's (90244d6c...) and the new rc.jobs.r37metal
     differs from it and carries the two closing lines (props off + the (none) verdict)
  4. swap etc/rc.jobs; everything else stays byte for byte -- sbin/init INCLUDED
  5. rebuild with tools/apfs/mkapfs.py and prove the round trip: every file re-reads byte
     for byte, and every R36-lineage file except the one swapped is identical to the base

usage:
  python3 tools/r37_volume_build.py --base base/r36-md1-root.apfs \
      --apfs-dir .userland-pin/tools/apfs --rc-jobs .userland-pin/userland/rc.jobs.r37metal \
      --out r37-md1-root.apfs --manifest r37-volume-manifest.json [--work DIR]

outputs: the new container, its manifest (every path: mode, size, sha256 -- a published
release asset), and a verdict line per check.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys

# the ONE replacement (a path inside the volume); nothing is added, nothing is linked
RC_JOBS_PATH = 'etc/rc.jobs'

# the R36 volume's own identities (the r36 release's SHA256SUMS + r36-volume-manifest.json)
R36_INIT_SHA = 'df43673ad99662b41ca650dfd747f15ea34adce6aa91c607be696c9ef13a1f04'
R36_RC_JOBS_SHA = '90244d6cd7c75cf5fb54c561c994364caebd23b68d41b4a7c8abcff8bb4fab91'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_apfs(apfs_dir):
    spec = importlib.util.spec_from_file_location('r37_apfs', os.path.join(apfs_dir, 'apfs.py'))
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
                raise SystemExit('r37_volume_build: %s: neither file, dir nor link -- refusing' % path)
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
    ap.add_argument('--base', required=True, help='the R36 J3-fix md1 container (r36-md1-root.apfs)')
    ap.add_argument('--apfs-dir', required=True, help='tools/apfs (apfs.py + mkapfs.py)')
    ap.add_argument('--rc-jobs', required=True, help='the rc.jobs.r37metal file (the userland pin tree)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--work', default=None)
    ap.add_argument('--slack', type=int, default=512, help='free blocks to leave (default 512 = 2 MB)')
    args = ap.parse_args()

    print('== r37_volume_build ==')
    apfs = load_apfs(args.apfs_dir)
    work = args.work or args.out + '.work'
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    base_tree = os.path.join(work, 'base_tree')
    os.makedirs(base_tree)

    base = extract_volume(apfs, args.base, base_tree)
    base_files = sum(1 for r in base.values() if r['kind'] == 'file')
    print('base: %d records (%d files) from %s' % (len(base), base_files, args.base))

    # ---- the R36 lineage must actually be here: GLM six, the link, the inject line, the init
    GLM_PRESENT = ('lib/libredwood-gl.dylib', 'lib/RedwoodMetal.bundle/RedwoodMetal',
                   'lib/RedwoodMetal.bundle/Info.plist', 'bin/metal_props', 'bin/metal_test',
                   'bin/metal_drv_test', 'bin/gl_test', 'bin/u2f_test', 'lib/libOSMesa.8.dylib')
    for p in GLM_PRESENT:
        assert p in base and base[p]['kind'] == 'file', 'the base is not the R36 J3-fix volume: ' + p
    LINK = 'System/Library/Extensions/RedwoodMetal.bundle'
    assert LINK in base and base[LINK].get('kind') == 'link' and \
        base[LINK].get('target') == '/redwood/lib/RedwoodMetal.bundle', \
        'the base is not the R36 J3-fix volume: the Extensions link is missing or wrong'
    with open(os.path.join(base_tree, 'etc/inject.conf'), 'rb') as f:
        assert b'/redwood/lib/libredwood-gl.dylib backboardd' in f.read(), \
            'the base is not the R36 J3-fix volume: inject.conf lacks the backboardd line'
    print('lineage check: the base carries the GL+Metal pieces (9 files, the Extensions link, '
          'the inject.conf backboardd line)')

    # ---- sbin/init is NOT a variable this round: it must be R36's union init, byte for byte
    INIT_PATH = 'sbin/init'
    assert INIT_PATH in base and base[INIT_PATH]['kind'] == 'file', 'the base has no sbin/init'
    init_b = open(os.path.join(base_tree, INIT_PATH), 'rb').read()
    got = sha(init_b)
    assert got == R36_INIT_SHA, \
        'sbin/init is %s -- the R36 manifest says df43673a...; not the R36 union init, refusing' % got
    FIX_DESC = b"our Metal plug-in beside iOS's own, where Metal's discovery opens it"
    FIX_FAIL = b'bindfs union %s on %s: %s'
    assert FIX_DESC in init_b and FIX_FAIL in init_b, \
        'the init carries the R36 sha but not the union fix strings -- refusing'
    print('init: the R36 union init rides VERBATIM (%d B, sha %s...)' % (len(init_b), got[:16]))

    # ---- the ONE swap: etc/rc.jobs, out R36's list, in the props-off list
    old_rc_b = open(os.path.join(base_tree, RC_JOBS_PATH), 'rb').read()
    assert sha(old_rc_b) == R36_RC_JOBS_SHA, \
        'the base etc/rc.jobs is %s -- R36 built 90244d6c...; wrong base, refusing' % sha(old_rc_b)
    with open(args.rc_jobs, 'rb') as f:
        rc = f.read()
    assert sha(rc) != sha(old_rc_b), "the new rc.jobs is byte-identical to R36's -- nothing changed?"
    for needle, what in ((b'/redwood/bin/rwtouch wait 22', 'the wait'),
                         (b'/redwood/bin/metal_props show', 'props show'),
                         (b'/redwood/bin/metal_props on', 'props on'),
                         (b'/redwood/bin/metal_test', 'metal_test'),
                         (b'/redwood/bin/metal_drv_test --sweep', 'the sweep'),
                         (b'/redwood/bin/metal_props off', 'THE props-off line'),
                         (b'/redwood/bin/metal_props show', 'the closing verdict')):
        assert needle in rc, 'the new rc.jobs lacks %s -- not the R37 list' % what
    # the closing pair must come AFTER the sweep line (the containment is temporal)
    assert rc.rindex(b'/redwood/bin/metal_props off') > rc.rindex(b'/redwood/bin/metal_drv_test --sweep'), \
        'the props-off line must close the list, after the sweep'
    print("rc.jobs: R36's %d B (sha %s...) -> R37's %d B (sha %s...), the closing pair after the sweep"
          % (len(old_rc_b), sha(old_rc_b)[:16], len(rc), sha(rc)[:16]))

    # ---- selftest: the extracted tree rebuilds to a container whose every file re-reads byte for byte
    st_tree = os.path.join(work, 'selftest_tree')
    st_out = os.path.join(work, 'selftest.apfs')
    shutil.copytree(base_tree, st_tree, symlinks=True)
    build_container(os.path.join(args.apfs_dir, 'mkapfs.py'), st_tree, st_out, args.slack)
    st = extract_volume(apfs, st_out, os.path.join(work, 'selftest_read'))
    bad = [p for p, r in base.items() if r['kind'] == 'file' and st.get(p) != r]
    assert not bad, 'selftest: %d files do not round-trip: %s' % (len(bad), bad[:5])
    print('selftest: the builder and the reader agree on all %d files (byte for byte)' % base_files)

    # ---- the surgery: ONE replacement, everything else verbatim -- sbin/init INCLUDED
    with open(os.path.join(base_tree, RC_JOBS_PATH), 'wb') as f:
        f.write(rc)
    os.chmod(os.path.join(base_tree, RC_JOBS_PATH), 0o755)
    print('swap  %-44s %10d B  (R36 was sha256 %s...)' % (RC_JOBS_PATH, len(rc), sha(old_rc_b)[:16]))

    # ---- build and prove the result
    build_container(os.path.join(args.apfs_dir, 'mkapfs.py'), base_tree, args.out, args.slack)
    out_size = os.path.getsize(args.out)
    read = extract_volume(apfs, args.out, os.path.join(work, 'out_read'))
    problems = diff_manifests(base, read, changed_expect={RC_JOBS_PATH})
    assert not problems, 'the rebuilt container disagrees: %s' % problems
    assert read[RC_JOBS_PATH]['sha256'] == sha(rc), 'rc.jobs did not round-trip'
    assert read[INIT_PATH]['sha256'] == R36_INIT_SHA, 'sbin/init moved -- it must ride byte for byte'
    print('prove: %d files byte-identical to the R36 lineage; exactly ONE replaced '
          '(etc/rc.jobs = rc.jobs.r37metal, the props-off closing pair); sbin/init and every '
          'link untouched' % (len(base) - 1,))
    print('volume: %s (%d B, %.1f MB), sha256 %s' % (args.out, out_size, out_size / 1048576, sha(open(args.out, 'rb').read())))

    man = {
        'round': 'R37 props-off containment (the plague round)',
        'base': {'path': args.base, 'sha256': sha(open(args.base, 'rb').read())},
        'volume': {'path': args.out, 'sha256': sha(open(args.out, 'rb').read()), 'size': out_size},
        'sbin_init': {'r36_sha256': R36_INIT_SHA, 'r37_sha256': R36_INIT_SHA,
                      'note': 'rides byte for byte -- NOT a variable this round'},
        'rc_jobs': {'r36_sha256': R36_RC_JOBS_SHA, 'r37_sha256': sha(rc), 'r37_size': len(rc)},
        'files': read,
    }
    with open(args.manifest, 'w') as f:
        json.dump(man, f, indent=1, sort_keys=True)
    print('manifest: %s (%d entries)' % (args.manifest, len(read)))
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
