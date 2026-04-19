# CLEANUP prompt

Turn m2c-generated C for one GameCube function into code that compiles under
emscripten (clang/gcc). Goal is **semantic correctness**, not byte-matching.
Register allocation is irrelevant.

## Inputs

- Function address: `{fn_addr}`
- Raw m2c output:
  ```c
  {m2c_source}
  ```
- Already-resolved callee signatures:
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
- Nearby matched functions (style reference):
  ```c
  {nearby}
  ```

## Rules

- Resolve `M2C_ERROR(expr)` by producing the actual expression; if truly
  unknown, replace with a call to a named helper and emit a `TODO:` comment.
- Replace `MULTU_HI(a,b)` with `((u64)(a)*(u64)(b))>>32` unless a cleaner
  idiom fits.
- Delete `saved_reg_*` locals — they are m2c's register-shuffle artifacts.
- Infer struct field names from callee signatures when possible; otherwise
  leave `unk_0xNN` — the SYNTHESIZE pass will reconcile.
- Do not include inline PPC `asm{}` blocks. Replace with portable C.
- Do not use `volatile` for register MMIO — those paths will be HAL shimmed.
- Output ONE function body. No prose, no markdown fence.
