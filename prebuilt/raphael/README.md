# prebuilt/raphael — the raphael (K20 Pro / SM8150) boot chain, hardware-proven

Every file here is the exact artifact that already booted on the phone, kept by its
sha256 prefix so no future rebuild can silently swap it:

| file | bytes | sha256 (prefix) | proven on |
|---|---|---|---|
| `SILICIUM_UEFI-raphael.fd` | 3,145,728 | `0c47bdebf5ebb473` | r1: UEFI from the phone's own XBL, DXE → BDS on hardware |
| `BootShim.bin` | 176 | `310db2f5a44c9726` | r2: FD 0x9FC00000+0x300000, XNUP → 0xF0000000, byte-identical to the golden chain |
| `loader.efi` | 68,608 | `9800946a19dfc987` | r3/r4: xnuloader with the ufs=2 block service + platform_raphael (loader stays on core 0, kernel on core 7) |
| `raphael.dtb` | 499,823 | `2a1ec87b98bf1c63` | r1+: the golden raphael DTB (also `t19-materials/soc-dtb.bin`) |

`tools/r5-flash-build.sh` packs these with `prebuilt/mach.development.bcm2837` (the
hackios kernel, Darwin 22.6.0) and `userland/root.apfs` (the flash-boot volume built by
`userland/build.sh PREBUILT=1 FLASHBOOT=1 CRYPTEXES=flash`) into the v2 boot image for
`fastboot boot`, and verifies the result against the provenance above.
