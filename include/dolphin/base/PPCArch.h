#ifndef PPCARCH_H
#define PPCARCH_H

#include "dolphin/types.h"

#ifdef __cplusplus
extern "C" {
#endif

// SPR numbers
#define HID0 1008
#define HID2  920
#define TBL   284
#define TBU   285
#define L2CR 1017

// MSR bits
#define MSR_EE 0x8000
#define MSR_IR 0x0020
#define MSR_DR 0x0010

// HID0 bits
#define HID0_ICE 0x8000
#define HID0_DCE 0x4000

// HID2 bits
#define HID2_DCHERR  0x800000
#define HID2_DNCERR  0x400000
#define HID2_DCMERR  0x200000
#define HID2_DQOERR  0x100000

// L2CR bits
#define L2CR_L2E  0x80000000
#define L2CR_L2I  0x00200000
#define L2CR_L2IP 0x00000001

// SRR1 bit for DMA machine check
#define SRR1_DMA_BIT 0x00200000

u32  PPCMfmsr(void);
void PPCMtmsr(u32 val);
u32  PPCMfhid0(void);
u32  PPCMfhid2(void);
void PPCMthid2(u32 val);
u32  PPCMfl2cr(void);
void PPCMtl2cr(u32 val);
void PPCHalt(void);
void __sync(void);

#ifdef __cplusplus
};
#endif

#endif  // PPCARCH_H
