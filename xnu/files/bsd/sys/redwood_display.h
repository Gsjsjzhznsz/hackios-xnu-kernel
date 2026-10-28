/*
 * Where the panel's scan is (bsd/dev/redwood_display.c, boot-arg fb_scan=1): for the framebuffer
 * driver (iokit/Kernel/MiniIOMobileFramebuffer.cpp), so that frames go into the panel's memory
 * where the scan lets them land whole, at the panel's own rate.  (R38: the display round --
 * raphael's command-mode TE, the same port the reference repo runs on this phone; the names
 * follow this tree's redwood_ file convention, the raphael_display_* helpers stay raphael_
 * because they belong to raphael's display block, as raphael_touch.c's do.)
 */
#ifndef _SYS_REDWOOD_DISPLAY_H_
#define _SYS_REDWOOD_DISPLAY_H_

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct redwood_display_scan {
	int ok;
	uint32_t lines;                 /* lines a frame, the blanking included (2432) */
	uint32_t shown;                 /* of which shown (2400): lines 0..shown-1, then the blanking */
	uint32_t millihz;               /* frames a second, in thousandths (119930) */
	uint64_t line_ns, frame_ns;
};

int redwood_display_scan_start(void);                           /* 1 when the scan can be read */
const struct redwood_display_scan *redwood_display_scan(void);  /* NULL without */
uint32_t redwood_display_line(uint32_t *frame);                 /* video line or command TE phase tick; command frame is 0 */
uint64_t redwood_display_next_frame_ns(void);                   /* until the next frame's first line */

#ifdef __cplusplus
}
#endif

#endif /* _SYS_REDWOOD_DISPLAY_H_ */
