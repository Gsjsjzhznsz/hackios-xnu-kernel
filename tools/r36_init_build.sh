#!/bin/bash
# tools/r36_init_build.sh -- build the R36 sbin/init (the J3 fix) from the pinned userland.
#
# R36's ONE variable is init: the R35 log proved Metal's discovery composes
# /System/Library/Extensions/<MetalPluginName>.bundle on the ROOT and the link riding in the
# small image's own /redwood/System/Library/Extensions was never reached by it (the J3 of
# that round's tree).  The fix rides in userland/src/init.c (pin 6f04967, the a5ef0c3 init +
# rc.jobs.r36metal): flash_root union-binds this image's Extensions dir over the iOS volume's
# (MNT_RDONLY | MNT_UNION -- the shape /jb-bin over /flash/bin has ridden since R28i, and the
# r35 device log itself shows the sibling binds of flash_root working), so Apple's AGX bundles
# stay visible BESIDE ours.
#
# This is userland/build.sh's own init compile line, verbatim (its src/ + libmini/, this tree's
# xnu-clang): a freestanding static arm64 Mach-O, no SDK needed -- the same compiler the kernel
# build pins (XNU_HOST_CLANG), the same wrapper xnu-deps/setup.sh laid down (host/bin/xnu-clang).
#
#   obj/mini.o   <- $CC -Ilibmini -c libmini/mini.c
#   obj/start.o  <- $CC -c libmini/start.S
#   r36-init/init <- $CC -Ilibmini $LD -o ... src/init.c obj/mini.o obj/start.o
#
# Gates, from the bytes alone:
#   - arm64 Mach-O (an ELF means the toolchain went astray)
#   - the fix's own strings are IN ("our Metal plug-in beside iOS's own..." -- the check()
#     description flash_root prints; "bindfs union %s on %s: %s" -- the failure printf)
#   - neither string is in the R35-lineage init (R36_OLD_INIT, the path of the sbin/init
#     extracted from the R35 volume -- the volume build extracts it) -- the strings being
#     absent there is what proves this init is a DIFFERENT init, not the old one relabelled
#
# usage: tools/r36_init_build.sh [tree]        (default: this repository)
#   R36_OUT=... (default r36-init)   R36_OLD_INIT=... (the R35-lineage sbin/init, for the
#   absent-string gate; if set, its absence of the fix strings is asserted)
set -eu
TREE=$(cd "${1:-$(dirname "$0")/..}" && pwd)
cd "$TREE"
R36_OUT=${R36_OUT:-r36-init}
USERLAND_URL=${USERLAND_URL:-https://github.com/Gsjsjzhznsz/hackios-xnu-userland.git}
USERLAND_PIN=${USERLAND_PIN:-6f04967}

# ---- the userland checkout (the same arrangement as r35_gl_metal_build.sh's, at this round's pin)
if [ ! -d .userland.git ] || [ "$(git --git-dir=.userland.git rev-parse HEAD 2>/dev/null)" != "$USERLAND_PIN" ]; then
        rm -rf .userland.git
        tmp=$(mktemp -d)
        git clone --no-checkout "$USERLAND_URL" "$tmp/u"
        mv "$tmp/u/.git" .userland.git
        rm -rf "$tmp"
        UG=(git --git-dir="$TREE/.userland.git" --work-tree="$TREE")
        "${UG[@]}" config core.bare false
        "${UG[@]}" config core.worktree "$TREE"
        "${UG[@]}" config status.showUntrackedFiles no
        cp common/userland.exclude .userland.git/info/exclude 2>/dev/null || true
        # README.md, .gitignore and LICENSE at the top are the kernel repository's (skip-worktree),
        # every other file of the pin is written out
        "${UG[@]}" read-tree -m "$USERLAND_PIN"
        for f in README.md .gitignore LICENSE; do
                "${UG[@]}" ls-files --error-unmatch "$f" > /dev/null 2>&1 && "${UG[@]}" update-index --skip-worktree "$f"
        done
        "${UG[@]}" ls-files -z | grep -zvxE 'README\.md|\.gitignore|LICENSE' | xargs -0 "${UG[@]}" checkout --
fi
GOT=$(git --git-dir=.userland.git rev-parse HEAD)
[ "$GOT" = "$USERLAND_PIN" ] || { echo "::error::userland pin drift: $GOT != $USERLAND_PIN"; exit 1; }
echo "→ userland @ $(git --git-dir=.userland.git log -1 --format='%h %cs %s')"
test -f userland/src/init.c || { echo "::error::the pin's tree has no userland/src/init.c"; exit 1; }
test -f userland/rc.jobs.r36metal || { echo "::error::the pin's tree has no rc.jobs.r36metal (the pin predates the R36 round)"; exit 1; }
grep -q "bind_dir_union" userland/src/init.c || { echo "::error::the pin's init.c has no bind_dir_union (not the R36 init)"; exit 1; }

# ---- the toolchain, exactly as the kernel build pins it
source xnu-env.sh
XNU_HOST_CLANG="$THEOS/toolchain/linux/iphone/bin/clang"
export XNU_HOST_CLANG
test -x "$XNU_HOST_CLANG" || { echo "::error::theos 工具链 clang 缺位"; exit 1; }
test -x xnu-deps/host/bin/xnu-clang || { echo "::error::xnu-clang 缺位（先跑 xnu-deps/setup.sh，内核构建步已建）"; exit 1; }

# ---- userland/build.sh's own compile lines, verbatim
CC="xnu-clang -arch arm64 -Os -static -nostdlib -ffreestanding -fno-stack-protector -fno-builtin -Wall -Wextra"
LD="-Wl,-e,_start -Wl,-pagezero_size,0x100000000"
mkdir -p obj "$R36_OUT"
(cd userland && $CC -Ilibmini -c libmini/mini.c -o ../obj/mini.o)
(cd userland && $CC -c libmini/start.S -o ../obj/start.o)
(cd userland && $CC -Ilibmini $LD -o "../$R36_OUT/init" src/init.c ../obj/mini.o ../obj/start.o)

# ---- gates, from the bytes alone
python3 - "$R36_OUT/init" "${R36_OLD_INIT:-}" <<'PY'
import sys

FIX_DESC = b"our Metal plug-in beside iOS's own, where Metal's discovery opens it"
FIX_FAIL = b"bindfs union %s on %s: %s"

p = sys.argv[1]
b = open(p, 'rb').read()
assert b[:4] in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xfe\xed\xfa\xcf'), \
    p + ': not a Mach-O (magic ' + b[:4].hex() + ')'
assert FIX_DESC in b, 'the R36 init does not carry the union check description -- not the fixed init'
assert FIX_FAIL in b, 'the R36 init does not carry the union failure printf -- not the fixed init'
print('r36 init: arm64 Mach-O, %d B; the fix strings are in (desc + the bindfs-union printf)' % len(b))

if len(sys.argv) > 2 and sys.argv[2]:
    old = open(sys.argv[2], 'rb').read()
    assert FIX_DESC not in old and FIX_FAIL not in old, \
        sys.argv[2] + ': the supposed R35-lineage init ALREADY carries the fix strings -- wrong --old-init'
    print('r36 init: distinct from the R35-lineage init (both strings absent there), %d B vs %d B' % (len(b), len(old)))
PY

{
        echo "init from userland pin $USERLAND_PIN, $(date -u +%FT%TZ)"
        sha256sum "$R36_OUT/init"
} > "$R36_OUT/SHA256SUMS"
cat "$R36_OUT/SHA256SUMS"
echo "→ R36 init ready in $R36_OUT/"
