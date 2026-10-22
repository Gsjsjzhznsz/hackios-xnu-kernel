## Firmware Infos

- **Device:** Xiaomi Mi 9T Pro / K20 Pro (raphael) - port in progress
- **First-stage source:** `cepheus` (Xiaomi Mi 9, same SM8150), taken from
  [Project-Silicium/Device-Binaries](https://github.com/Project-Silicium/Device-Binaries)
  the way `redwood` took its own from the phone's firmware.
- **Pending:** replace with the drivers of raphael's own `xbl.img` (extract with
  [UEFIReader](https://github.com/WOA-Project/UEFIReader)) once its firmware is at hand;
  same-generation SM8150 drivers are expected to work as-is, but this is to be verified
  on the real phone, not assumed.

## Why cepheus binaries

raphael is not upstream in Project-Silicium, and no `binary-raphael` repository exists.
The nearest same-SoC (SM8150, Snapdragon 855) Xiaomi device with a maintained binary set
is cepheus (Mi 9): same QcomPkg driver surface (51 drivers), same RawFiles conventions
(`SecParti.cfg`, `uefipil.cfg`), same AMOLED panel class. The raphael UEFI FD built from
this package is a bring-up image; if any driver misbehaves on the phone, the fix is to
swap in raphael's own firmware, which this directory layout makes a drop-in change.

## Patches/Fixes

None applied yet (unlike redwood's five usual patches - see `../redwood/README.md`).
Apply the Silicium usual five to this set if the bring-up shows the same symptoms;
until then this is the unmodified cepheus set.
