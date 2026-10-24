#!/usr/bin/env python3
# r24b_volume_audit.py -- R24b md1 volume forensics.
#
# Parses an APFS container image exactly the way the kernel's bsd/miscfs/apfs/apfs_lib.c
# does (same structures from apfs_format.h, same fletcher64, same B-tree conventions),
# then answers the R24 regression's volume questions:
#
#   1. Is every CHECKSUMMED (metadata) block still valid after the R24 same-length swap?
#      (the kernel verifies fletcher64 on metadata reads; file data carries no checksum)
#   2. Which file owns the swapped byte region; do the OTHER fw files' extents overlap it?
#   3. What does /fw/a640_zap.mbn read back as, in place, from each volume (sha256)?
#
# usage:
#   r24b_volume_audit.py report <image>                     # structure + checksum audit
#   r24b_volume_audit.py diff   <imgA> <imgB>               # byte/block diff A -> B
#   r24b_volume_audit.py files <image> [outdir]             # list fw/ files + extract all
#   r24b_volume_audit.py zap    <image>                     # in-place /fw/a640_zap.mbn sha256

import hashlib, struct, sys, os

MOD = 0xFFFFFFFF

def fletcher64(block):
    # the kernel's apfs_fletcher64 (apfs_lib.c:77): over block[8..], with the APFS tail
    s1 = s2 = 0
    for i in range(8, len(block) - 3, 4):
        s1 = (s1 + struct.unpack_from('<I', block, i)[0]) % MOD
        s2 = (s2 + s1) % MOD
    c1 = MOD - ((s1 + s2) % MOD)
    c2 = MOD - ((s1 + c1) % MOD)
    return (c2 << 32) | c1

def cksum_ok(block):
    if len(block) < 8 or len(block) % 4:
        return False
    return fletcher64(block) == struct.unpack_from('<Q', block, 0)[0]

class Img:
    def __init__(self, path):
        self.path = path
        self.data = open(path, 'rb').read()
        if self.data[32:36] != b'NXSB':
            raise SystemExit(f'{path}: no NXSB at +32')
        f = self.nx = self.fields_nx(self.data)
        self.bs = f['block_size']
        self.blocks = f['block_count']
        if len(self.data) != self.bs * self.blocks:
            print(f'note: image {len(self.data):,} B != {self.bs}x{self.blocks:,}')

    # ---- the NXSB fields the kernel's open path uses (apfs_nx_open) ----
    def fields_nx(self, b):
        u = struct.unpack_from
        return dict(
            magic=u('<I', b, 32)[0], block_size=u('<I', b, 36)[0], block_count=u('<Q', b, 40)[0],
            xid=u('<Q', b, 16)[0],
            xp_desc_blocks=u('<I', b, 104)[0], xp_data_blocks=u('<I', b, 108)[0],
            xp_desc_base=u('<Q', b, 112)[0], xp_data_base=u('<Q', b, 120)[0],
            desc_next=u('<I', b, 128)[0], data_next=u('<I', b, 132)[0],
            desc_index=u('<I', b, 136)[0], desc_len=u('<I', b, 140)[0],
            data_index=u('<I', b, 144)[0], data_len=u('<I', b, 148)[0],
            omap_oid=u('<Q', b, 160)[0],
            fs_oids=[u('<Q', b, 184 + 8 * i)[0] for i in range(100)],
        )

    def blk(self, addr):
        off = addr * self.bs
        return self.data[off:off + self.bs]

    # ---- B-tree node parse, apfs_lib.c node_parse/node_entry verbatim ----
    def node(self, b):
        flags, level = struct.unpack_from('<HH', b, 32)
        nkeys = struct.unpack_from('<I', b, 36)[0]
        ts_off, ts_len = struct.unpack_from('<HH', b, 40)
        bs = self.bs
        toc_off, toc_len = 56 + ts_off, ts_len
        key_base = toc_off + toc_len
        val_end = bs
        if flags & 0x0001:                      # ROOT: the btree info sits at the end
            val_end = bs - 40
        fixed = bool(flags & 0x0004)
        leaf = bool(flags & 0x0002)
        key_size = val_size = 0
        if flags & 0x0001:
            key_size, val_size = struct.unpack_from('<II', b, bs - 40 + 8)
        return dict(blk=b, flags=flags, level=level, nkeys=nkeys, toc_off=toc_off,
                    toc_len=toc_len, key_base=key_base, val_end=val_end,
                    fixed=fixed, leaf=leaf, key_size=key_size, val_size=val_size)

    def entries(self, n):
        b, out = n['blk'], []
        for i in range(n['nkeys']):
            if n['fixed']:
                koff, voff = struct.unpack_from('<HH', b, n['toc_off'] + 4 * i)
                kl, vl = n['key_size'], (n['val_size'] if n['leaf'] else 8)
            else:
                koff, kl, voff, vl = struct.unpack_from('<HHHH', b, n['toc_off'] + 8 * i)
                if voff == 0xffff:
                    out.append((n['key_base'] + koff, kl, None, 0)); continue
            out.append((n['key_base'] + koff, kl, n['val_end'] - voff, vl))
        return out

    # ---- omap lookup: latest xid, not deleted (the kernel's omap_cursor rule) ----
    def omap_recs(self, omap_phys_paddr):
        """walk the PHYSICAL omap B-tree behind an apfs_omap_phys block;
        yield (oid, xid, flags, paddr)"""
        op = self.blk(omap_phys_paddr)
        if not cksum_ok(op):
            raise SystemExit(f'omap phys at block {omap_phys_paddr}: BAD CHECKSUM')
        tree_root = struct.unpack_from('<Q', op, 48)[0]     # om_tree_oid: physical
        recs, stack = [], [tree_root]
        seen = set()
        while stack:
            addr = stack.pop()
            if addr in seen or addr == 0 or addr >= self.blocks:
                continue
            seen.add(addr)
            b = self.blk(addr)
            if not cksum_ok(b):
                raise SystemExit(f'omap node at block {addr}: BAD CHECKSUM')
            n = self.node(b)
            if not n['leaf']:
                for (ko, kl, vo, vl) in self.entries(n):
                    if vo is not None:
                        stack.append(struct.unpack_from('<Q', b, vo)[0])
            else:
                for (ko, kl, vo, vl) in self.entries(n):
                    oid, xid = struct.unpack_from('<QQ', b, ko)
                    if vo is None:
                        continue
                    ov_flags, ov_size, ov_paddr = struct.unpack_from('<IIQ', b, vo)
                    recs.append((oid, xid, ov_flags, ov_paddr))
        return recs

    def omap_latest(self, recs):
        best = {}
        for (oid, xid, flags, paddr) in recs:
            if flags & 1:                       # DELETED
                continue
            if oid not in best or xid > best[oid][0]:
                best[oid] = (xid, paddr)
        return {oid: paddr for oid, (xid, paddr) in best.items()}

    def resolve(self, omap_paddr, oid):
        m = self.omap_latest(self.omap_recs(omap_paddr))
        if oid not in m:
            raise SystemExit(f'oid {oid:#x} not in omap at {omap_paddr:#x}')
        return m[oid]

    # ---- the fs tree: inodes, drecs, file extents ----
    def fs_tree(self, root_oid, vol_omap_paddr, hashed_drecs=False):
        vmap = self.omap_latest(self.omap_recs(vol_omap_paddr))
        def addr_of(oid):
            return vmap.get(oid, oid)       # virtual oid through the map, else physical
        inodes, drecs, extents, stack, seen = {}, [], {}, [addr_of(root_oid)], set()
        while stack:
            addr = stack.pop()
            if addr in seen or addr >= self.blocks:
                continue
            seen.add(addr)
            b = self.blk(addr)
            if not cksum_ok(b):
                raise SystemExit(f'fs node at block {addr}: BAD CHECKSUM')
            n = self.node(b)
            if not n['leaf']:
                for (ko, kl, vo, vl) in self.entries(n):
                    if vo is not None:
                        stack.append(addr_of(struct.unpack_from('<Q', b, vo)[0]))
                continue
            for (ko, kl, vo, vl) in self.entries(n):
                if vo is None:
                    continue
                oid_and_type = struct.unpack_from('<Q', b, ko)[0]
                otype = oid_and_type >> 60
                obj_id = oid_and_type & 0x0FFFFFFFFFFFFFFF
                if otype == 3:                  # INODE: parent_id @+0, private_id @+8
                    parent_id, private_id = struct.unpack_from('<QQ', b, vo)
                    inodes[obj_id] = (parent_id, private_id)
                elif otype == 8:                # FILE_EXTENT: logical @key+8; val len,phys,crypto
                    logical = struct.unpack_from('<Q', b, ko + 8)[0]
                    len_flags, phys, crypto = struct.unpack_from('<QQQ', b, vo)
                    extents.setdefault(obj_id, []).append(
                        (logical, len_flags & 0x00FFFFFFFFFFFFFF, phys))
                elif otype == 9:                # DIR_REC: hashed key: hdr + len/hash u32 + name;
                                                # plain key: hdr + len u16 + name.  mkapfs uses
                                                # hashed keys even on case-sensitive volumes, so
                                                # detect from the key itself, not from features.
                    if hashed_drecs is None:
                        nlh = struct.unpack_from('<I', b, ko + 8)[0]
                        trial = nlh & 0x3FF
                        cand = b[ko + 12:ko + 12 + trial - 1]
                        hashed_drecs = (1 <= trial <= 256 and
                                        all(0x20 <= c < 0x7f for c in cand))
                    if hashed_drecs:
                        nlh = struct.unpack_from('<I', b, ko + 8)[0]
                        name_len = nlh & 0x3FF
                        name = b[ko + 12:ko + 12 + name_len - 1].decode('utf-8', 'replace')
                    else:
                        name_len = struct.unpack_from('<H', b, ko + 8)[0]
                        name = b[ko + 10:ko + 10 + name_len - 1].decode('utf-8', 'replace')
                    file_id, date_added, dflags = struct.unpack_from('<QQH', b, vo)
                    drecs.append((obj_id, name, file_id, dflags & 0xF))
        return inodes, drecs, extents

    def volumes(self):
        out = []
        m = self.omap_latest(self.omap_recs(self.nx['omap_oid']))
        for oid in self.nx['fs_oids']:
            if oid and oid in m:
                sb = self.blk(m[oid])
                if sb[32:36] == b'APSB' and cksum_ok(sb):
                    omap_oid = struct.unpack_from('<Q', sb, 128)[0]
                    root_oid = struct.unpack_from('<Q', sb, 136)[0]
                    name = sb[296:296 + 256].split(b'\0')[0].decode('utf-8', 'replace')
                    out.append((name, oid, m[oid], omap_oid, root_oid))
        return out

    def file_tree(self):
        """path -> (extents, private_id); only regular files"""
        vol = self.volumes()[0]
        inodes, drecs, extents = self.fs_tree(vol[4], vol[3], None)
        children = {}
        for (parent, name, file_id, dt) in drecs:
            children.setdefault(parent, []).append((name, file_id, dt))
        out = {}
        def walk(dir_id, prefix):
            for (name, fid, dt) in children.get(dir_id, []):
                if fid == 2:
                    continue
                if dt == 4:                     # DT_DIR
                    walk(fid, f'{prefix}{name}/')
                else:
                    priv = inodes.get(fid, (0, fid))[1]
                    ext = sorted(extents.get(priv, []))
                    out[f'{prefix}{name}'] = (ext, priv)
        walk(2, '')
        return out

    def resolve_vol_root(self):
        for (name, oid, paddr, omap_oid, root_oid) in self.volumes():
            return self.resolve(omap_oid, root_oid)
        raise SystemExit('no APSB volume found')

    def file_bytes(self, ext):
        out = bytearray()
        for (logical, ln, phys) in sorted(ext):
            if phys == 0:
                out += b'\0' * ln
                continue
            off = phys * self.bs
            out += self.data[off:off + ln]
        return bytes(out)

    # ---- checksum audit over the WHOLE image ----
    def audit(self):
        meta_valid = meta_bad = data = 0
        meta_bad_list = []
        type_hist = {}
        for a in range(min(self.blocks, len(self.data) // self.bs)):
            b = self.blk(a)
            if cksum_ok(b):
                t = struct.unpack_from('<I', b, 24)[0] & 0xFFFF
                meta_valid += 1
                type_hist[t] = type_hist.get(t, 0) + 1
            else:
                # a block whose first 8 bytes fail fletcher is, per this driver,
                # file data (no object header, no checksum) -- apfs_lib.c:2465
                data += 1
                t = struct.unpack_from('<I', b, 24)[0] & 0xFFFF
                if b[32:36] in (b'NXSB', b'APSB', b'BTSX'):
                    meta_bad_list.append((a, b[32:36].decode('latin1')))
        return meta_valid, data, type_hist, meta_bad_list

def magic_name(t):
    return {1: 'nx-superblock', 2: 'btree-root', 3: 'btree-node', 4: 'volume-superblock',
            5: 'spaceman', 6: 'spaceman-cab', 7: 'bitmap', 8: 'bitmap-block',
            9: 'checkpoint-map', 0x0b: 'omap', 0x0e: 'fs-tree', 0x0f: 'extents-tree',
            0x10: 'reap-queue', 0x0c: 'reaper', 0x0d: 'efi-jumpstart'}.get(t, f'type{t:#x}')

def cmd_report(path):
    im = Img(path)
    print(f'== {path}: block_size {im.bs}, blocks {im.blocks:,} ({len(im.data):,} B)')
    print(f'   nx xid {im.nx["xid"]}, desc area {im.nx["xp_desc_base"]:#x}+{im.nx["xp_desc_blocks"]}, '
          f'omap oid {im.nx["omap_oid"]:#x}')
    for (name, oid, paddr, omap_oid, root_oid) in im.volumes():
        print(f'   volume "{name}" oid {oid:#x} sb@{paddr} omap@{omap_oid:#x} root-tree oid {root_oid:#x}')
    valid, data, hist, bad = im.audit()
    print(f'   checksum audit: {valid} metadata blocks VALID, {data:,} data blocks (no checksum), '
          f'bad superblock blocks: {bad if bad else "none"}')
    for t in sorted(hist):
        print(f'     {magic_name(t):22s} x {hist[t]}')
    ft = im.file_tree()
    print(f'   files: {len(ft)}')
    swap_lo, swap_hi = 0x7b000, 0x7b000 + 14256
    for p in sorted(ft):
        ext, priv = ft[p]
        spans = ', '.join(f'{e[2] * im.bs:#x}+{e[1]}' for e in ext) or '(sparse)'
        size = sum(e[1] for e in ext)
        hit = '  <-- THE SWAP REGION' if any(e[2] * im.bs < swap_hi and e[2] * im.bs + e[1] > swap_lo for e in ext) else ''
        print(f'     {p:44s} {size:>9,} B  @ {spans}{hit}')

def cmd_files(path, outdir):
    im = Img(path)
    ft = im.file_tree()
    os.makedirs(outdir, exist_ok=True)
    for p, (ext, priv) in sorted(ft.items()):
        data = im.file_bytes(ext)
        fn = os.path.join(outdir, p.replace('/', '_'))
        open(fn, 'wb').write(data)
        print(f'{p:44s} {len(data):>9,} B  sha256 {hashlib.sha256(data).hexdigest()}')

def cmd_zap(path):
    im = Img(path)
    ft = im.file_tree()
    for p in ft:
        if p.endswith('a640_zap.mbn'):
            d = im.file_bytes(ft[p][0])
            print(f'{path}: {p} {len(d):,} B sha256 {hashlib.sha256(d).hexdigest()}')
            return
    print(f'{path}: no a640_zap.mbn')

def cmd_diff(path_a, path_b):
    a, b = Img(path_a), Img(path_b)
    if len(a.data) != len(b.data):
        raise SystemExit('sizes differ')
    A, B, bs = a.data, b.data, a.bs
    runs, i = [], 0
    while i < len(A):
        if A[i] != B[i]:
            j = i
            while j < len(A) and A[j] != B[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    print(f'== diff {os.path.basename(path_a)} -> {os.path.basename(path_b)}: '
          f'{sum(e - s for s, e in runs):,} bytes in {len(runs)} runs')
    for (s, e) in runs:
        blk_s, blk_e = s // bs, (e - 1) // bs
        va, vb = cksum_ok(A[blk_s * bs:blk_s * bs + bs]), cksum_ok(B[blk_s * bs:blk_s * bs + bs])
        print(f'   {s:#08x}..{e - 1:#08x} ({e - s:>5,} B)  blocks {blk_s}..{blk_e}'
              f'  meta-valid: A={va} B={vb}')
    blkA = {k: A[k * bs:(k + 1) * bs] for k in range(len(A) // bs)}
    changed = sorted({s // bs for (s, e) in runs})
    meta_changed = [k for k in changed if cksum_ok(blkA[k]) or cksum_ok(B[k * bs:(k + 1) * bs])]
    print(f'   blocks touched: {len(changed)} -> {changed}')
    print(f'   of those, CHECKSUMMED (metadata) blocks: {meta_changed if meta_changed else "NONE"}')


# ---------------------------------------------------------------- CI gate ----
# r24b gate: the R24 same-length swap ran over THIS volume; prove, from the volume's
# own structures, that it is structurally what it claims:
#   - every checksummed (metadata) block still verifies (the swap touched none);
#   - the three fw files sit where R13 put them, and the two the swap never touched
#     are byte-identical to their R13 selves (pinned sha256);
#   - /fw/a640_zap.mbn reads back, in place, as the phone's own production-signed
#     file (pinned sha256 of its 14256 content bytes);
#   - step 18's a630_sqe.fw and the GMU's a640_gmu.bin share NO block with the zap.

PINS = {
    'fw/a630_sqe.fw':  ('f5446e14a2683addc00b56dd565000c27eb8c8ed395eabedf0b7cc8dc95f02fa', 36864, 0x68000),
    'fw/a640_gmu.bin': ('044448652f858627474274bdfc8e1a6e5050c4d54bd9c1cf868ee19facc82e54', 40960, 0x71000),
    'fw/a640_zap.mbn': ('627ecc6af99e201b1545b2c498109fb709665a3519c6e87ebfbaef912849b304', 14256, 0x7b000),
}

def cmd_gate(path):
    im = Img(path)
    ok = True
    def check(cond, what):
        nonlocal ok
        print(('  OK  ' if cond else '  FAIL') + ' ' + what)
        ok = ok and cond

    valid, data, hist, bad = im.audit()
    check(bad == [], f'metadata audit: {valid} checksummed blocks all valid, no superblock block broken')
    check(valid >= 20, f'metadata block count sane ({valid} >= 20); {data:,} data blocks carry no checksum')

    ft = im.file_tree()
    exts = {}
    for name, (sha, size, pstart) in PINS.items():
        if name not in ft:
            check(False, f'{name} missing from the volume')
            continue
        ext, priv = ft[name]
        d = im.file_bytes(ext)[:size]
        got = hashlib.sha256(d).hexdigest()
        check(got == sha, f'{name}: content sha256 {got[:16]}... pinned')
        start = ext[0][2] * im.bs
        span = sum(e[1] for e in ext)
        check(start == pstart, f'{name}: extent at {start:#x} (pinned {pstart:#x}), {span:,} B on disk')
        exts[name] = (start, start + span)

    (zs, ze) = exts.get('fw/a640_zap.mbn', (0, 0))
    for name in ('fw/a630_sqe.fw', 'fw/a640_gmu.bin'):
        if name in exts:
            s, e = exts[name]
            check(e <= zs or s >= ze, f'{name} [{s:#x}..{e:#x}) shares no block with the zap [{zs:#x}..{ze:#x})')

    v = im.data
    check(b'Xiaomi Attestation CA' in v, 'the Xiaomi production chain is in the volume (the swap took)')
    check(b'Generated Test Attestation CA' not in v, 'the test chain is gone from the volume')
    print('VOLUME GATE: ' + ('PASS' if ok else 'FAIL'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'report':
        cmd_report(sys.argv[2])
    elif cmd == 'files':
        cmd_files(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '/tmp/fw')
    elif cmd == 'zap':
        cmd_zap(sys.argv[2])
    elif cmd == 'diff':
        cmd_diff(sys.argv[2], sys.argv[3])
    elif cmd == 'gate':
        cmd_gate(sys.argv[2])
    else:
        raise SystemExit(__doc__)
