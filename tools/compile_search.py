#!/usr/bin/env python3
"""
compile_search.py — Compile a C/C++ file with FSA flags and search DOL for each function.

Reads compiled .o, extracts each function's bytes + relocation mask, then searches
the FSA DOL text section for exact (masked) matches.

Usage:
  python tools/compile_search.py src/JSystem/JKernel/JKRDisposer.cpp [--cflags jsystem|dolphin|dolzel]
  python tools/compile_search.py src/dolphin/os/OSTime.c --cflags dolphin

Output: address + size for each matched function; "NO MATCH" if not found.
"""

import argparse, struct, subprocess, sys, re, tempfile, os
from pathlib import Path

REPO      = Path(__file__).resolve().parent.parent
DOL       = REPO / "orig/sys/main.dol"
TEXT_OFF  = 0x2600
TEXT_ADDR = 0x80021840
TEXT_SIZE = 0x43A4A4
COMPILERS = REPO / "build/compilers"

# Compiler path for each MW version
def mwcc(version="GC/1.3.2"):
    return COMPILERS / version.replace("/", "/") / "mwcceppc.exe"

CFLAGS_BASE = [
    "-nodefaults", "-proc gekko", "-align powerpc", "-enum int", "-fp hardware",
    "-Cpp_exceptions off", "-O4,p", "-inline auto",
    '-pragma "cats off"', '-pragma "warn_notinlined off"',
    "-maxerrors 1", "-nosyspath", "-RTTI off", "-fp_contract on",
    "-str reuse", "-multibyte",
    f"-i {REPO}/include", f"-i {REPO}/build/G4SE01/include", f"-i {REPO}/src",
    "-DVERSION=0",
]
CFLAGS_DOLPHIN  = [*CFLAGS_BASE, "-fp_contract off"]
CFLAGS_JSYSTEM  = [*CFLAGS_BASE, "-use_lmw_stmw off", "-str reuse,pool,readonly",
                   "-inline noauto", "-O3,s", "-sym on", "-fp_contract off"]
CFLAGS_DOLZEL   = [*CFLAGS_JSYSTEM, "-schedule off"]

PRESETS = {"dolphin": CFLAGS_DOLPHIN, "jsystem": CFLAGS_JSYSTEM, "dolzel": CFLAGS_DOLZEL}

# Relocation type → instruction word mask (bits to ZERO before matching)
RELOC_MASKS = {
    10: 0xFC000003,  # R_PPC_REL24 — bl offset
    6:  0x0000FFFF,  # R_PPC_ADDR16_HA — upper half
    4:  0x0000FFFF,  # R_PPC_ADDR16_LO — lower half
    2:  0xFFFFFFFF,  # R_PPC_ADDR32 — full word
    3:  0xFFFFFFFF,  # R_PPC_ADDR24
}

def parse_elf_rela(data: bytes, text_off: int, text_size: int):
    """Parse ELF .rela.text and return {text_offset: mask_word}."""
    if data[:4] != b'\x7fELF':
        return {}
    e_shoff = struct.unpack_from(">I", data, 0x20)[0]
    e_shentsize = struct.unpack_from(">H", data, 0x2E)[0]
    e_shnum = struct.unpack_from(">H", data, 0x30)[0]
    masks = {}
    for i in range(e_shnum):
        sh = e_shoff + i * e_shentsize
        sh_type = struct.unpack_from(">I", data, sh+4)[0]
        if sh_type != 4:  # SHT_RELA
            continue
        sh_info = struct.unpack_from(">I", data, sh+0x1C)[0]
        # sh_info points to the section this rela applies to — find .text (sh_info section)
        target_sh = e_shoff + sh_info * e_shentsize
        target_off = struct.unpack_from(">I", data, target_sh+0x10)[0]
        if target_off != text_off:
            continue
        sh_offset = struct.unpack_from(">I", data, sh+0x10)[0]
        sh_size   = struct.unpack_from(">I", data, sh+0x14)[0]
        for j in range(0, sh_size, 12):
            r_offset = struct.unpack_from(">I", data, sh_offset+j)[0]
            r_info   = struct.unpack_from(">I", data, sh_offset+j+4)[0]
            r_type   = r_info & 0xFF
            if r_type in RELOC_MASKS:
                masks[r_offset] = RELOC_MASKS[r_type]
    return masks

def extract_functions(obj_data: bytes):
    """Return list of (name, bytes, mask_array) for each .text function."""
    if obj_data[:4] != b'\x7fELF':
        return []
    e_shoff = struct.unpack_from(">I", obj_data, 0x20)[0]
    e_shentsize = struct.unpack_from(">H", obj_data, 0x2E)[0]
    e_shnum = struct.unpack_from(">H", obj_data, 0x30)[0]
    e_shstrndx = struct.unpack_from(">H", obj_data, 0x32)[0]

    # Build section table
    sections = []
    for i in range(e_shnum):
        sh = e_shoff + i * e_shentsize
        sections.append({
            "name_off": struct.unpack_from(">I", obj_data, sh)[0],
            "type": struct.unpack_from(">I", obj_data, sh+4)[0],
            "offset": struct.unpack_from(">I", obj_data, sh+0x10)[0],
            "size": struct.unpack_from(">I", obj_data, sh+0x14)[0],
            "link": struct.unpack_from(">I", obj_data, sh+0x18)[0],
            "info": struct.unpack_from(">I", obj_data, sh+0x1C)[0],
            "idx": i,
        })

    # String tables
    shstr_sec = sections[e_shstrndx]
    shstr = obj_data[shstr_sec["offset"]:shstr_sec["offset"]+shstr_sec["size"]]

    def sh_name(s):
        end = shstr.index(b'\x00', s["name_off"])
        return shstr[s["name_off"]:end].decode()

    # Find .text sections and symbol table
    text_secs = [s for s in sections if sh_name(s) == ".text"]
    sym_sec = next((s for s in sections if s["type"] == 2), None)  # SHT_SYMTAB
    if not sym_sec or not text_secs:
        return []

    symstr_sec = sections[sym_sec["link"]]
    symstr = obj_data[symstr_sec["offset"]:symstr_sec["offset"]+symstr_sec["size"]]
    sym_data = obj_data[sym_sec["offset"]:sym_sec["offset"]+sym_sec["size"]]
    sym_ent = sym_sec["info"]  # first global symbol

    results = []
    for text_sec in text_secs:
        text_bytes = obj_data[text_sec["offset"]:text_sec["offset"]+text_sec["size"]]
        reloc_masks = parse_elf_rela(obj_data, text_sec["offset"], text_sec["size"])

        # Find all symbols in this section
        fns = []
        for j in range(len(sym_data) // 16):
            st_name = struct.unpack_from(">I", sym_data, j*16)[0]
            st_value = struct.unpack_from(">I", sym_data, j*16+4)[0]
            st_size = struct.unpack_from(">I", sym_data, j*16+8)[0]
            st_info = sym_data[j*16+12]
            st_shndx = struct.unpack_from(">H", sym_data, j*16+14)[0]
            if st_shndx != text_sec["idx"]:
                continue
            st_type = st_info & 0xF
            if st_type != 2:  # STT_FUNC
                continue
            name_end = symstr.index(b'\x00', st_name)
            name = symstr[st_name:name_end].decode()
            if st_size > 0:
                fns.append((st_value, st_size, name))

        fns.sort()
        for off, size, name in fns:
            fn_bytes = bytearray(text_bytes[off:off+size])
            mask = bytearray(size)
            for rel_off, rel_mask in reloc_masks.items():
                if off <= rel_off < off+size:
                    local = rel_off - off
                    # Apply mask to instruction (big-endian word)
                    w = struct.unpack_from(">I", fn_bytes, local)[0]
                    w_masked = w & ~rel_mask
                    struct.pack_into(">I", fn_bytes, local, w_masked)
                    struct.pack_into(">I", mask, local, rel_mask)
            results.append((name, bytes(fn_bytes), bytes(mask)))
    return results

def search_dol(fn_bytes: bytes, mask: bytes):
    """Search FSA DOL text section for fn_bytes (with mask applied to both sides)."""
    with open(DOL, "rb") as f:
        f.seek(TEXT_OFF)
        text = bytearray(f.read(TEXT_SIZE))
    # Zero mask bits in DOL text for comparison
    masked_text = bytearray(TEXT_SIZE)
    for i in range(TEXT_SIZE):
        m = mask[i % len(mask)] if mask else 0
        masked_text[i] = text[i] & ~m
    target = bytes(fn_bytes)
    results, start = [], 0
    while True:
        idx = bytes(masked_text).find(target, start)
        if idx == -1:
            break
        results.append(TEXT_ADDR + idx)
        start = idx + 1
    return results

def compile_file(src: Path, cflags: list, version: str = "GC/1.3.2"):
    """Compile src with mwcc, return .o bytes or None."""
    wrapper = REPO / "build/tools/wibo"
    cc = mwcc(version)
    with tempfile.NamedTemporaryFile(suffix=".o", delete=False) as tf:
        out = tf.name
    # Split multi-word flags (e.g. "-proc gekko" → ["-proc", "gekko"])
    split_flags = []
    for f in cflags:
        split_flags.extend(f.split())
    cmd = [str(wrapper), str(cc)] + split_flags + ["-c", str(src), "-o", out]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    if r.returncode != 0:
        print(f"[!] Compile failed:\n{r.stderr}", file=sys.stderr)
        os.unlink(out)
        return None
    with open(out, "rb") as f:
        data = f.read()
    os.unlink(out)
    return data

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("--cflags", choices=list(PRESETS), default="jsystem")
    ap.add_argument("--mw-version", default="GC/1.3.2")
    args = ap.parse_args()

    print(f"[+] Compiling {args.src} with cflags_{'cflags' if args.cflags else 'jsystem'}...")
    obj = compile_file(args.src, PRESETS[args.cflags], args.mw_version)
    if not obj:
        return 1

    fns = extract_functions(obj)
    print(f"[+] Found {len(fns)} functions, searching DOL...")
    for name, fn_bytes, mask in fns:
        hits = search_dol(fn_bytes, mask)
        if len(hits) == 1:
            print(f"  ✓  0x{hits[0]:08X}  size=0x{len(fn_bytes):X}  {name}")
        elif len(hits) == 0:
            print(f"  ✗  NO MATCH  size=0x{len(fn_bytes):X}  {name}")
        else:
            addrs = ", ".join(f"0x{h:08X}" for h in hits[:5])
            print(f"  ?  {len(hits)} matches: {addrs}  size=0x{len(fn_bytes):X}  {name}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
