#ifndef DOLPHIN_OS_H
#define DOLPHIN_OS_H

#ifdef __cplusplus
extern "C" {
#endif

extern void* __OSArenaHi;
extern void* __OSArenaLo;

void* OSGetArenaHi(void);
void* OSGetArenaLo(void);
void  OSSetArenaHi(void* newHi);
void  OSSetArenaLo(void* newLo);

#ifdef __cplusplus
}
#endif

#endif // DOLPHIN_OS_H
