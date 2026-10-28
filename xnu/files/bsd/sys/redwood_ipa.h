/*
 * The modem's packets: the IPA's data path for this processor (bsd/dev/redwood.ipa.c) and the
 * network interface that stands on it (bsd/dev/redwood.cell.c).
 *
 * The IPA (the SoC's packet engine) carries IP packets between the modem and this processor in
 * QMAP framing: a 4-byte header (pad length, mux id, packet length) before each packet. The
 * driver of the modem (redwood.mpss.c) brings the IPA up; redwood.ipa.c sets up this processor's
 * side of it -- GSI rings, the IPA's endpoints, its tables -- before the modem is started, and
 * then moves packets. redwood.cell.c makes an Ethernet-looking interface (en1) out of that, with
 * an ARP and DHCP responder of its own, so that iOS configures it as it would a USB Ethernet
 * adapter: address, router and DNS come from the modem's data session (QMI WDS), which
 * redwood.mpss.c reports with redwood_cell_session().
 */
#ifndef _SYS_REDWOOD_IPA_H_
#define _SYS_REDWOOD_IPA_H_

#ifdef KERNEL
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

/* ---- redwood.ipa.c: the IPA's endpoints for this processor ---- */

/*
 * Set up this processor's channels and endpoints; before the modem starts, with the IPA's clock on,
 * its firmware running and its SMMU context in place (the modem driver's doing). False: no data
 * path (the log says why); everything else goes on without it.
 */
bool redwood_ipa_data_setup(void);
bool redwood_ipa_data_ready(void);
/* One IP packet to the modem on QMAP mux `mux`. Copied. False if dropped (no room, too long). */
bool redwood_ipa_transmit(uint8_t mux, const void *packet, size_t length);
/*
 * Packets from the modem: `receive` is called for each, with its mux id, from the caller's thread.
 * Returns how many there were. Also finishes transmissions (frees their buffers).
 */
unsigned redwood_ipa_poll(void (*receive)(uint8_t mux, const uint8_t *packet, size_t length));
/* What has gone through, for the log. */
void redwood_ipa_status(char *to, size_t size);
/* The IPA endpoints of this processor's data path to and from the modem (for QMI DPM Open Port). */
void redwood_ipa_modem_endpoints(uint32_t *to_modem, uint32_t *from_modem);

/* ---- redwood.cell.c: the interface ---- */

/*
 * Called by the modem driver: the data session is up with these settings (network byte order
 * addresses; mtu 0: unknown), or down. From any thread.
 */
void redwood_cell_session(bool up, uint32_t address, uint32_t mask, uint32_t gateway, uint32_t dns1, uint32_t dns2, uint32_t mtu);
/* Make en1 on the IPA's data path once redwood_ipa_data_setup() has succeeded. */
void redwood_cell_attach(void);

#endif /* KERNEL */
#endif /* _SYS_REDWOOD_IPA_H_ */
