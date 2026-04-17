#!/usr/bin/env python3
"""
fix_nonmatch.py — Post-process m2c output to fix compilation errors.

Transformations (applied in order):
  1. @symbol → at_symbol      (CW-specific labels gcc can't parse)
  2. (bitwise TYPE) → (TYPE)  (reinterpret cast → value cast, compiles)
  3. Negative offset: EXPR->unk-HEX  → (*(u8*)((char*)(EXPR)-0xHEX))
  4. Combined field+vtable (multi-pass, field non-call BEFORE vtable):
       a. EXPR->unkHEX (not followed by '(') → (*(u32*)((char*)(EXPR)+0xHEX))
       b. EXPR->unkHEX(args) [vtable call]  → ((u32(*)())(*(void**)...))(args)
  5. VAR.unkHEX / lbl.unkHEX (dot access)  → (*(u32*)((char*)&VAR+0xHEX))
  6. *(BASE + OFFSET) deref fix            → (*(u32*)((char*)(BASE) + OFFSET))
  7. Duplicate forward decls  → keep first by NAME (also drop if function defined)
  8. nonmatch.h: types, M2C_CARRY, MULTU_HI, saved_reg_*, ps_* stubs (written once)

Usage:
  python tools/fix_nonmatch.py              # fix all src/nonmatch/seg_*.c in place
  python tools/fix_nonmatch.py --verify     # also gcc-check each file after fixing
  python tools/fix_nonmatch.py --dry-run    # count changes, don't write
"""

import argparse, re, subprocess, sys
from pathlib import Path

REPO   = Path(__file__).resolve().parent.parent
NM_DIR = REPO / "src" / "nonmatch"
NM_HDR = NM_DIR / "nonmatch.h"

# ---------------------------------------------------------------------------
# nonmatch.h
# ---------------------------------------------------------------------------
NONMATCH_H = """\
/* Shared types/macros for m2c-generated nonmatching code */
#pragma once

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

/* Carry bit placeholder for 64-bit arithmetic patterns */
#ifndef M2C_CARRY
# define M2C_CARRY 0
#endif

/* High word of unsigned 32x32→64 multiply */
#ifndef MULTU_HI
# define MULTU_HI(a,b) 0U
#endif

/* Callee-saved integer register stubs (for __save_gpr / __restore_gpr etc.) */
#ifndef saved_reg_r14
# define saved_reg_r2  0
# define saved_reg_r13 0
# define saved_reg_r14 0
# define saved_reg_r15 0
# define saved_reg_r16 0
# define saved_reg_r17 0
# define saved_reg_r18 0
# define saved_reg_r19 0
# define saved_reg_r20 0
# define saved_reg_r21 0
# define saved_reg_r22 0
# define saved_reg_r23 0
# define saved_reg_r24 0
# define saved_reg_r25 0
# define saved_reg_r26 0
# define saved_reg_r27 0
# define saved_reg_r28 0
# define saved_reg_r29 0
# define saved_reg_r30 0
# define saved_reg_r31 0
# define saved_reg_l   0
#endif

/* Callee-saved FP register stubs (for __save_fpr / __restore_fpr etc.) */
#ifndef saved_reg_f14
# define saved_reg_f14 ((f64)0)
# define saved_reg_f15 ((f64)0)
# define saved_reg_f16 ((f64)0)
# define saved_reg_f17 ((f64)0)
# define saved_reg_f18 ((f64)0)
# define saved_reg_f19 ((f64)0)
# define saved_reg_f20 ((f64)0)
# define saved_reg_f21 ((f64)0)
# define saved_reg_f22 ((f64)0)
# define saved_reg_f23 ((f64)0)
# define saved_reg_f24 ((f64)0)
# define saved_reg_f25 ((f64)0)
# define saved_reg_f26 ((f64)0)
# define saved_reg_f27 ((f64)0)
# define saved_reg_f28 ((f64)0)
# define saved_reg_f29 ((f64)0)
# define saved_reg_f30 ((f64)0)
# define saved_reg_f31 ((f64)0)
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
"""

def ensure_header() -> None:
    NM_HDR.write_text(NONMATCH_H)


# ---------------------------------------------------------------------------
# Balanced-paren matcher (handles up to 5 levels — covers all m2c output)
# ---------------------------------------------------------------------------
_NP = r'[^()]*'
_P1 = rf'\({_NP}\)'
_P2 = rf'\({_NP}(?:{_P1}{_NP})*\)'
_P3 = rf'\({_NP}(?:{_P2}{_NP})*\)'
_P4 = rf'\({_NP}(?:{_P3}{_NP})*\)'
_P5 = rf'\({_NP}(?:{_P4}{_NP})*\)'
BPAREN = rf'(?:{_P5}|{_P4}|{_P3}|{_P2}|{_P1})'


# ---------------------------------------------------------------------------
# 1. @ symbols → valid C identifiers
# ---------------------------------------------------------------------------
_AT_SYM = re.compile(r'@(\w+)')

def fix_at_symbols(src: str) -> str:
    return _AT_SYM.sub(lambda m: f'at_{m.group(1)}', src)


# ---------------------------------------------------------------------------
# 2. (bitwise TYPE) → (TYPE)
# ---------------------------------------------------------------------------
_BITWISE = re.compile(r'\(bitwise\s+((?:[\w]+(?:\s*\*)?(?:\s*\*)?)|(?:void\s*\*))\s*\)')

def fix_bitwise(src: str) -> str:
    return _BITWISE.sub(lambda m: f'({m.group(1).strip()})', src)


# ---------------------------------------------------------------------------
# 3. Negative-offset field access: EXPR->unk-HEX → (*(u8*)((char*)(EXPR)-0xHEX))
#    Uses [0-9A-Fa-f]+ to handle hex offsets like unk-3C.
# ---------------------------------------------------------------------------
_FIELD_NEG_ID = re.compile(r'\b([A-Za-z_]\w*)\s*->\s*unk-([0-9A-Fa-f]+)\b')
_FIELD_NEG_PAR = re.compile(rf'({BPAREN})\s*->\s*unk-([0-9A-Fa-f]+)\b')

def _field_neg(ptr: str, n: str) -> str:
    return f'(*(u8*)((char*)({ptr})-0x{n.upper()}))'

def fix_field_neg(src: str) -> str:
    src = _FIELD_NEG_ID.sub(lambda m: _field_neg(m.group(1), m.group(2)), src)
    src = _FIELD_NEG_PAR.sub(lambda m: _field_neg(m.group(1), m.group(2)), src)
    return src


# ---------------------------------------------------------------------------
# 4. Combined field access + vtable calls (multi-pass, field BEFORE vtable)
#
#    Key insight: for chained accesses like a->unkXX->unkYY(args):
#    - Run FIELD first (non-call, i.e. NOT followed by '(')
#      a->unkXX (not followed by '(') → (*(u32*)((char*)(a)+0xXX))
#    - Run VTABLE second on the result
#      (*(u32*)((char*)(a)+0xXX))->unkYY( → proper vtable call
#    Repeat until stable.
# ---------------------------------------------------------------------------
# FIELD: exclude call context with (?!\s*\()
_FIELD_ID = re.compile(r'\b([A-Za-z_]\w*)\s*->\s*unk([0-9A-Fa-f]{1,4})\b(?!\s*\()')
_FIELD_PAREN = re.compile(rf'({BPAREN})\s*->\s*unk([0-9A-Fa-f]{{1,4}})\b(?!\s*\()')

# VTABLE: only for call context
_VTBL_ID = re.compile(r'\b([A-Za-z_]\w*)\s*->\s*unk([0-9A-Fa-f]{1,4})\s*\(')
_VTBL_PAREN = re.compile(rf'({BPAREN})\s*->\s*unk([0-9A-Fa-f]{{1,4}})\s*\(')

def _vtbl(ptr: str, off: str) -> str:
    return f'((u32(*)())(*(void**)((char*)({ptr})+0x{off.upper()})))('

def _field(ptr: str, off: str) -> str:
    return f'(*(u32*)((char*)({ptr})+0x{off.upper()}))'

def fix_unk_access(src: str) -> str:
    """Multi-pass: field (non-call) BEFORE vtable so chained a->unkXX->unkYY( is handled."""
    for _ in range(20):
        new = src
        # Field access first (non-call context only)
        new = _FIELD_ID.sub(lambda m: _field(m.group(1), m.group(2)), new)
        new = _FIELD_PAREN.sub(lambda m: _field(m.group(1), m.group(2)), new)
        # Then vtable calls on what remains
        new = _VTBL_ID.sub(lambda m: _vtbl(m.group(1), m.group(2)), new)
        new = _VTBL_PAREN.sub(lambda m: _vtbl(m.group(1), m.group(2)), new)
        if new == src:
            break
        src = new
    return src


# ---------------------------------------------------------------------------
# 5. Dot access: VAR.unkHEX → (*(u32*)((char*)&VAR+0xHEX))
# ---------------------------------------------------------------------------
_DOT_FIELD = re.compile(r'\b([A-Za-z_]\w*)\.unk([0-9A-Fa-f]{1,4})\b')

def fix_dot_access(src: str) -> str:
    return _DOT_FIELD.sub(
        lambda m: f'(*(u32*)((char*)&{m.group(1)}+0x{m.group(2).upper()}))',
        src
    )


# ---------------------------------------------------------------------------
# 6. Fix *(BASE + OFFSET) deref where BASE is a word or paren expression.
#    "invalid use of void expression" (void* arithmetic) and
#    "invalid type argument of unary *" (u32 arithmetic) are both fixed by
#    casting the base to char* before adding the offset.
# ---------------------------------------------------------------------------
# Match *(WORD + EXPR) where EXPR may contain one level of parens
_DEREF_OFFSET_ID = re.compile(
    r'\*\((\w+)\s*\+\s*((?:[^()]*|\([^()]*\))+)\)'
)
# Match *((BPAREN) + EXPR)
_DEREF_OFFSET_PAR = re.compile(
    rf'\*\(({BPAREN})\s*\+\s*((?:[^()]*|\([^()]*\))+)\)'
)

def fix_deref_offset(src: str) -> str:
    # PAR first (more specific base), then ID
    src = _DEREF_OFFSET_PAR.sub(
        lambda m: f'(*(u32*)((char*)({m.group(1)}) + {m.group(2)}))', src
    )
    src = _DEREF_OFFSET_ID.sub(
        lambda m: f'(*(u32*)((char*)({m.group(1)}) + {m.group(2)}))', src
    )
    return src


# ---------------------------------------------------------------------------
# 7. Deduplicate forward declarations by function/variable NAME.
#    Also drops forward decls for functions that are DEFINED in the same file
#    (prevents "conflicting types" errors when m2c generates wrong signatures).
# ---------------------------------------------------------------------------
_FWD_DECL_LINE = re.compile(
    r'^([ \t]*(?:extern\s+)?(?:void\s*\*?|u32|s32|u8|u16|s16|char\s*\*|f32|u64|s64|f64)\s+'
    r'((?:fn|dtor|ctor|lbl|at_)_?[0-9A-Fa-f]+(?:_[A-Za-z0-9_]*)?)'
    r'[^\n;]*;[^\n]*)',
    re.MULTILINE
)

# Detect function definitions (return_type name(...) { ) to suppress forward decls
_FUNC_DEF_NAME = re.compile(
    r'^(?:static\s+)?(?:void|u32|s32|u8|u16|s16|u64|s64|f32|f64|char)\s*'
    r'\*?\s*(\w+)\s*\([^)]*\)\s*\{',
    re.MULTILINE
)

def dedup_fwd_decls(src: str) -> str:
    defined_names = set(m.group(1) for m in _FUNC_DEF_NAME.finditer(src))
    seen: set[str] = set()
    def keep(m):
        name = m.group(2)
        if name in seen or name in defined_names:
            return ''
        seen.add(name)
        return m.group(0)
    return _FWD_DECL_LINE.sub(keep, src)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def fix_file(src: str) -> str:
    src = fix_at_symbols(src)
    src = fix_bitwise(src)
    src = fix_field_neg(src)       # negative offsets before combined pass
    src = fix_unk_access(src)      # field (non-call) then vtable, multi-pass
    src = fix_dot_access(src)
    src = fix_deref_offset(src)    # *(base + offset) → typed pointer deref
    src = dedup_fwd_decls(src)
    return src


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
GCC_FLAGS = [
    'gcc', '-fsyntax-only', '-x', 'c', '-std=gnu11',
    '-Wno-incompatible-pointer-types',
    '-Wno-int-conversion',
    '-Wno-return-type',
    '-Wno-implicit-function-declaration',
    '-Wno-unused-value',
    '-Wno-int-to-pointer-cast',
    '-Wno-pointer-to-int-cast',
    '-Wno-discarded-qualifiers',
    '-w',       # silence all warnings — only count hard errors
    '-I', str(NM_DIR),
]

def verify(path: Path) -> tuple[int, list[str]]:
    r = subprocess.run(GCC_FLAGS + [str(path)], capture_output=True, text=True)
    errors = [l for l in r.stderr.splitlines() if ': error:' in l]
    unique = list(dict.fromkeys(
        re.sub(r'^[^:]+:[0-9]+:[0-9]+:', '', e).strip() for e in errors
    ))
    return len(errors), unique


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--verify',  action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('files', nargs='*', type=Path)
    args = ap.parse_args()

    if not args.dry_run:
        ensure_header()
        print(f"[+] Wrote {NM_HDR.name}")

    seg_files = args.files or sorted(NM_DIR.glob('seg_*.c'))
    n_changed = 0
    pass_c = fail_c = 0
    remaining: dict[str, int] = {}

    for path in seg_files:
        orig = path.read_text()
        fixed = fix_file(orig)
        if fixed != orig:
            n_changed += 1
            if not args.dry_run:
                path.write_text(fixed)

        if args.verify:
            n_err, msgs = verify(path)
            if n_err == 0:
                pass_c += 1
            else:
                fail_c += 1
                for msg in msgs:
                    remaining[msg] = remaining.get(msg, 0) + 1

    if not args.dry_run:
        print(f"[+] Rewrote {n_changed}/{len(seg_files)} files")

    if args.verify:
        print(f"\n[+] gcc: {pass_c} PASS, {fail_c} FAIL / {len(seg_files)} total")
        if remaining:
            print("\nTop remaining error classes:")
            for msg, cnt in sorted(remaining.items(), key=lambda x: -x[1])[:20]:
                print(f"  {cnt:5d}  {msg}")

    return 0 if (not args.verify or fail_c == 0) else 1


if __name__ == '__main__':
    sys.exit(main())
