#ifndef OS_H
#define OS_H

#include "dolphin/types.h"
#include "dolphin/os/OSContext.h"
#include "dolphin/os/OSInterrupt.h"
#include "dolphin/os/OSTime.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OS_CACHED_REGION_PREFIX   0x8000
#define OS_UNCACHED_REGION_PREFIX 0xC000
#define OS_BASE_CACHED   (OS_CACHED_REGION_PREFIX   << 16)
#define OS_BASE_UNCACHED (OS_UNCACHED_REGION_PREFIX << 16)

#define OSPhysicalToCached(paddr)    ((void*)((u32)(paddr) + OS_BASE_CACHED))
#define OSPhysicalToUncached(paddr)  ((void*)((u32)(paddr) + OS_BASE_UNCACHED))
#define OSCachedToPhysical(caddr)    ((u32)((u8*)(caddr) - OS_BASE_CACHED))
#define OSCachedToUncached(caddr)    ((void*)((u8*)(caddr) + (OS_BASE_UNCACHED - OS_BASE_CACHED)))
#define OSUncachedToCached(ucaddr)   ((void*)((u8*)(ucaddr) - (OS_BASE_UNCACHED - OS_BASE_CACHED)))
#define OSUncachedToPhysical(ucaddr) ((u32)((u8*)(ucaddr) - OS_BASE_UNCACHED))

typedef u32 __OSException;

#define OS_ERROR_MACHINE_CHECK 0

typedef void (*OSExceptionHandler)(__OSException, OSContext*);
OSExceptionHandler __OSSetExceptionHandler(__OSException exception, OSExceptionHandler handler);
OSExceptionHandler __OSGetExceptionHandler(__OSException exception);

typedef void (*__OSErrorHandler)(u16 error, OSContext* context, ...);
__OSErrorHandler OSSetErrorHandler(__OSException exception, __OSErrorHandler handler);

void OSReport(const char* msg, ...);
void OSDumpContext(OSContext* context);
void OSLoadContext(OSContext* context);

void OSDisableScheduler(void);
void OSEnableScheduler(void);
void __OSReschedule(void);

#ifdef __cplusplus
};
#endif

#endif  // OS_H
