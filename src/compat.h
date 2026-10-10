/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TBNET_COMPAT_H
#define TBNET_COMPAT_H

/* The frame helper and public size constant were introduced together.
 * Older kernels use the same 4096-byte framing (zero means a full frame).
 */
#ifdef TB_MAX_FRAME_SIZE
#define TBNET_HAVE_FRAME_SIZE 1
#else
#define TB_MAX_FRAME_SIZE 4096
#endif

#ifndef SPEED_80000
#define SPEED_80000 80000
#endif
#endif
