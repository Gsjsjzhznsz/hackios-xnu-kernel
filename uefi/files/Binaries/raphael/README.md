## Firmware Infos

- **Device:** Xiaomi Mi 9T Pro / Redmi K20 Pro (raphael)
- **Source:** the phone's own `xbl.img` (7 MiB ELF, uploaded by the device owner,
  dated 2024-05), parsed with an FFS walker equivalent to
  [UEFIReader](https://github.com/WOA-Project/UEFIReader): the 3 MB UEFI FV at
  `0x9FC00000` (ELF segment), inner DXE FV inside the gzip'd
  `1d301fe9-…` GUIDed section. 77 PE drivers + 55 raw files extracted, every
  module matched to this tree by its FFS GUID.
- **What is here:** raphael's own drivers, depex sections and raw files; the
  `SecParti.cfg` / `uefipil.cfg` / battery provisions / panel XML set the XBL
  actually ships (superset of the old cepheus set by two K82 dualdsi panels and
  `SUNWODA.K82.BATTERY.PROVISION`).
- **Layout:** unchanged from the Silicium `Device-Binaries` shape
  (`QcomPkg/Drivers/<Name>/{<Name>.efi,<Name>.patched.efi,<Name>.depex,<Name>.inf}`);
  each `<Name>.efi` is the pristine raphael firmware, the INF decides which one
  the build takes.

## Patches/Fixes

Four of Silicium's usual binary patches are re-applied to the raphael binaries
(the cepheus set shipped them pre-applied; they are ported semantically here,
each anchor-verified against the raphael build):

### ButtonsDxe

- **Reason:** To make the Power Button usable in UEFI.
- **Patch:** The Special Qcom Key Code (`0x102`) has been Changed to the Key
  Code Enter (`0xD`); the key is stored as UnicodeChar (stack slot `+0x1a`)
  with a zeroed scan code. raphael site: `0x373C` (same build as cepheus).
- **Patch Creator:** [Gustave Monce](https://github.com/gus33000) (original),
  ported to raphael firmware for this tree.

### DALSYSDxe

- **Reason:** To avoid Mismatched Cached Copies.
- **Patch:** Enabled Cache Coherence for UFS - the DAL device property byte
  8 bytes before the `$_SB_.UFS0` ACPI path string (`0x2E5A6`) set to `0x01`.
- **Patch Creator:** [Gustave Monce](https://github.com/gus33000) (original),
  ported to raphael firmware for this tree.

### ClockDxe

- **Reason:** A DAL-dispatcher function (byte-arg lookup through the global
  DAL object at `0x43888+0x140`, then a vtable call) hangs/misbehaves in the
  Silicium environment on SM8150; the cepheus set stubbed it, so does this tree.
- **Patch:** The function (raphael `0xB54C`, cepheus `0xB4FC` + 0x50) is
  stubbed to `mov w0,#0; ret` (return `EFI_SUCCESS`).
- **Patch Creator:** cepheus set (Project-Silicium), identified and ported to
  raphael firmware for this tree.

### UsbConfigDxe

- **Reason:** To make USB work in the OS.
- **Patch:** The USB Deinit Code has been Removed from Exit Boot Services
  Event to allow the OS to continue using the USB Port - both conditional
  deinit paths (`cbz w10`, raphael `0x49C4`/`0x4A20`, same offsets as
  cepheus) turned into unconditional branches to the skip path.
- **Patch Creator:** [Gustave Monce](https://github.com/gus33000) (original),
  ported to raphael firmware for this tree.

### DisplayDxe

- The cepheus set shipped a **rebuilt** `DisplayDxe.patched.efi` (+0x1000 of
  .text, 346 scattered diff islands - not a portable byte patch). It is kept
  here as `DisplayDxe.cepheus-patched.efi` (**not referenced** by the INF) as a
  known-good fallback experiment; the INF now points at raphael's own stock
  `DisplayDxe.efi`. Note the raphaelPkg DSC builds with
  `USE_CUSTOM_DISPLAY_DRIVER = 0`, so the binary DisplayDxe is dormant anyway -
  Silicium's SimpleFbDxe drives the display for stage 1 (the ABL-initialized
  framebuffer at `0x9C000000`).

## What is NOT from this XBL

Nothing. Every `.efi`, `.te`, `.raw`, `.depex` and `RawFiles/` payload in this
directory tree is from the raphael `xbl.img`; the only non-raphael bytes are
`DisplayDxe.cepheus-patched.efi` (documented above). The `xbl_config`/`abl`
partitions contain further drivers (XhciDxe and friends) that SM8150 does not
need for this port - the Include set follows upstream `cepheusPkg` exactly.
