#!/usr/bin/env python3
"""
Fix CodeWarrior compilation errors in m2c-generated nonmatch stubs.

Fixes applied:
  1. void *var_/temp_ → char *  (CodeWarrior forbids void* arithmetic)
  2. extern u32 lbl_  → extern char lbl_  (avoids u32* → char* implicit conv)
  3. *(u32*)(...)  = &lbl_XXX  → (u32)&lbl_XXX  (pointer→int needs explicit cast)
  4. obj->((fnptr)(...))(args) → ((fnptr)(...))(args)  (m2c vtable call bogus ->)
  5. Undeclared unkspXX variables get a s32 declaration injected at function top
"""
import re
import sys
from pathlib import Path

NONMATCH_DIR = Path(__file__).resolve().parent.parent / "src" / "nonmatch"

# Fix 1: local void* variable declarations (indented lines only)
_VOID_VAR = re.compile(r'^(\s+)void \*(var_|temp_)', re.MULTILINE)

# Fix 2: extern u32 label declarations → char
_EXTERN_LBL = re.compile(r'\bextern u32 (lbl_[0-9A-Fa-f]+)\b')

# Fix 3: assignment of &lbl_ to a u32-deref target — cast the address to u32
# Pattern: ) = &lbl_XXXXXXXX  (within a *(u32*) assignment context)
_LBL_ADDR_ASSIGN = re.compile(r'\) = &(lbl_[0-9A-Fa-f]+)\b')

# Fix 4: bogus vtable-call syntax: "ident->((cast)(...))(...)" → "((cast)(...))(...)"
# m2c emits  obj->((ret(*)(...))(*(void**)(...)))(args)
# Strip "obj->" leaving just the function-pointer call.
_VTABLE_ARROW = re.compile(r'\b\w+->(\(\()')

# Fix 5: undeclared unkspXX variables
_UNKSP_USE = re.compile(r'\bunksp([0-9A-Fa-f]+)\b')
_FN_OPEN = re.compile(
    r'^(?!extern|typedef|struct|union|#)[\w\s\*]+\bfn_[0-9A-Fa-f]+\s*\([^)]*\)\s*\{',
    re.MULTILINE,
)


def find_undeclared_unksp(src: str, declared: set[str]) -> list[str]:
    used = set(_UNKSP_USE.findall(src))
    return sorted(used - declared, key=lambda x: int(x, 16))


def fix_file(path: Path) -> bool:
    src = path.read_text(encoding="utf-8", errors="replace")
    orig = src

    src = _VOID_VAR.sub(r'\1char *\2', src)          # Fix 1
    src = _EXTERN_LBL.sub(r'extern char \1', src)    # Fix 2
    src = _LBL_ADDR_ASSIGN.sub(r') = (u32)&\1', src) # Fix 3
    src = _VTABLE_ARROW.sub(r'\1', src)               # Fix 4

    # Fix 5: inject s32 declarations for undeclared unkspXX locals
    declared = set(re.findall(r'\bunksp([0-9A-Fa-f]+)\b(?=\s*;)', src))
    missing = find_undeclared_unksp(src, declared)
    if missing:
        decls = "".join(f"\n    s32 unksp{n};" for n in missing)
        src = _FN_OPEN.sub(lambda m: m.group(0) + decls, src)

    if src != orig:
        path.write_text(src, encoding="utf-8")
        return True
    return False


def main():
    targets = [Path(p) for p in sys.argv[1:]] if sys.argv[1:] else sorted(NONMATCH_DIR.glob("seg_*.c"))
    changed = 0
    for p in targets:
        if fix_file(p):
            changed += 1
    print(f"Fixed {changed}/{len(targets)} files.")


if __name__ == "__main__":
    main()
