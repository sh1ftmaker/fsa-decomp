/* _ps_emu.h -- paired-singles SIMD emulation scaffold for cleanup output.
 * m2c emits unknown-instruction markers for every ps_xxx op (PowerPC Gekko
 * paired-singles extension). These inline stubs give cleanup agents a typed
 * helper to call in place of the marker so the seg file still compiles.
 * Bodies are deliberately TODO: they return a plausibly-typed zero so the
 * compile gate passes. Phase 4+ fills them in with real semantics.
 * Include AFTER nonmatch.h so we supersede its macro stubs.
 */
#pragma once

#include "nonmatch.h"

/* The macros in nonmatch.h would shadow these inline functions. Drop them. */
#undef ps_add
#undef ps_sub
#undef ps_mul
#undef ps_div
#undef ps_abs
#undef ps_neg
#undef ps_madd
#undef ps_msub
#undef ps_nmadd
#undef ps_nmsub
#undef ps_sum0
#undef ps_sum1
#undef ps_muls0
#undef ps_muls1
#undef ps_madds0
#undef ps_madds1
#undef psq_l
#undef psq_lx
#undef psq_st
#undef psq_stx

#ifdef __cplusplus
extern "C" {
#endif

/* Paired-single: two f32s packed. Stored as a plain f32 here for scalar
 * compatibility with the existing nonmatch code; upgrade to a 2-lane
 * vector when a real backend lands. */
typedef f32 ps_t;

static inline ps_t ps_add(ps_t a, ps_t b)                         { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_add */ }
static inline ps_t ps_sub(ps_t a, ps_t b)                         { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_sub */ }
static inline ps_t ps_mul(ps_t a, ps_t b)                         { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_mul */ }
static inline ps_t ps_mul0(ps_t a, ps_t b)                        { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_mul0 */ }
static inline ps_t ps_mul1(ps_t a, ps_t b)                        { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_mul1 */ }
static inline ps_t ps_div(ps_t a, ps_t b)                         { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_div */ }
static inline ps_t ps_abs(ps_t a)                                 { (void)a;                       return (ps_t)0; /* TODO: ps_abs */ }
static inline ps_t ps_neg(ps_t a)                                 { (void)a;                       return (ps_t)0; /* TODO: ps_neg */ }
static inline ps_t ps_madd(ps_t a, ps_t b, ps_t c)                { (void)a; (void)b; (void)c;     return (ps_t)0; /* TODO: ps_madd */ }
static inline ps_t ps_msub(ps_t a, ps_t b, ps_t c)                { (void)a; (void)b; (void)c;     return (ps_t)0; /* TODO: ps_msub */ }
static inline ps_t ps_nmadd(ps_t a, ps_t b, ps_t c)               { (void)a; (void)b; (void)c;     return (ps_t)0; /* TODO: ps_nmadd */ }
static inline ps_t ps_nmsub(ps_t a, ps_t b, ps_t c)               { (void)a; (void)b; (void)c;     return (ps_t)0; /* TODO: ps_nmsub */ }
static inline ps_t ps_sum0(ps_t a, ps_t b, ps_t c)                { (void)a; (void)b; (void)c;     return (ps_t)0; /* TODO: ps_sum0 */ }
static inline ps_t ps_sum1(ps_t a, ps_t b, ps_t c)                { (void)a; (void)b; (void)c;     return (ps_t)0; /* TODO: ps_sum1 */ }
static inline ps_t ps_muls0(ps_t a, ps_t b)                       { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_muls0 */ }
static inline ps_t ps_muls1(ps_t a, ps_t b)                       { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_muls1 */ }
static inline ps_t ps_madds0(ps_t a, ps_t b, ps_t c)              { (void)a; (void)b; (void)c;     return (ps_t)0; /* TODO: ps_madds0 */ }
static inline ps_t ps_madds1(ps_t a, ps_t b, ps_t c)              { (void)a; (void)b; (void)c;     return (ps_t)0; /* TODO: ps_madds1 */ }

static inline ps_t ps_merge00(ps_t a, ps_t b)                     { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_merge00 */ }
static inline ps_t ps_merge01(ps_t a, ps_t b)                     { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_merge01 */ }
static inline ps_t ps_merge10(ps_t a, ps_t b)                     { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_merge10 */ }
static inline ps_t ps_merge11(ps_t a, ps_t b)                     { (void)a; (void)b;              return (ps_t)0; /* TODO: ps_merge11 */ }

/* Quantised loads/stores — scalar emulation. `w` / `q` select lanes/quant type. */
static inline ps_t psq_l(const void *p, s32 o, s32 w, s32 q)      { (void)p; (void)o; (void)w; (void)q; return (ps_t)0; /* TODO: psq_l */ }
static inline ps_t psq_lx(const void *p, s32 x, s32 w, s32 q)     { (void)p; (void)x; (void)w; (void)q; return (ps_t)0; /* TODO: psq_lx */ }
static inline void psq_st(ps_t v, void *p, s32 o, s32 w, s32 q)   { (void)v; (void)p; (void)o; (void)w; (void)q; /* TODO: psq_st */ }
static inline void psq_stx(ps_t v, void *p, s32 x, s32 w, s32 q)  { (void)v; (void)p; (void)x; (void)w; (void)q; /* TODO: psq_stx */ }

#ifdef __cplusplus
}
#endif
