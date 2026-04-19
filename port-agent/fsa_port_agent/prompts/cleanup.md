# CLEANUP prompt

Turn m2c-generated C for one GameCube function into code that compiles under
emscripten (clang/gcc). Goal is **semantic correctness + human readability**,
not byte-matching. Register allocation is irrelevant.

Another human will read this code after you. Make that reader's life easier.

## Inputs

- Function address: `{fn_addr}`
- **Required signature** (authoritative — this is the signature the seg
  file's own body currently declares for this fn; the first line of your
  body MUST match this return type, name, and argument list. Disagreement
  will fail the gate with `conflicting types` or `too few arguments`.):
  ```c
  {target_decl}
  ```
- **Mangled CodeWarrior name** (from FSA's `symbols.txt`, if known — this
  tells you what class/method this fn *is*. Authoritative for purpose,
  NOT for the definition line — still use `{target_decl}` for that):
  ```
  {tww_name}
  ```
- Raw m2c output. **Warning — m2c drops arguments it cannot prove and
  leaks its own scratch locals.** If this block calls `fn_X()` with fewer
  or more args than the "Already-resolved callee signatures" section lists,
  or with the wrong types, **the signatures are authoritative** — rewrite
  the call to match. Same for undeclared `unk0` / `arg0` / `var_rN` /
  `temp_rN` identifiers: if m2c emitted them, either replace with the
  parameter name from `{target_decl}` or declare a local; never leave an
  undeclared identifier. This single issue causes the dominant ~34% of
  gate rejections (too-few/too-many-arguments + conflicting types).
  ```c
  {m2c_source}
  ```
- Already-resolved callee signatures (**authoritative — call these with
  EXACTLY this arity and these types; do NOT trust m2c's callsite shape
  above**):
  ```
  {callee_sigs}
  ```
- Caller usage hints:
  ```
  {caller_sigs}
  ```
- String references in this function's `.rodata`:
  ```
  {strings}
  ```
- Nearby matched functions (style reference — prefer these variable-naming
  conventions over m2c's `var_r3` / `temp_r4` register names):
  ```c
  {nearby}
  ```
- **TWW reference** — the matching method in the TWW source tree, if FSA's
  mangled symbol appears in TWW. Use this as the *types and naming* template
  for LOCALS: variable names, struct field access patterns, helper calls.
  FSA's version may have different register allocation or minor body
  differences, but the surrounding class layout and call shape will be
  nearly identical. **Use TWW's `this->foo` / local names — BUT the
  definition line still follows `{target_decl}`.** Do not rename the
  definition's arguments to `this` unless `{target_decl}` already is.
  ```cpp
  {tww_reference}
  ```
- **SIMD / paired-single helpers** — if the m2c output contains any
  `M2C_ERROR(/* unknown instruction: ps_* */)` markers ({m2c_error_count}
  in this body), call the typed inline helpers declared in `_ps_emu.h`
  instead of leaving the marker. The seg file already includes this header.
  Available helpers:
  ```c
  {helpers}
  ```

- **Prior attempt** (empty unless this is a retry — check
  `{prior_attempt_num}`): what the previous attempt wrote, plus the gate
  error that rejected it. Diagnose what went wrong and write a DIFFERENT
  response; do not repeat the same shape.
  ```c
  {prior_response}
  ```
  Compiler rejected it with:
  ```
  {prior_error}
  ```

## Environment constraints (hard limits — the gate rejects these)

The seg file includes **only** `nonmatch.h`, which provides:

- Integer typedefs: `u8 s8 u16 s16 u32 s32 u64 s64` and floats `f32 f64`.
- `NULL` only.
- `ps_*` macros that stub paired-single ops (ps_add, ps_sub, ps_mul,
  ps_madd, ps_msub, ps_sum0/1, ps_muls0/1, ps_madds0/1, psq_l, psq_st).

There is **no libc available**. Specifically:

- **No** `<math.h>`: do NOT call `sqrtf`, `fabsf`, `sinf`, `cosf`, `floor`,
  etc. If m2c emitted one, replace it with the inline equivalent (or a
  `// TODO: sqrtf` stub that returns the argument) — a missing math
  prototype fails the gate with `implicit declaration of function`.
- **No** `<string.h>`: no `memcpy`, `memset`, `strcmp`, `strcpy`, `strlen`.
  For field-at-offset assignment, just write the assignment directly.
- **No** `<stdint.h>`: do NOT use `uintptr_t`, `intptr_t`, `size_t`,
  `ptrdiff_t`. For pointer arithmetic, cast through `(u32)` or
  `(char *)`, never `(uintptr_t)`.
- **No** `<stdio.h>`, `<stdlib.h>`, `<assert.h>`, no `printf`, `assert`,
  `malloc`, `abs`.
- **No** C++ features: no `new`, `delete`, `class`, `virtual`, `template`,
  no `std::`, no `nullptr` (use `NULL`), no references (`&`), no method
  syntax. TWW reference is C++ — translate it to plain C.

Other gate-level rules:

- Do not use `volatile` for register MMIO — those paths will be HAL shimmed.
- Do not include inline PPC `asm{}` blocks. Replace with portable C.
- `switch (x) { case 42: ... }` — leave `case` constants as literal ints
  (from the m2c output). Do NOT try to name them; synthesis pass renames.

## Readability rules (soft but important)

This is the difference between "m2c noise the next engineer has to re-read"
and "code someone can skim to understand the fn's intent":

- **No `var_rN` / `temp_rN` / `saved_reg_*` locals.** Those are m2c's
  register-allocation artifacts. Rename based on what the value represents:
  `i`, `count`, `ptr`, `node`, `x`, `result`, etc. If the TWW reference
  uses a specific name (e.g. `pActor`, `mMode`), reuse it.
- **Prefer struct field access over raw offset arithmetic.** If the callee
  sigs tell you `arg0` is a `fopAc_ac_c *`, write `arg0->mMode` instead of
  `*(u32 *)((char *)arg0 + 0x234)`. If the struct isn't known yet, leave
  `arg0->unk_0x234` as `unk_0x234` (a placeholder the SYNTHESIZE pass
  will reconcile into a named field).
- **Prefer `for (i = 0; i < N; i++)` over `do { ... } while (ctr != 0)`**
  when the m2c output is plainly counting. If the loop is a
  DecrementAndBranch pattern, a `while` or `for` is clearer.
- **Collapse m2c's repeated `*(u32 *)(p + 0x4) = 0xFFFFFFFF;` into a
  memset-style loop or a `for`-loop over a field if the intent is "clear
  an array". Stay semantically equivalent — don't skip writes.
- **Resolve `M2C_ERROR(expr)`** by producing the actual expression; if
  truly unknown, replace with a call to a named helper and emit a `TODO:`
  comment. If it's a paired-single op, call the `ps_*` helper in `{helpers}`.
- **Replace `MULTU_HI(a,b)`** with `((u64)(a)*(u64)(b))>>32` unless a
  cleaner idiom fits (e.g. signed-division-by-constant).
- Infer struct field names from callee signatures or the TWW reference
  when possible; otherwise leave `unk_0xNN` — the SYNTHESIZE pass will
  reconcile.

## Output

Output ONE function body. No prose, no markdown fence.

## Final checklist

Before writing the response file, confirm ALL of:

- [ ] Contains `fn_<ADDR>` in the definition line.
- [ ] Definition line matches `{target_decl}` exactly (same return type, same
      argument types in the same order — parameter names may differ).
- [ ] Every call to a `fn_<ADDR>` in the body passes arguments that conform to
      the callee signature listed under "Already-resolved callee signatures".
- [ ] No `M2C_ERROR(` remains — every marker resolved, stubbed, or replaced
      with a `_ps_emu.h` helper call.
- [ ] No libc call that isn't in the Environment constraints list (no
      `sqrtf`, `memcpy`, `printf`, `malloc`, `uintptr_t`, etc.).
- [ ] No `#include` directive — the seg file already includes what exists.
- [ ] All braces balanced; function ends in `}`.
- [ ] No markdown fence (no ``` opening/closing).
- [ ] No `asm {` / `__asm` blocks.
- [ ] No raw `saved_reg_*` / `var_rN` / `temp_rN` locals from the m2c input.

A failure on any of the above → the compile gate rejects the response and
the function is re-enqueued at the next tier.
