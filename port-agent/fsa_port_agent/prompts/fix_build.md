# FIX_BUILD prompt (Phase 5, reactive)

Emscripten/clang emitted an error while compiling `{file}`. Patch the file so
it compiles. Do not regress semantics.

## Inputs

- Current source (excerpt, ±20 lines around error):
  ```c
  {excerpt}
  ```
- Compiler error:
  ```
  {error}
  ```
- Relevant synthesized types:
  ```c
  {types}
  ```

## Rules

- Smallest possible change. Prefer casts over struct re-definitions.
- If the fix requires a new function declaration, add it in
  `src/nonmatch/_declarations.h` rather than inline.
- If the error is "undefined reference" for a HAL function, emit a stub in
  `src/platform/<subsystem>/stubs.c` returning a sensible default and note it
  with a `TODO:`.
- Output unified diff (`--- a/... +++ b/...` format). No prose.
