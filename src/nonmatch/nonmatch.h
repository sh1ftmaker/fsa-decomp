/* Shared types/macros for m2c-generated nonmatching code */
#pragma once
#pragma ANSI_strict off
#pragma warn_illtype off

typedef signed char        s8;
typedef unsigned char      u8;
typedef signed short       s16;
typedef unsigned short     u16;
typedef signed int         s32;
typedef unsigned int       u32;
typedef signed long long   s64;
typedef unsigned long long u64;
typedef float              f32;
typedef double             f64;

#ifndef NULL
# define NULL ((void*)0)
#endif

/* M2C_ERROR: variadic so commas inside nested FIELD-casts don't break arg count */
#ifndef M2C_ERROR
# define M2C_ERROR(...) 0
#endif

/* Paired-singles (GC SIMD float) stubs — functions compile but give wrong fp results */
#ifndef ps_add
# define ps_add(a,b)         ((f32)0)
# define ps_sub(a,b)         ((f32)0)
# define ps_mul(a,b)         ((f32)0)
# define ps_div(a,b)         ((f32)0)
# define ps_abs(a)           ((f32)0)
# define ps_neg(a)           ((f32)0)
# define ps_madd(a,b,c)      ((f32)0)
# define ps_msub(a,b,c)      ((f32)0)
# define ps_nmadd(a,b,c)     ((f32)0)
# define ps_nmsub(a,b,c)     ((f32)0)
# define ps_sum0(a,b,c,d)    ((f32)0)
# define ps_sum1(a,b,c,d)    ((f32)0)
# define ps_muls0(a,b)       ((f32)0)
# define ps_muls1(a,b)       ((f32)0)
# define ps_madds0(a,b,c)    ((f32)0)
# define ps_madds1(a,b,c)    ((f32)0)
# define psq_l(p,o,w,q)      ((f32)0)
# define psq_lx(p,x,w,q)     ((f32)0)
# define psq_st(v,p,o,w,q)   ((f32)0)
# define psq_stx(v,p,x,w,q)  ((f32)0)
#endif
