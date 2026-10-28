#!/bin/bash
# tools/r35_gl_metal_build.sh -- build the R35 GL+Metal userland pieces.
#
# R35 is the Metal round.  R34 proved the GL-test stack (Mesa freedreno over the kernel's
# RedwoodGPU service: gl_test's triangle on the panel, the device's own record).  R35 adds,
# still with R33's kernel bytes VERBATIM:
#
#   - the GL=1 library: lib/libredwood-gl.dylib (src/eagl.c + src/gl_osmesa.c over the same
#     libOSMesa.8.dylib) -- the table of functions that hands CoreAnimation's calls to Mesa.
#     The kernel's injector reads it from etc/inject.conf and puts it inside backboardd,
#     whose access gate (src/inject.c gpu_draws()) opens on the RedwoodGPU service -- the
#     road "the system draws with the GPU" takes this round.
#
#   - the Metal discovery chain's own pieces (userland/docs, metal.md sections 1 and 10):
#       lib/RedwoodMetal.bundle/{RedwoodMetal,Info.plist}   the driver as a BUNDLE -- the
#                           form Metal's step 3 opens; the dylib form CANNOT serve here,
#                           because dyld uniques images by install_name and a process that
#                           already holds /redwood/lib/libredwoodmetal.dylib would bind the
#                           bundle to it and fail the class-identity test (metal_plugin.plist
#                           carries the measured reason)
#       bin/metal_props     the switch: MetalPluginName / MetalPluginClassName onto the
#                           IOAcceleratorES service (kernel must be booted with metal=1)
#       bin/metal_test      the five steps of the chain walked on the record, plus CoreImage
#       bin/metal_drv_test  every class against every method its protocols require (--sweep)
#
# Deliberately NOT built here: libredwoodmetal.dylib (the dylib twin of the bundle -- with it
# in the volume, dyld's install-name uniquing breaks the bundle road above) and
# libredwoodair.dylib (the AIR compiler the Metal compute path dlopens; its absence is a
# sentence the driver prints, not a crash -- the compute round is a later round).
#
# This is userland/build.sh's GL=test + GL=1 + Metal blocks lifted out and run standalone:
# the full build.sh also makes the launchd job list with mkjobs.py --root-volume, which needs
# ios165/rootfs.slt (the listing of THIS phone's iOS volume, kept on the maintainer's machine).
# The R34-lineage volume already carries that list, judged and baked -- tools/r35_volume_build.py
# keeps it and adds only what this script produces.
#
# The layout is the workspace arrangement of common/setup.sh: the userland repository lives in
# THIS tree (its tools/ beside this file's), checked out at a pinned commit (the round's pin,
# asserted).  Everything downstream refers to paths relative to the tree root, exactly as
# userland/build.sh does.
#
# Outputs into $GLM_OUT (default r35-glm/):  libOSMesa.8.dylib, gl_test, u2f_test,
#   libredwood-gl.dylib, RedwoodMetal, Info.plist, metal_props, metal_test, metal_drv_test,
#   SHA256SUMS.
#
# Needs: the cross toolchain (xnu-deps/host/bin + theos iPhoneOS16.5.sdk, as for the kernel),
# meson, ninja, bison, flex, llvm-ar (apt), python3 venv for Mako (mk-mesa.sh makes it).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

USERLAND_URL=${USERLAND_URL:-https://github.com/Gsjsjzhznsz/hackios-xnu-userland.git}
USERLAND_PIN=${USERLAND_PIN:-de5ef5892f88e79945d42c70c14bfbd2dce2611d}
GLM_OUT=${GLM_OUT:-r35-glm}
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
test -f userland/rc.jobs.r35metal || { echo "::error::the pin's tree has no rc.jobs.r35metal (the pin predates the Metal round)"; exit 1; }

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
if [ ! -f "$MESA_OUT/libOSMesa.8.dylib" ]; then
        echo "→ building Mesa (softpipe,freedreno) -- the first build takes minutes"
        tools/mk-mesa.sh
fi
test -f "$MESA_OUT/libOSMesa.8.dylib" || { echo "::error::Mesa produced no libOSMesa.8.dylib"; exit 1; }
echo "→ Mesa: $(stat -c %s "$MESA_OUT/libOSMesa.8.dylib") B, built-from $(cut -c1-12 "$MESA_OUT/built-from" 2>/dev/null || echo '?')"

# ---- the compile lines: userland/build.sh's own, verbatim
DYN="xnu-clang -arch arm64 -isysroot $THEOS/sdks/iPhoneOS16.5.sdk -O1"
mkdir -p "$GLM_OUT" obj

# (1) GL=test's three, exactly as R34 shipped them
$DYN -framework CoreFoundation -framework IOKit "$MESA_OUT/libOSMesa.8.dylib" -o "$GLM_OUT/gl_test" userland/src/gl_test.c
$DYN "$MESA_OUT/libOSMesa.8.dylib" -o "$GLM_OUT/u2f_test" userland/src/u2f_test.c
cp "$MESA_OUT/libOSMesa.8.dylib" "$GLM_OUT/libOSMesa.8.dylib"

# (2) GL=1's library: the function table CoreAnimation calls, over the same Mesa.
#     mkeagl.py generates the dispatch table's header from the slots file and Mesa's own
#     gl.xml / exported-gl.txt -- build.sh's exact two lines, in build.sh's order.
./userland/mkeagl.py userland/src/eagl_slots.txt "$MESA_OUT/gl.xml" "$MESA_OUT/exported-gl.txt" \
        userland/src/eagl.c > obj/eagl_gen.h
$DYN -dynamiclib -install_name /redwood/lib/libredwood-gl.dylib -Iobj -framework CoreFoundation \
        -framework IOKit -framework IOSurface -lobjc "$MESA_OUT/libOSMesa.8.dylib" \
        -o "$GLM_OUT/libredwood-gl.dylib" userland/src/eagl.c userland/src/gl_osmesa.c

# (3) The Metal driver as a BUNDLE: build.sh's own line.  -bundle, not -dynamiclib: the
#     bundle's executable must be its own image, or NSBundle's class-identity test fails.
$DYN -bundle -framework Foundation -framework IOKit -framework Metal -lobjc \
        -o "$GLM_OUT/RedwoodMetal" userland/src/redwoodmetal.m userland/src/redwooddrawable.m
cp userland/src/metal_plugin.plist "$GLM_OUT/Info.plist"

# (4) The switch and the two record-takers.  metal_props must NOT itself ask Metal (a
#     program that flips the switch holds only IOKit and CoreFoundation -- build.sh's own
#     remark); metal_test and metal_drv_test are the ones that ask.
$DYN -framework CoreFoundation -framework IOKit -o "$GLM_OUT/metal_props" userland/src/metal_props.c
$DYN -fobjc-arc -Wno-deprecated-declarations -framework Foundation -framework CoreGraphics \
        -framework CoreImage -framework IOKit -framework Metal -framework OpenGLES \
        -lMobileGestalt -o "$GLM_OUT/metal_test" userland/src/metal_test.m
$DYN -framework Foundation -framework IOKit -framework Metal -lobjc \
        -o "$GLM_OUT/metal_drv_test" userland/src/metal_drv_test.m

# ---- every output must be arm64 Mach-O (xnu-clang's target; an ELF means the toolchain went astray)
python3 - "$GLM_OUT" <<'PY'
import sys, os
for n in ('gl_test', 'u2f_test', 'libOSMesa.8.dylib', 'libredwood-gl.dylib', 'RedwoodMetal',
          'metal_props', 'metal_test', 'metal_drv_test'):
        p = os.path.join(sys.argv[1], n)
        assert os.path.isfile(p), p + ': the build produced nothing'
        b = open(p, 'rb').read(16)
        assert b[:4] in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xfe\xed\xfa\xcf'), \
                n + ': not a Mach-O (magic ' + b[:4].hex() + ')'
print('GL+Metal 八件 + Info.plist 都是 Mach-O（arm64）/ 齐备')
PY

{
        echo "Mesa from tools/mesa patches $(cat "$MESA_OUT/built-from" 2>/dev/null | cut -c1-16)..., userland pin $USERLAND_PIN, $(date -u +%FT%TZ)"
        sha256sum "$GLM_OUT/libOSMesa.8.dylib" "$GLM_OUT/gl_test" "$GLM_OUT/u2f_test" \
                  "$GLM_OUT/libredwood-gl.dylib" "$GLM_OUT/RedwoodMetal" "$GLM_OUT/Info.plist" \
                  "$GLM_OUT/metal_props" "$GLM_OUT/metal_test" "$GLM_OUT/metal_drv_test"
} > "$GLM_OUT/SHA256SUMS"
cat "$GLM_OUT/SHA256SUMS"
echo "→ r35 GL+Metal pieces ready in $GLM_OUT/"
