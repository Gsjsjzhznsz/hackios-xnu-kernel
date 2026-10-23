# raphael's GPU firmware (fw/a640_gmu.bin, fw/a630_sqe.fw, fw/a640_zap.mbn)

The three files bsd/dev/raphael_adreno.c looks for in `fw/` of the small image
(md1, made by userland/build.sh from `../fw-gpu/` -- this directory, copied
there by the build workflow or by hand: `cp fw-gpu-raphael/* fw-gpu/`).

Provenance: Linux's linux-firmware (the 20260916 set, as Debian's
firmware-qcom-soc 20260916-1 packages it), where the msm catalog's names for
chipid 0x06040001 (the Adreno 640 of raphael's SM8150) lead:

  a640_gmu.bin    qcom/a640_gmu.bin -- the GMU's own firmware, the a640
                  family's (GEN2) block format: its code and data for the
                  GMU's two 16K memories named inside, and one block at
                  0x57000 that goes where Linux allocates the family's
                  "dcache" (the driver makes those pools itself)
  a630_sqe.fw     qcom/a630_sqe.fw -- the 3D core's command processor's
                  microcode
  a640_zap.mbn    qcom/sm8150/a640_zap.mbn -- sm8150's zap shader: the signed
                  image without which TrustZone keeps the 3D core in secure
                  mode. A single-file "mbn" (an ELF32 whose hash segment and
                  one relocatable PT_LOAD segment are both inside the file).
                  This is the generic one of linux-firmware; the phone's own
                  Android carries its own (its tree names
                  "qcom/sm8150/Xiaomi/raphael/a640_zap.mbn"). If TrustZone
                  refuses this one's signature (step 12 of the driver says
                  so), that one replaces this file, same name.

The workflow copies this directory into hackios/fw-gpu/ before
userland/build.sh runs. SHA256s of the files as they are here:

3ff0c02708bbe78641db887fa62f3a7f9337934d0c2ce0b961ef7c43172591d2  a640_gmu.bin
1c21b527d9183487cc550dabbb3f43e555df5a977a461934fc61f0635a9aa90c  a630_sqe.fw
a17975ca587974260fa5ba298fee63b70ee83b0817a8f515e0ee59571a96ee08  a640_zap.mbn
