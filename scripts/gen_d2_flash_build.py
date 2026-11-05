"""scripts/gen_d2_flash_build.py -- clone tools/d1_flash_build.py into tools/d2_flash_build.py.

D2 rides on the D1 release as its base: the base GPU image sha and the base
kernel entry sha are the only two constants that move.  Everything else --
every law, every assertion, the D1 marker, the verbatim R39 cmdline -- stays
byte for byte, because D2 does not touch the build machinery at all.
"""
import hashlib
import sys

SRC = 'tools/d1_flash_build.py'
DST = 'tools/d2_flash_build.py'

D1_GPU = 'a4f1c330622acbe8bcc922e66eb1ef3a1b917d1af9c9d0e92d0ae929fdb81a45'
D1_KERNEL = '85448240dab17fdebe11929d25de3a74c1211209f3f4e2c5fac1d423985c199f'


def sub1(src, old, new, why):
    n = src.count(old)
    assert n == 1, '%s: %d hits (want exactly 1)' % (why, n)
    return src.replace(old, new)


def main():
    src = open(SRC, 'rb').read().decode()
    assert hashlib.sha256(open(SRC, 'rb').read()).hexdigest(), 'unreadable'

    src = sub1(src,
               'tools/d1_flash_build.py -- pack the d1 GPU image: the r61 release bytes with ONE entry swapped.',
               'tools/d2_flash_build.py -- pack the d2 GPU image: the d1 release bytes with ONE entry swapped.',
               'the header line')
    src = sub1(src,
               "R61_GPU = '39c6e70e430e424b93f6cfe40d2c7d264c3bf2dde0813087fdd48aa559f99ea1'",
               "R61_GPU = '%s'" % D1_GPU,
               'the base GPU image sha (the d1 release)')
    src = sub1(src,
               "R61_KERNEL = '8ef12fefbb2edc1cafdb0bb38bca4d0bb6adffee1771fdd333efc9c77b7b5ae7'",
               "R61_KERNEL = '%s'" % D1_KERNEL,
               'the base kernel entry sha (the D1 kernel)')
    src = sub1(src,
               "print('gpu base: loader 4bf828e4, kernel 8ef12fef, volume 5b9bbbcb, cmdline = the R39 token set verbatim')",
               "print('gpu base: loader 4bf828e4, kernel 85448240 (D1), volume 5b9bbbcb, cmdline = the R39 token set verbatim')",
               'the base banner')

    open(DST, 'wb').write(src.encode())
    print('wrote %s (%d B, sha256 %s)' % (DST, len(src.encode()),
                                         hashlib.sha256(src.encode()).hexdigest()))


if __name__ == '__main__':
    sys.exit(main())
