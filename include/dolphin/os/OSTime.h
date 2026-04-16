#ifndef OSTIME_H
#define OSTIME_H

#include "dolphin/types.h"
#include "dolphin/os/OSUtil.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef s64 OSTime;
typedef u32 OSTick;

OSTime OS_SYSTEM_TIME AT_ADDRESS(0x800030D8);

typedef struct OSCalendarTime {
    s32 seconds;
    s32 minutes;
    s32 hours;
    s32 day_of_month;
    s32 month;
    s32 year;
    s32 week_day;
    s32 year_day;
    s32 milliseconds;
    s32 microseconds;
} OSCalendarTime;

OSTime OSGetTime(void);
OSTick OSGetTick(void);
OSTime __OSGetSystemTime(void);
void OSTicksToCalendarTime(OSTime ticks, OSCalendarTime* ct);

extern u32 __OSBusClock AT_ADDRESS(0x800000F8);
#define OS_BUS_CLOCK   (__OSBusClock)
#define OS_TIMER_CLOCK (OS_BUS_CLOCK / 4)

#define OSTicksToSeconds(ticks)      ((ticks) / OS_TIMER_CLOCK)
#define OSTicksToMilliseconds(ticks) ((ticks) / (OS_TIMER_CLOCK / 1000))
#define OSTicksToMicroseconds(ticks) (((ticks) * 8) / (OS_TIMER_CLOCK / 125000))
#define OSSecondsToTicks(sec)        ((sec) * OS_TIMER_CLOCK)

#ifdef __cplusplus
};
#endif

#endif  // OSTIME_H
