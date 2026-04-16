#ifndef _DOLPHIN_OS_OSUTIL_H
#define _DOLPHIN_OS_OSUTIL_H

#include "dolphin/types.h"

#ifdef __MWERKS__
#define AT_ADDRESS(addr) : (addr)
#else
#define AT_ADDRESS(addr)
#endif

#endif  // _DOLPHIN_OS_OSUTIL_H
