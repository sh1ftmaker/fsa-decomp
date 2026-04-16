#ifndef OS_H
#define OS_H

#include "dolphin/types.h"
#include "dolphin/os/OSContext.h"
#include "dolphin/os/OSInterrupt.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OS_BASE_CACHED 0x80000000

typedef u32 __OSException;

#define OS_ERROR_MACHINE_CHECK 0

typedef void (*__OSErrorHandler)(u16 error, OSContext* context, ...);
__OSErrorHandler OSSetErrorHandler(__OSException exception, __OSErrorHandler handler);

void OSReport(const char* msg, ...);

#ifdef __cplusplus
};
#endif

#endif  // OS_H
