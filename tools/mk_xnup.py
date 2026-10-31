#!/usr/bin/env python3
"""mk_xnup.py —— 从 gpu flash 镜像提取 XNUP 裸包（R48 律③：gzip 解压 + 切到 XNUP 魔数起点）

用法:
  mk_xnup.py build <gpu-flash.img> <out.xnup>
  mk_xnup.py verify <gpu-flash.img> <expected.xnup>   # 逐字节比对（刷前字节校验律）

提取法与 tools/r54_flash_build.py unpack() 逐位同源:
  ANDROID! 头 → kernel 段（page=4096, k_size@8）→ gzip.decompress → find(b'XNUP') → 尾切。
"""
import sys, struct, gzip, hashlib


def extract(gpu_img_path):
    img = open(gpu_img_path, 'rb').read()
    assert img[:8] == b'ANDROID!', gpu_img_path + ': not an Android boot image'
    page = struct.unpack_from('<I', img, 36)[0]
    assert page == 4096, 'page size %d' % page
    k_size = struct.unpack_from('<I', img, 8)[0]
    k_off = page
    blob = gzip.decompress(img[k_off:k_off + k_size])
    x = blob.find(b'XNUP')
    assert x > 0, 'no XNUP payload (x=%d)' % x
    payload = blob[x:]
    # payload 头自证：magic + size 自洽
    magic, size = struct.unpack_from('<II', payload, 0)
    assert magic == 0x50554E58, 'bad payload magic %08x' % magic
    assert 8 + size <= len(payload), 'payload size %d overruns %d' % (size, len(payload))
    return payload[:8 + size]  # 精确切到 XNUP 声明终点（尾部去 gzip 流残料）


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    mode, img_path = sys.argv[1], sys.argv[2]
    payload = extract(img_path)
    print('payload: %d B  sha256 %s' % (len(payload), sha(payload)))
    if mode == 'build':
        out = sys.argv[3]
        open(out, 'wb').write(payload)
        print('wrote %s' % out)
    elif mode == 'verify':
        exp = open(sys.argv[3], 'rb').read()
        if payload == exp:
            print('VERIFY: BYTE-IDENTICAL (%d B) — 刷前字节校验 ✓' % len(exp))
        else:
            print('VERIFY: MISMATCH mine=%d B expected=%d B' % (len(payload), len(exp)))
            print('  mine     %s' % sha(payload))
            print('  expected %s' % sha(exp))
            return 1
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
