#!/bin/bash
# tools/r34_gl_build.sh -- build the R34 GL-test userland pieces: Mesa (softpipe + freedreno)
# as libOSMesa.8.dylib, and the two programs that link it (gl_test, u2f_test).
#
# This is userland/build.sh's GL=test block, lifted out and run standalone: the full build.sh
# also makes the launchd job list with mkjobs.py --root-volume, which needs ios165/rootfs.slt
# (the listing of THIS phone's iOS volume, kept on the maintainer's machine).  The R13-lineage
# volume already carries that list, judged and baked -- tools/r34_volume_build.py keeps it and
# adds only what this script produces.
#
# The layout is the workspace arrangement of common/setup.sh: the userland repository lives in
# THIS tree (its tools/ beside this file's), checked out at a pinned commit (the round's pin,
# asserted).  Everything downstream refers to paths relative to the tree root, exactly as
# userland/build.sh does.
#
# Outputs into $GL_OUT (default r34-gl/):  libOSMesa.8.dylib, gl_test, u2f_test, SHA256SUMS.
#
# Needs: the cross toolchain (xnu-deps/host/bin + theos iPhoneOS16.5.sdk, as for the kernel),
# meson, ninja, bison, flex, llvm-ar (apt), python3 venv for Mako (mk-mesa.sh makes it).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

USERLAND_URL=${USERLAND_URL:-https://github.com/Gsjsjzhznsz/hackios-xnu-userland.git}
USERLAND_PIN=${USERLAND_PIN:-6832a54fee2c9605a141c2aa7bd9ab5f40b93f69}
GL_OUT=${GL_OUT:-r34-gl}
MESA_OUT=xnu-deps/build/mesa/out

# ---- the userland checkout (setup.sh's arrangement, at the pin)
if [ ! -d .userland.git ]; then
        echo "→ userland checkout: $USERLAND_URL @ ${USERLAND_PIN:0:12}"
        tmp=$(mktemp -d)
        git clone --no-checkout "$USERLAND_URL" "$tmp/u"
        mv "$tmp/u/.git" .userland.git
        rm -rf "$tmp"
        UG=(git --git-dir="$ROOT/.userland.git" --work-tree="$ROOT")
        "${UG[@]}" config core.bare false
        "${UG[@]}" config core.worktree "$ROOT"
        "${UG[@]}" config status.showUntrackedFiles no
        cp common/userland.exclude .userland.git/info/exclude
        # README.md, .gitignore and LICENSE at the top are the kernel repository's (skip-worktree),
        # every other file of the pin is written out -- common/setup.sh's own dance
        "${UG[@]}" read-tree -m "$USERLAND_PIN"
        for f in README.md .gitignore LICENSE; do
                "${UG[@]}" ls-files --error-unmatch "$f" > /dev/null 2>&1 && "${UG[@]}" update-index --skip-worktree "$f"
        done
        "${UG[@]}" ls-files -z | grep -zvxE 'README\.md|\.gitignore|LICENSE' | xargs -0 "${UG[@]}" checkout --
fi
GOT=$(git --git-dir=.userland.git rev-parse HEAD)
[ "$GOT" = "$USERLAND_PIN" ] || { echo "::error::userland pin drift: $GOT != $USERLAND_PIN (bump the pin consciously)"; exit 1; }
echo "→ userland @ $(git --git-dir=.userland.git log -1 --format='%h %cs %s')"

# ---- the host pieces mk-mesa.sh needs
if ! command -v meson > /dev/null || ! command -v ninja > /dev/null; then
        sudo apt-get install -y -qq meson ninja-build
fi
if ! command -v llvm-ar > /dev/null; then
        AR=$(ls /usr/lib/llvm-*/bin/llvm-ar 2>/dev/null | sort -V | tail -1 || true)
        [ -n "$AR" ] || AR=$(command -v llvm-ar-19 || command -v llvm-ar-18 || true)
        [ -n "$AR" ] || { echo "::error::no llvm-ar on this runner (apt install llvm)"; exit 1; }
        sudo ln -sf "$(readlink -f "$AR")" /usr/local/bin/llvm-ar
fi
command -v bison > /dev/null && command -v flex > /dev/null || sudo apt-get install -y -qq bison flex
echo "→ meson $(meson --version), ninja $(ninja --version), llvm-ar: $(command -v llvm-ar)"

# ---- the toolchain, exactly as the kernel build pins it
source xnu-env.sh
XNU_HOST_CLANG="$THEOS/toolchain/linux/iphone/bin/clang"
export XNU_HOST_CLANG
test -x "$XNU_HOST_CLANG" || { echo "::error::theos 工具链 clang 缺位"; exit 1; }
test -d "$THEOS/sdks/iPhoneOS16.5.sdk/usr/include" || { echo "::error::iPhoneOS16.5.sdk 缺位"; exit 1; }

# ---- Mesa: one pinned release, the tree's own patches, softpipe + freedreno (DRIVERS default)
# R34 run 40612388705 的教训：mk-mesa.sh 的 curl -sL --fail 静默失败时整段无声死掉
# （本地 dry-run 有缓存所以绿，CI 首下就撞）。这里先把源码包按 mk-mesa.sh 自己钉死的
# 版本/SHA 可见地预取好——mk-mesa.sh 见文件在就跳过它的静默下载。
MESA_VER=$(sed -n 's/^VERSION=//p' tools/mk-mesa.sh | head -1)
MESA_SHA=$(sed -n 's/^SHA256=//p' tools/mk-mesa.sh | head -1)
mkdir -p "$MESA_OUT"
if [ -n "$MESA_VER" ] && [ -n "$MESA_SHA" ] && [ ! -f "xnu-deps/build/mesa/mesa-$MESA_VER.tar.xz" ]; then
        echo "→ 预取 mesa-$MESA_VER.tar.xz（可见进度，重试 3 次）"
        curl -fL --retry 3 --retry-delay 5 --connect-timeout 30 \
                -o "xnu-deps/build/mesa/mesa-$MESA_VER.tar.xz" \
                "https://archive.mesa3d.org/mesa-$MESA_VER.tar.xz"
fi
if [ -n "$MESA_VER" ] && [ -n "$MESA_SHA" ] && [ -f "xnu-deps/build/mesa/mesa-$MESA_VER.tar.xz" ]; then
        echo "$MESA_SHA  xnu-deps/build/mesa/mesa-$MESA_VER.tar.xz" | sha256sum -c -
fi
if [ ! -f "$MESA_OUT/libOSMesa.8.dylib" ]; then
        echo "→ building Mesa (softpipe,freedreno) -- the first build takes minutes"
        tools/mk-mesa.sh
fi
test -f "$MESA_OUT/libOSMesa.8.dylib" || { echo "::error::Mesa produced no libOSMesa.8.dylib"; exit 1; }
echo "→ Mesa: $(stat -c %s "$MESA_OUT/libOSMesa.8.dylib") B, built-from $(cut -c1-12 "$MESA_OUT/built-from" 2>/dev/null || echo '?')"

# ---- gl_test and u2f_test: build.sh's own two link lines (GL=test shape: NO libredwood-gl.dylib)
DYN="xnu-clang -arch arm64 -isysroot $THEOS/sdks/iPhoneOS16.5.sdk -O1"
mkdir -p "$GL_OUT"
$DYN -framework CoreFoundation -framework IOKit "$MESA_OUT/libOSMesa.8.dylib" -o "$GL_OUT/gl_test" userland/src/gl_test.c
$DYN "$MESA_OUT/libOSMesa.8.dylib" -o "$GL_OUT/u2f_test" userland/src/u2f_test.c
cp "$MESA_OUT/libOSMesa.8.dylib" "$GL_OUT/libOSMesa.8.dylib"
file "$GL_OUT/gl_test" "$GL_OUT/u2f_test" | sed 's/^/  /'
{
        echo "Mesa from tools/mesa patches $(cat "$MESA_OUT/built-from" 2>/dev/null | cut -c1-16)..., userland pin $USERLAND_PIN, $(date -u +%FT%TZ)"
        sha256sum "$GL_OUT/libOSMesa.8.dylib" "$GL_OUT/gl_test" "$GL_OUT/u2f_test"
} > "$GL_OUT/SHA256SUMS"
cat "$GL_OUT/SHA256SUMS"
echo "→ r34 GL pieces ready in $GL_OUT/"
