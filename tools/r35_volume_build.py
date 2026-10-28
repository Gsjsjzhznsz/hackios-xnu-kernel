#!/usr/bin/env python3
"""tools/r35_volume_build.py -- build the R35 GL+Metal md1 volume from the R34-lineage bytes.

R35 is the Metal round.  The kernel is R33's own bytes (verbatim, no new code) and the GPU
image's cmdline gains exactly ONE token (metal=1 -- the IOAcceleratorES service's permission
to take the two discovery properties).  What moves in the volume:

    /lib/libredwood-gl.dylib            GL=1's library: CoreAnimation's call table over Mesa
                                        (src/eagl.c + src/gl_osmesa.c); etc/inject.conf gains
                                        the line that puts it inside backboardd, whose own gate
                                        (src/inject.c gpu_draws()) opens on the RedwoodGPU
                                        service -- "the system draws with the GPU" this round
    /lib/RedwoodMetal.bundle/RedwoodMetal  the Metal driver as a BUNDLE -- the form Metal's
    /lib/RedwoodMetal.bundle/Info.plist    discovery step 3 opens (metal_plugin.plist)
    /System/Library/Extensions/RedwoodMetal.bundle -> /redwood/lib/RedwoodMetal.bundle
                                        the link Metal composes its path onto; this directory
                                        is iOS's own, so the link travels in the volume
    /bin/metal_props                    the switch (MetalPluginName / MetalPluginClassName)
    /bin/metal_test                     the five steps of the chain + CoreImage, on the record
    /bin/metal_drv_test                 every class against every method (--sweep)
    /etc/rc.jobs                        REPLACED by rc.jobs.r35metal (wait 22; props show/on;
                                        metal_test; metal_drv_test --sweep)
    /etc/inject.conf                    the R34-lineage table + ONE line:
                                        /redwood/lib/libredwood-gl.dylib backboardd

The R34 GL pieces ride unchanged (lib/libOSMesa.8.dylib, bin/gl_test, bin/u2f_test -- they are
IN the R34-lineage base this script starts from).

Why surgery on the R34 volume and not a fresh userland/build.sh: that script's job list is
made by mkjobs.py --root-volume against ios165/rootfs.slt (the listing of THIS phone's iOS
volume), which exists on the maintainer's machine and nowhere else.  The R34-lineage volume
already carries that list, judged and baked (and R34's GL additions, device-proven).  So:
read the container with tools/apfs/apfs.py (read-only), materialize the tree, add the Metal
files and the link, swap the two, rebuild with tools/apfs/mkapfs.py, and prove the round trip:
every file re-reads from the new container byte for byte, and every R34-lineage file except
the two swapped is byte-identical to what the base carried.

usage:
  python3 tools/r35_volume_build.py --base base/r34-md1-root.apfs \
      --apfs-dir tools/apfs --rc-jobs userland/rc.jobs.r35metal --glm-dir r35-glm \
      --out r35-md1-root.apfs --manifest r35-volume-manifest.json [--work DIR]

outputs: the new container, its manifest (every path: mode, size, sha256 -- the flash
build's law and a published release asset), and a verdict line per check.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys

# the six GL+Metal additions, the one link, and the two replacements (paths inside the volume)
GLM_FILES = {
    'lib/libredwood-gl.dylib': 'libredwood-gl.dylib',
    'lib/RedwoodMetal.bundle/RedwoodMetal': 'RedwoodMetal',
    'lib/RedwoodMetal.bundle/Info.plist': 'Info.plist',
    'bin/metal_props': 'metal_props',
    'bin/metal_test': 'metal_test',
    'bin/metal_drv_test': 'metal_drv_test',
}
GLM_LINKS = {
    'System/Library/Extensions/RedwoodMetal.bundle': '/redwood/lib/RedwoodMetal.bundle',
}
RC_JOBS_PATH = 'etc/rc.jobs'
INJECT_PATH = 'etc/inject.conf'
INJECT_ADDITION = b'/redwood/lib/libredwood-gl.dylib backboardd\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_apfs(apfs_dir):
    spec = importlib.util.spec_from_file_location('r35_apfs', os.path.join(apfs_dir, 'apfs.py'))
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
                raise SystemExit('r35_volume_build: %s: neither file, dir nor link -- refusing' % path)
    walk(apfs.ROOT_DIR_INO_NUM, '')
    return manifest


def build_container(mkapfs_py, tree, out, slack):
    subprocess.check_call([sys.executable, mkapfs_py, '-z', '--plain', 'fw', '--time', '0',
                           '--slack', str(slack), '-o', out, 'redwood=' + tree])


def diff_manifests(base, new, added_expect, changed_expect, links_expect):
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
            problems.append('GLM FILE ABSENT: ' + p)
        elif r['sha256'] != want_sha:
            problems.append('GLM FILE WRONG BYTES: ' + p)
    # the one new link, and no link may have moved
    for p, target in links_expect.items():
        r = new.get(p)
        if r is None:
            problems.append('GLM LINK ABSENT: ' + p)
        elif r.get('kind') != 'link' or r.get('target') != target:
            problems.append('GLM LINK WRONG: %s -> %r' % (p, (r or {}).get('target')))
    for p, r in base.items():
        if r['kind'] == 'link' and p not in links_expect:
            if new.get(p) != r:
                problems.append('LINK CHANGED: ' + p)
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='the R34-lineage md1 container (r34-md1-root.apfs)')
    ap.add_argument('--apfs-dir', required=True, help='tools/apfs (apfs.py + mkapfs.py)')
    ap.add_argument('--rc-jobs', required=True, help='the rc.jobs.r35metal file (userland repo)')
    ap.add_argument('--glm-dir', required=True,
                    help='directory with libredwood-gl.dylib, RedwoodMetal, Info.plist, '
                         'metal_props, metal_test, metal_drv_test')
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

    print('== r35_volume_build ==')
    base = extract_volume(apfs, args.base, base_tree)
    base_files = sum(1 for r in base.values() if r['kind'] == 'file')
    print('base: %d records (%d files) from %s' % (len(base), base_files, args.base))

    # ---- the R34 lineage must actually be here: the GL pieces are this round's carry
    for p in ('lib/libOSMesa.8.dylib', 'bin/gl_test', 'bin/u2f_test'):
        assert p in base and base[p]['kind'] == 'file', 'the base is not the R34 GL volume: ' + p
    print('lineage check: the base carries the R34 GL pieces (libOSMesa.8.dylib, gl_test, u2f_test)')

    # ---- selftest: the extracted tree rebuilds to a container whose every file re-reads byte for byte
    st_tree = os.path.join(work, 'selftest_tree')
    st_out = os.path.join(work, 'selftest.apfs')
    shutil.copytree(base_tree, st_tree, symlinks=True)
    build_container(os.path.join(args.apfs_dir, 'mkapfs.py'), st_tree, st_out, args.slack)
    st = extract_volume(apfs, st_out, os.path.join(work, 'selftest_read'))
    bad = [p for p, r in base.items() if r['kind'] == 'file' and st.get(p) != r]
    assert not bad, 'selftest: %d files do not round-trip: %s' % (len(bad), bad[:5])
    print('selftest: the builder and the reader agree on all %d files (byte for byte)' % base_files)

    # ---- the surgery: two replacements, six additions, one link, everything else verbatim
    glm = {}
    for vol_path, src_name in GLM_FILES.items():
        src = os.path.join(args.glm_dir, src_name)
        data = open(src, 'rb').read()
        glm[vol_path] = sha(data)
        dst = os.path.join(base_tree, vol_path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(data)
        os.chmod(dst, 0o755)
        print('add   %-44s %10d B  sha256 %s' % (vol_path, len(data), glm[vol_path][:16] + '...'))
    for vol_path, target in GLM_LINKS.items():
        host = os.path.join(base_tree, vol_path)
        os.makedirs(os.path.dirname(host), exist_ok=True)
        os.symlink(target, host)
        print('link  %-44s -> %s' % (vol_path, target))

    rc = open(args.rc_jobs, 'rb').read()
    old_rc = base[RC_JOBS_PATH]['sha256']
    with open(os.path.join(base_tree, RC_JOBS_PATH), 'wb') as f:
        f.write(rc)
    os.chmod(os.path.join(base_tree, RC_JOBS_PATH), 0o755)
    print('swap  %-44s %10d B  (was sha256 %s...)' % (RC_JOBS_PATH, len(rc), old_rc[:16]))

    old_inj = open(os.path.join(base_tree, INJECT_PATH), 'rb').read()
    assert sha(old_inj) == base[INJECT_PATH]['sha256'], 'the inject.conf record disagrees with the bytes on the tree'
    assert b'libredwood-gl' not in old_inj, 'the base already carries a libredwood-gl line'
    new_inj = old_inj + INJECT_ADDITION
    with open(os.path.join(base_tree, INJECT_PATH), 'wb') as f:
        f.write(new_inj)
    os.chmod(os.path.join(base_tree, INJECT_PATH), 0o755)
    print('patch %-44s %10d B  (+%d B: the backboardd line)' % (INJECT_PATH, len(new_inj), len(INJECT_ADDITION)))

    # ---- build and prove the result
    build_container(os.path.join(args.apfs_dir, 'mkapfs.py'), base_tree, args.out, args.slack)
    out_size = os.path.getsize(args.out)
    read = extract_volume(apfs, args.out, os.path.join(work, 'out_read'))
    problems = diff_manifests(base, read, added_expect=glm,
                              changed_expect={RC_JOBS_PATH, INJECT_PATH}, links_expect=GLM_LINKS)
    assert not problems, 'the rebuilt container disagrees: %s' % problems
    new_rc = read[RC_JOBS_PATH]['sha256']
    assert new_rc == sha(rc), 'rc.jobs did not round-trip'
    assert read[INJECT_PATH]['sha256'] == sha(new_inj), 'inject.conf did not round-trip'
    assert INJECT_ADDITION in open(os.path.join(work, 'out_read', INJECT_PATH), 'rb').read()
    print('prove: %d files byte-identical to the R34 lineage; %d GLM additions in place; '
          '1 link in place; rc.jobs = the r35metal list; inject.conf = the table + the backboardd line'
          % (len(base) - 2, len(glm)))
    print('volume: %s (%d B, %.1f MB), sha256 %s' % (args.out, out_size, out_size / 1048576, sha(open(args.out, 'rb').read())))

    man = {
        'round': 'R35 GL+Metal',
        'base': {'path': args.base, 'sha256': sha(open(args.base, 'rb').read())},
        'volume': {'path': args.out, 'sha256': sha(open(args.out, 'rb').read()), 'size': out_size},
        'rc_jobs_sha256': new_rc,
        'inject_conf_sha256': sha(new_inj),
        'glm_files': {p: read[p] for p in glm},
        'glm_links': GLM_LINKS,
        'files': read,
    }
    with open(args.manifest, 'w') as f:
        json.dump(man, f, indent=1, sort_keys=True)
    print('manifest: %s (%d entries)' % (args.manifest, len(read)))
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
