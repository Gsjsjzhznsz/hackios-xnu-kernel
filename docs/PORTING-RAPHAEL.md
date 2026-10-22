# Porting HackiOS to raphael (Xiaomi Mi 9T Pro / K20 Pro, SM8150)

This port follows [docs/PORTING.md](PORTING.md) with one addition upstream does not need:
**raphael's ABL only accepts Android boot images with a v2 header** (see "The v2 law" below).
The port was started from the raphael knowledge base of the earlier `ios-raphael` project
(XNU booted directly from ABL through a shim), which already proved the boot chain, the
real device tree, the memory geometry and the 8-core configuration on this phone.

## What raphael is, next to redwood

| | redwood (POCO X5 Pro 5G) | raphael (Mi 9T Pro / K20 Pro) |
|---|---|---|
| SoC | SM7325 (Snapdragon 778G), `KodiakPkg` | SM8150 (Snapdragon 855), `HanaPkg` |
| Nearest upstream UEFI | `lisaPkg` | `cepheusPkg` (Mi 9, same SoC) - not yet upstream itself |
| Cores | 8: 4x A55 + 4x A78 | 8: 4x A55 (cap 1024) + 3x A76 + 1x A76 Prime (cap 1740) |
| GIC v3 | dist 0x17A00000 / redist 0x17A60000 | identical (read from the phone's DTB) |
| MPIDR | second affinity field (shift 8) | identical (`cpu@0x100`-style numbering) |
| Display | 1080x2400, fb at 0xE1000000 | 1080x2340 AMOLED, fb at 0x9C000000 (cepheus map) |
| Touch | Goodix GT9916, bit-banged SPI | Goodix GT9886, I2C (different bus - own driver needed) |
| GPU | Adreno 642L (a6xx) | Adreno 640 (a6xx) - same freedreno family |
| Wi-Fi | WCN6750, ath11k | WCN3998, ath10k family - later stage, `WIFI_ATH11K=0` |
| PMIC | PM8150B fg-gen4 hardware fuel gauge (patch 0096) experience applies directly |

The 4+4 topology, the GIC addresses, the core numbering and the little-cluster mask are
the same as redwood's, which is why the loader's `struct platform` carries over almost
unchanged; what differs is the framebuffer base (the loader tells devices apart by it),
the UEFI memory map and the boot image header law.

## What is already in this tree

- `common/devices/raphael.conf` - the device profile. FD 0x9FC00000/3 MB, payload 64 MB at
  0xF0000000 (both matching the memory map below), `BOOTIMG_HEADER_VERSION=2`,
  `FB_BASE=0x9C000000`, `BOOTARGS="touchscreen=0 cpus=8"` for first bring-up.
- `common/devices/raphael.platform.c` + `platform_raphael` in `xnuloader/loader.c`
  (and `&platform_raphael` in `platforms[]`). Every value is measured, not guessed:
  GIC/cores/shift/small-cluster were parsed from the phone's real 499,823-byte vendor_boot
  DTB with the same rules `common/new-device.sh` applies.
- `uefi/files/Platforms/Xiaomi/raphaelPkg/` - the package itself: `HanaPkg` (SM8150)
  include, SMBIOS "Mi 9T Pro"/raphael, framebuffer 1080x2340x32, ACPI tables from
  `cepheus/AcpiTables.inf` (Silicium-ACPI), `DeviceBuild.py` retargeted, `SOC_TYPE = 0`
  (SM8150-AB).
- `uefi/files/Platforms/Xiaomi/raphaelPkg/Include/*.inc` - rebased **verbatim on upstream
  `cepheusPkg`** (the authoritative, Windows-booting SM8150 set at our Mu-Silicium BASE):
  the stage-1 versions had been shaped on redwood's lisa-derived lists and referenced 14
  drivers that do not exist anywhere in SM8150 firmware (FeatureEnabler/GLink/IPCC/
  PmicGlink/Xhci* and friends - those live in xbl_config on newer SoCs, not on SM8150's
  xbl), while missing AdcDxe/CipherDxe/HashDxe/PdcDxe. Plus three raphael-new raw entries
  (two Panel_K82 dualdsi panels, `SUNWODA.K82.BATTERY.PROVISION`) with their real FFS GUIDs.
- `uefi/files/Platforms/Xiaomi/raphaelPkg/Library/MemoryMapLib/MemoryMapLib.c` - the
  cepheus map plus one entry: `{"XNU Payload", 0xF0000000, 0x04000000}` (free RAM in the
  cepheus map: nothing is defined between the DXE Heap end 0xC4800000 and 4 GB).
- `uefi/files/Binaries/raphael/` - **raphael's own firmware**, extracted from the phone's
  real `xbl.img` (7 MiB ELF; 3 MB UEFI FV at `0x9FC00000`, inner DXE FV gzip-wrapped in the
  `1d301fe9-…` GUIDed section - the WOA-Project/UEFIReader approach, reimplemented in an
  FFS walker). 77 PE drivers + depexes + 55 raw files, all matched by FFS GUID. Four of
  Silicium's usual binary patches re-applied semantically onto the raphael builds
  (ButtonsDxe keycode, DALSYSDxe UFS cache coherence, ClockDxe DAL-dispatcher stub,
  UsbConfigDxe deinit removal); the cepheus set's rebuilt DisplayDxe is kept unreferenced
  as `DisplayDxe.cepheus-patched.efi` fallback - see `files/Binaries/raphael/README.md`.
- `uefi/files/Resources/DTBs/raphael.dtb` - the phone's real device tree (from the
  vendor_boot of the t12 golden image, the exact DTB the ABL chain booted).
- `uefi/files/Resources/Configs/raphael.toml` - `header_version = 2`.
- `mkxnuimg.py` - learned `BOOTIMG_HEADER_VERSION` (default 1 = redwood unchanged): when 2,
  it packs the kernel segment as `gzip(shim+FD+payload)` only, puts the DTB in its own
  segment at `BOOTIMG_DTB_ADDR` (default 0x1F00000) and truncates the file exactly at the
  DTB's end. Verified against the golden t12 image byte-for-byte in the earlier project.

## The v2 law (raphael's ABL)

Upstream `mkxnuimg.py` packs `--header_version 1` with the DTB appended to the gzipped
kernel segment; redwood's ABL accepts that. **raphael's ABL does not** - a v1 image boots
to a black screen with the phone's splash intact, three independent builds proved it
(t8, t13, t19; the same failure in the earliest days). The ABL only accepts v2:

- independent DTB segment, `dtb_addr` = 0x1F00000, `base` 0, pagesize 4096,
  `cmdline "console=null"`, `os_version 11.0.0`, `os_patch_level 2021-10`;
- the file is truncated exactly at the end of the DTB segment.

This exact chain was proven byte-identical by rebuilding a golden raphael image with it
(20 bytes of `id[]` accounting fields apart, which the ABL does not read). `mkxnuimg.py`
implements it under `BOOTIMG_HEADER_VERSION=2`; redwood keeps its v1 path untouched.

## Stage plan

1. **UEFI brings up** - build raphaelPkg (`build_uefi.py -d raphael -r RELEASE`), flash
   nothing, `fastboot boot` the packed image. First milestone per PORTING.md: the UEFI's
   own display (SimpleFb at 0x9C000000) and the loader's screen over USB (`./xl log`).
   The conservative bits: the loader does not arm its virtual watchdog on raphael yet
   (`PLAT != &platform_redwood` paths treat it like QEMU until the SMC is verified), and
   `charger_start` stays off (charging is known-broken upstream anyway).
2. **Kernel verbose** - the prebuilt redwood kernel (BCM2837 base + platform code) should
   print `-v` boot text on the UEFI framebuffer; raphael-specific drivers (Goodix SPI)
   simply will not probe, which is fine for this stage. Then build the kernel from
   `xnu/` with the same toolchain as upstream.
3. **Userland** - `ios165/fetch.sh` (7.4 GB from Apple's CDN), `REST=1 userland/build.sh`;
   root.apfs in the payload (max 64 MB at 0xF0000000 - watch the payload size assertion).
4. **Lock screen** - the first visual milestone: SpringBoard on the AMOLED.
5. **Touch** - write `bsd/dev/raphael_goodix.c` (GT9886 over I2C) feeding
   `/dev/redwood-touch`; the earlier project's GT9886 driver (config send, coordinate
   parsing, 0x4009-style register access) is the reference implementation.
6. **The rest of the phone** - buttons/cpufreq/battery (PM8150B fg-gen4 - the earlier
   project has a complete hardware fuel-gauge implementation to port), then GPU
   (Adreno 640 + Mesa), sound, and Wi-Fi last (WCN3998 is ath10k, not ath11k - a
   different LinuxKPI front end).

## Open items

- ~~raphael's own `xbl.img` (from its fastboot ROM) to replace the cepheus binaries~~ -
  **DONE**: the owner's actual `xbl.img` is the source of the whole binary set now;
  nothing of cepheus is referenced anymore.
- ~~the five usual Silicium binary patches~~ - **DONE**: four ported onto the raphael
  builds (see `files/Binaries/raphael/README.md`); DisplayDxe's cepheus patch is a
  rebuild and stays as an unreferenced fallback (and is dormant anyway while
  `USE_CUSTOM_DISPLAY_DRIVER = 0`).
- **UEFI builds and packs (stage-1 milestone image r1)** - `raphaelPkg` compiles clean
  (RELEASE_CLANGPDB, FD 3 MB, 1.69 MB used, 83 PE modules; provenance proven: the FD's
  UFSDxe is byte-identical to the user's XBL module, ButtonsDxe/ClockDxe are the
  ported-patched raphael builds). Canonical image = `mkxnuimg.py` v2 path
  (`raphael-uefi-r1.img`, sha256 `91a9b55f…74e1844`). Toolchain notes: conda-forge
  clang-18/lld/nasm/mono (no root needed); `mu_nasm` extdep pre-seeded with system
  nasm per `uefi/README.md`; Mu-Silicium lives at the repo ROOT (`uefi/apply.sh`
  clones `../Mu-Silicium`), where `mkxnuimg.py` expects it. `raphael.toml` carries the
  full v2 argument set (dtb/dtb_offset/base `"0x0"`/cmdline/os_version) - mind that
  `build_uefi.py`'s `if value:` drops a numeric 0 and its argument map has no
  `pagesize`, so `Mu-raphael.img` from that path is v2-with-defaults (2048-byte
  pages, padded tail); the byte-exact law lives in `mkxnuimg.py`.
- on-device stage-1 test: `fastboot boot raphael-uefi-r1.img` (see
  `download/raphael-hackios-r1/TEST-RAPHAEL-UEFI-R1.md` for the judgment tree);
- the loader's virtual-watchdog SMCs on raphael (kept off in stage 1);
- `FD_BASE`/`FD_SIZE` in the profile match `MemoryMapLib`'s "UEFI FD" entry
  (0x9FC00000/0x300000) - `mkxnuimg.py` asserts the FD length, so a mismatch fails loudly.
