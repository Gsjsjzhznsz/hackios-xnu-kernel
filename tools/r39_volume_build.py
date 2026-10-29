#!/usr/bin/env python3
"""tools/r39_volume_build.py -- build the R39 GLES-only volume from the R38 bytes.

R39 is the lockscreen round.  The R38 device log (4803 lines, read in full) settled what
R36's forensics only suspected: the props-on window is a LOTTERY, and the loser poisons the
whole boot --

  - R37 (the same window, the same bundle): the walker LIVED -- RedwoodGPU client opened (0),
    MTLCreateSystemDefaultDevice() = "Adreno 7c+ Gen 3 (chip 0x6040001, Redwood)", class
    RedwoodMetalDevice, the 11-class 420+-method sweep answered.  J1 SENTINEL ESTABLISHED.
  - R38 (the same list, the R38 kernel): analyticsd walked first and died at the frontier
    (signal 10 at 0x1e32e491c, seconds after metal_props on and BEFORE metal_test's own
    crash), and after that first frontier death EVERY process born afterwards died in dyld:
    lsd respawned 78 times to the end of the log INCLUDING with the properties already OFF
    (spawned 00:03:43.036, dead 00:03:43.057 -- 21 ms), analyticsd x4, mobilegestalt.xpc,
    and backboardd itself at 00:04:56 on the first touch.  The panel froze on CA's boot
    backdrop (frames 1..4, flips 1..3) because mobilegestalt.xpc and lsd -- SpringBoard's
    own dependencies -- were the plague's casualties: the white Apple.
  - R36 had the same shape (mobilegestalt.xpc died before metal_test; backboardd x9).

Whether the walker survives is not ours to choose, and the properties being off afterwards
does not cure the poisoned page-in state.  So R39 removes the trigger STRUCTURALLY: the
demo job runs the base probe set and NOTHING of the discovery chain ever executes -- no
metal_props, no metal_test, no sweep.  Metal's J1 verdict stands on R37's record; the
reference removed Metal from its own port for the same reason (docs/RAPHAEL.md 2026-10-27,
"upstream has merged the userland, removed Metal" -- GLES-only).

The ONE volume change is etc/rc.jobs: R38's rc.jobs.r37metal list (7dd3ccde...) out, the
pin's rc.jobs.r39quiet (the base probes verbatim, the metal tokens ABSENT) in.  Everything
else rides the R38 volume bytes verbatim -- sbin/init is NOT a variable this round: the
union init (a5ef0c3's build, df43673a...) rides byte for byte, asserted against this
record.  No init build, no theos, no kernel -- the GL+Metal six, the Extensions link,
inject.conf's backboardd line: all IN the R38 lineage this script starts from.  (The union
mount itself STAYS: with the properties never set, Metal returns nil before composing the
bundle path -- R35's behavior, zero SIGBUS in 3019 lines -- so the mount is inert, and
changing it would be a second variable.)

What the script does, in order:
  1. extract the R38 volume's tree (tools/apfs/apfs.py, read-only)
  2. assert the R38 lineage is really here (the GL six, the link, inject.conf's line,
     sbin/init == df43673a... and carrying the union fix strings)
  3. assert the base etc/rc.jobs is R38's (7dd3ccde..., the r37metal list, metal lines in)
     and the new rc.jobs.r39quiet differs from it, carries the base probe set, and carries
     NO metal token at all
  4. swap etc/rc.jobs; everything else stays byte for byte -- sbin/init INCLUDED
  5. rebuild with tools/apfs/mkapfs.py and prove the round trip: every file re-reads byte
     for byte, and every R38-lineage file except the one swapped is identical to the base

usage:
  python3 tools/r39_volume_build.py --base base/r38-md1-root.apfs \
      --apfs-dir .userland-pin/tools/apfs --rc-jobs .userland-pin/userland/rc.jobs.r39quiet \
      --out r39-md1-root.apfs --manifest r39-volume-manifest.json [--work DIR]

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

# the R38 volume's own identities (the r38 release's SHA256SUMS + this file's own probe)
R38_MD1 = '7ad5b1f880a938fb2ffb7824a11e4cc5a49d5cbdb66d7405c7fb1626d1ef865c'
R38_INIT_SHA = 'df43673ad99662b41ca650dfd747f15ea34adce6aa91c607be696c9ef13a1f04'
R38_RC_JOBS_SHA = '7dd3ccded4574c81c2508e9343513dc00571aec61967efa88d76c143db3393d0'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_apfs(apfs_dir):
    spec = importlib.util.spec_from_file_location('r39_apfs', os.path.join(apfs_dir, 'apfs.py'))
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
                raise SystemExit('r39_volume_build: %s: neither file, dir nor link -- refusing' % path)
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
    ap.add_argument('--base', required=True, help='the R38 md1 container (r38-md1-root.apfs)')
    ap.add_argument('--apfs-dir', required=True, help='tools/apfs (apfs.py + mkapfs.py)')
    ap.add_argument('--rc-jobs', required=True, help='the rc.jobs.r39quiet file (the userland pin tree)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--work', default=None)
    ap.add_argument('--slack', type=int, default=512, help='free blocks to leave (default 512 = 2 MB)')
    args = ap.parse_args()

    print('== r39_volume_build ==')
    apfs = load_apfs(args.apfs_dir)
    work = args.work or args.out + '.work'
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    base_tree = os.path.join(work, 'base_tree')
    os.makedirs(base_tree)

    base = extract_volume(apfs, args.base, base_tree)
    base_files = sum(1 for r in base.values() if r['kind'] == 'file')
    print('base: %d records (%d files) from %s' % (len(base), base_files, args.base))

    # ---- the R38 lineage must actually be here: GL six, the link, the inject line, the init
    GLM_PRESENT = ('lib/libredwood-gl.dylib', 'lib/RedwoodMetal.bundle/RedwoodMetal',
                   'lib/RedwoodMetal.bundle/Info.plist', 'bin/metal_props', 'bin/metal_test',
                   'bin/metal_drv_test', 'bin/gl_test', 'bin/u2f_test', 'lib/libOSMesa.8.dylib')
    for p in GLM_PRESENT:
        assert p in base and base[p]['kind'] == 'file', 'the base is not the R38 volume: ' + p
    LINK = 'System/Library/Extensions/RedwoodMetal.bundle'
    assert LINK in base and base[LINK].get('kind') == 'link' and \
        base[LINK].get('target') == '/redwood/lib/RedwoodMetal.bundle', \
        'the base is not the R38 volume: the Extensions link is missing or wrong'
    with open(os.path.join(base_tree, 'etc/inject.conf'), 'rb') as f:
        assert b'/redwood/lib/libredwood-gl.dylib backboardd' in f.read(), \
            'the base is not the R38 volume: inject.conf lacks the backboardd line'
    print('lineage check: the base carries the GL+Metal pieces (9 files, the Extensions link, '
          'the inject.conf backboardd line)')

    # ---- sbin/init is NOT a variable this round: it must be the R36/R38 union init, byte for byte
    INIT_PATH = 'sbin/init'
    assert INIT_PATH in base and base[INIT_PATH]['kind'] == 'file', 'the base has no sbin/init'
    init_b = open(os.path.join(base_tree, INIT_PATH), 'rb').read()
    got = sha(init_b)
    assert got == R38_INIT_SHA, \
        'sbin/init is %s -- the R36/R38 record says df43673a...; not the union init, refusing' % got
    FIX_DESC = b"our Metal plug-in beside iOS's own, where Metal's discovery opens it"
    FIX_FAIL = b'bindfs union %s on %s: %s'
    assert FIX_DESC in init_b and FIX_FAIL in init_b, \
        'the init carries the R38 sha but not the union fix strings -- refusing'
    print('init: the R36/R38 union init rides VERBATIM (%d B, sha %s...)' % (len(init_b), got[:16]))

    # ---- the ONE swap: etc/rc.jobs, out the r37metal list, in the GLES-only quiet list
    old_rc_b = open(os.path.join(base_tree, RC_JOBS_PATH), 'rb').read()
    assert sha(old_rc_b) == R38_RC_JOBS_SHA, \
        'the base etc/rc.jobs is %s -- the R38 volume carries 7dd3ccde... (rc.jobs.r37metal); wrong base, refusing' % sha(old_rc_b)
    for needle, what in ((b'metal_props on', 'the props-on line (the R38 list really is here)'),
                         (b'metal_test', 'metal_test'),
                         (b'metal_drv_test --sweep', 'the sweep'),
                         (b'metal_props off', 'the props-off closing line')):
        assert needle in old_rc_b, 'the base rc.jobs lacks %s -- not the r37metal list' % what
    with open(args.rc_jobs, 'rb') as f:
        rc = f.read()
    assert sha(rc) != sha(old_rc_b), "the new rc.jobs is byte-identical to R38's -- nothing changed?"
    # the structural law: NO discovery-chain command may ride the new list (comment lines
    # document the law; only executable lines are bound by it)
    rc_cmds = b'\n'.join(l for l in rc.split(b'\n') if l.strip() and not l.strip().startswith(b'#'))
    for needle in (b'metal_props', b'metal_test', b'metal_drv_test'):
        assert needle not in rc_cmds, 'the new rc.jobs RUNS %s -- R39 is GLES-only, refusing' % needle.decode()
    # the base probe set must ride verbatim
    for needle, what in ((b'/redwood/bin/hello_dyn started by launchd', 'hello_dyn'),
                         (b'/redwood/bin/svc_test', 'svc_test'),
                         (b'/redwood/bin/surf_test', 'surf_test'),
                         (b'/bin/ps -A', 'ps'),
                         (b'/redwood/bin/mg_test', 'mg_test'),
                         (b'/redwood/bin/cpu_test', 'cpu_test')):
        assert needle in rc, 'the new rc.jobs lacks the base probe %s' % what
    assert b'GLES-only' in rc, "the new rc.jobs does not document the GLES-only law"
    print("rc.jobs: R38's r37metal %d B (sha %s...) -> R39's quiet list %d B (sha %s...), "
          "the metal tokens ABSENT, the base probe set verbatim"
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
    print('swap  %-44s %10d B  (R38 was sha256 %s...)' % (RC_JOBS_PATH, len(rc), sha(old_rc_b)[:16]))

    # ---- build and prove the result
    build_container(os.path.join(args.apfs_dir, 'mkapfs.py'), base_tree, args.out, args.slack)
    out_size = os.path.getsize(args.out)
    read = extract_volume(apfs, args.out, os.path.join(work, 'out_read'))
    problems = diff_manifests(base, read, changed_expect={RC_JOBS_PATH})
    assert not problems, 'the rebuilt container disagrees: %s' % problems
    assert read[RC_JOBS_PATH]['sha256'] == sha(rc), 'rc.jobs did not round-trip'
    assert read[INIT_PATH]['sha256'] == R38_INIT_SHA, 'sbin/init moved -- it must ride byte for byte'
    print('prove: %d files byte-identical to the R38 lineage; exactly ONE replaced '
          '(etc/rc.jobs = rc.jobs.r39quiet, the discovery chain structurally off the boot path); '
          'sbin/init and every link untouched' % (len(base) - 1,))
    print('volume: %s (%d B, %.1f MB), sha256 %s' % (args.out, out_size, out_size / 1048576, sha(open(args.out, 'rb').read())))

    man = {
        'round': 'R39 GLES-only lockscreen (the structural props-off round)',
        'base': {'path': args.base, 'sha256': sha(open(args.base, 'rb').read())},
        'volume': {'path': args.out, 'sha256': sha(open(args.out, 'rb').read()), 'size': out_size},
        'sbin_init': {'r36_sha256': R38_INIT_SHA, 'r39_sha256': R38_INIT_SHA,
                      'note': 'rides byte for byte -- NOT a variable this round'},
        'rc_jobs': {'r38_sha256': R38_RC_JOBS_SHA, 'r39_sha256': sha(rc), 'r39_size': len(rc),
                    'note': 'rc.jobs.r39quiet: the base probe set, no metal_props/metal_test/sweep'},
        'files': read,
    }
    with open(args.manifest, 'w') as f:
        json.dump(man, f, indent=1, sort_keys=True)
    print('manifest: %s (%d entries)' % (args.manifest, len(read)))
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
