#!/bin/bash
# Build XNU for redwood on Linux. Extra make args are passed through.
source "$(dirname "${BASH_SOURCE[0]}")/xnu-env.sh"
cd "$XNU_SRC" || exit 1
# The Wi-Fi driver's object (tools/mk-ath11k.sh) is linked into the kernel when it is there; make
# does not see it change, so the linked kernel is thrown away when it has.
LX=${WIFI_OBJECT:-$XNUDEPS/build/ath11k/out/ath11k.o}
STAMP=BUILD/obj/redwood-objects
if [ -f "$LX" ]; then now="$LX $(stat -c %Y "$LX")"; else LX= now=; fi
if [ -d BUILD/obj ] && [ "$(cat $STAMP 2>/dev/null)" != "$now" ]; then
	rm -f BUILD/obj/*/mach.*.unstripped
	echo "$now" > $STAMP
fi
# Produce the bootable Mach-O; Apple's extra KDK symbol sets require unimplemented DriverKit RPCs
# (kext_create_symbol_set errors on them -- the R38 round's own forensics: 222 "exported name not
# in import list" for IOService::_Invoke family the base kernel never carried; the reference repo
# pins the goal for exactly this reason).
exec make .DEFAULT_GOAL=build REDWOOD_OBJECTS="$LX" SDKROOT=iphoneos ARCH_CONFIGS=ARM64 MACHINE_CONFIGS=${MACHINE:-BCM2837} KERNEL_CONFIGS=DEVELOPMENT \
  ARCH_STRING_FOR_CURRENT_MACHINE_CONFIG=arm64 BUILD_WERROR=0 BUILD_LTO=0 DO_CTFMERGE=0 \
  HOST_CC="clang -include $XNUDEPS/hostinc/hostcompat.h -I$XNUDEPS/hostinc -Wno-unused-command-line-argument -Wno-error=implicit-function-declaration" HOST_FLEX=flex HOST_BISON=bison HOST_GM4=m4 CC=xnu-clang CXX=xnu-clang++ LOGCOLORS=n "$@"
