#include <dolphin/os.h>

void* __OSArenaLo = (void*)-1;
void* __OSArenaHi;

void* OSGetArenaHi(void) { return __OSArenaHi; }
void* OSGetArenaLo(void) { return __OSArenaLo; }
void  OSSetArenaHi(void* newHi) { __OSArenaHi = newHi; }
void  OSSetArenaLo(void* newLo) { __OSArenaLo = newLo; }
