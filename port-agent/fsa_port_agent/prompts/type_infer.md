# TYPE_INFER prompt

Given N functions that access a shared struct via `(*(u32*)((char*)p + 0xNN))`
or `p->unk_0xNN` patterns, produce a single C typedef that captures the
observed layout.

## Inputs

Functions (de-duplicated):
```c
{functions}
```

## Rules

- Name the struct based on observed usage (caller signature hints, string
  refs). Prefer camelCase class names if a vtable or `__register_global_object`
  call is visible.
- Emit unknowns as `u8 padN[N];` with correct sizing.
- Preserve observed alignment — 4-byte unless 8-byte access (double, `stfd`)
  appears.
- Output ONE typedef. No prose.
