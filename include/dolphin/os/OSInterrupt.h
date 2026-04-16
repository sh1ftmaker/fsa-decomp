#ifndef OSINTERRUPT_H
#define OSINTERRUPT_H

#include "dolphin/types.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef u32 __OSInterrupt;
typedef u32 __OSMask;

BOOL OSDisableInterrupts(void);
BOOL OSEnableInterrupts(void);
BOOL OSRestoreInterrupts(BOOL status);

#ifdef __cplusplus
};
#endif

#endif  // OSINTERRUPT_H
