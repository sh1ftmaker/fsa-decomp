# SYNTHESIZE prompt (global pass, run once at end of Phase 3)

Review candidate types + naming across the whole project. Produce a single
header that unifies them.

## Inputs

- All candidate struct typedefs emitted by TYPE_INFER:
  ```c
  {candidates}
  ```
- Top-N highest-connectivity function signatures:
  ```
  {hot_sigs}
  ```
- Resolved string → function-name mapping (from `__register_global_object`
  calls and assertion strings):
  ```
  {name_hints}
  ```

## Rules

- Merge duplicate struct definitions by field-alignment equivalence.
- Normalize names: `fn_8001ABCD` → meaningful name when string evidence or
  callee/caller patterns permit; else keep `fn_` prefix.
- Emit as one `src/nonmatch/_synthesized_types.h` file including all unified
  typedefs + `extern` declarations. No function bodies.
- When in doubt, *do not rename*. Wrong names are worse than address names.
