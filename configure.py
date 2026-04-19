#!/usr/bin/env python3

###
# Generates build files for the project.
# This file also includes the project configuration,
# such as compiler flags and the object matching status.
#
# Usage:
#   python3 configure.py
#   ninja
#
# Append --help to see available options.
###

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.project import (
    Object,
    ProgressCategory,
    ProjectConfig,
    calculate_progress,
    generate_build,
    is_windows,
)

# Game versions
VERSIONS = [
    "G4SE01",  # 0 - USA
    "G4SJ01",  # 1 - Japan
    "G4SP01",  # 2 - Europe
]
DEFAULT_VERSION = VERSIONS.index("G4SE01")

parser = argparse.ArgumentParser()
parser.add_argument(
    "mode",
    choices=["configure", "progress"],
    default="configure",
    help="script mode (default: configure)",
    nargs="?",
)
parser.add_argument(
    "-v",
    "--version",
    choices=VERSIONS,
    type=str.upper,
    default=VERSIONS[DEFAULT_VERSION],
    help="version to build",
)
parser.add_argument(
    "--build-dir",
    metavar="DIR",
    type=Path,
    default=Path("build"),
    help="base build directory (default: build)",
)
parser.add_argument(
    "--binutils",
    metavar="BINARY",
    type=Path,
    help="path to binutils (optional)",
)
parser.add_argument(
    "--compilers",
    metavar="DIR",
    type=Path,
    help="path to compilers (optional)",
)
parser.add_argument(
    "--map",
    action="store_true",
    help="generate map file(s)",
)
parser.add_argument(
    "--no-asm",
    action="store_true",
    help="don't incorporate .s files from asm directory",
)
parser.add_argument(
    "--debug",
    action="store_true",
    help="build with debug info (non-matching)",
)
if not is_windows():
    parser.add_argument(
        "--wrapper",
        metavar="BINARY",
        type=Path,
        help="path to wibo or wine (optional)",
    )
parser.add_argument(
    "--dtk",
    metavar="BINARY | DIR",
    type=Path,
    help="path to decomp-toolkit binary or source (optional)",
)
parser.add_argument(
    "--objdiff",
    metavar="BINARY | DIR",
    type=Path,
    help="path to objdiff-cli binary or source (optional)",
)
parser.add_argument(
    "--sjiswrap",
    metavar="EXE",
    type=Path,
    help="path to sjiswrap.exe (optional)",
)
parser.add_argument(
    "--ninja",
    metavar="BINARY",
    type=Path,
    help="path to ninja binary (optional)"
)
parser.add_argument(
    "--verbose",
    action="store_true",
    help="print verbose output",
)
parser.add_argument(
    "--non-matching",
    dest="non_matching",
    action="store_true",
    help="builds equivalent (but non-matching) or modded objects",
)
parser.add_argument(
    "--warn",
    dest="warn",
    type=str,
    choices=["all", "off", "error"],
    help="how to handle warnings",
)
parser.add_argument(
    "--no-progress",
    dest="progress",
    action="store_false",
    help="disable progress calculation",
)
args = parser.parse_args()

config = ProjectConfig()
config.version = str(args.version)
version_num = VERSIONS.index(config.version)

# Apply arguments
config.build_dir = args.build_dir
config.dtk_path = args.dtk
config.objdiff_path = args.objdiff
config.binutils_path = args.binutils
config.compilers_path = args.compilers
config.generate_map = args.map
config.non_matching = args.non_matching
config.sjiswrap_path = args.sjiswrap
config.ninja_path = args.ninja
config.progress = args.progress
if not is_windows():
    config.wrapper = args.wrapper
if args.no_asm:
    config.asm_dir = None

# Tool versions
config.binutils_tag = "2.42-1"
config.compilers_tag = "20251118"
config.dtk_tag = "v1.8.3"
config.objdiff_tag = "v3.5.1"
config.sjiswrap_tag = "v1.2.2"
config.wibo_tag = "1.0.0"

# Project
config.config_path = Path("config") / config.version / "config.yml"
config.check_sha_path = Path("config") / config.version / "build.sha1"
config.asflags = [
    "-mgekko",
    "--strip-local-absolute",
    "-I include",
    f"-I build/{config.version}/include",
    f"--defsym version={version_num}",
]
config.ldflags = [
    "-fp hardware",
    "-nodefaults",
]
if args.debug:
    config.ldflags.append("-g")  # Or -gdwarf-2 for Wii linkers
if args.map:
    config.ldflags.append("-mapunused")
    # config.ldflags.append("-listclosure") # For Wii linkers

# Use for any additional files that should cause a re-configure when modified
config.reconfig_deps = []

# Optional numeric ID for decomp.me preset
# Can be overridden in libraries or objects
config.scratch_preset_id = 228 # The Legend of Zelda: Four Swords Adventures (DOL)

# Globs to exclude from context files
# *.mch excludes precompiled header output (which cannot be parsed)
config.context_exclude_globs = ["*.mch"]

# Macro definitions to inject into context files
config.context_defines = ["DECOMPCTX"]

# Base flags, common to most GC/Wii games.
# Generally leave untouched, with overrides added below.
cflags_base = [
    "-nodefaults",
    "-proc gekko",
    "-align powerpc",
    "-enum int",
    "-fp hardware",
    "-Cpp_exceptions off",
    # "-W all",
    "-O4,p",
    "-inline auto",
    '-pragma "cats off"',
    '-pragma "warn_notinlined off"',
    "-maxerrors 1",
    "-nosyspath",
    "-RTTI off",
    "-fp_contract on",
    "-str reuse",
    "-multibyte",  # For Wii compilers, replace with `-enc SJIS`
    "-i include",
    f"-i build/{config.version}/include",
    "-i src",
    "-i src/PowerPC_EABI_Support/MSL/MSL_C/MSL_Common/Include",
    "-i src/PowerPC_EABI_Support/MSL/MSL_C/MSL_Common_Embedded/Math/Include",
    "-i src/PowerPC_EABI_Support/MSL/MSL_C/PPC_EABI/Include",
    "-i src/PowerPC_EABI_Support/MSL/MSL_C++/MSL_Common/Include",
    "-i src/PowerPC_EABI_Support/Runtime/Inc",
    f"-DVERSION={version_num}",
]

# Debug flags
if args.debug:
    # Or -sym dwarf-2 for Wii compilers
    cflags_base.extend(["-sym on", "-DDEBUG=1"])
    cflags_base.extend(['-pragma "dont_inline on"'])
    cflags_base.extend(['-pragma "optimization_level 0"'])
else:
    cflags_base.append("-DNDEBUG=1")

# Warning flags
if args.warn == "all":
    cflags_base.append("-W all")
elif args.warn == "off":
    cflags_base.append("-W off")
elif args.warn == "error":
    cflags_base.append("-W error")

# Metrowerks library flags
cflags_runtime = [
    *cflags_base,
    "-use_lmw_stmw on",
    "-str reuse,pool,readonly",
    "-gccinc",
    "-common off",
    "-inline deferred,auto",
    "-char signed",
]

# Dolphin library flags
cflags_dolphin = [
    *cflags_base,
    "-fp_contract off",
]

# Framework flags
cflags_framework = [
    *cflags_base,
    "-use_lmw_stmw off",
    "-str reuse,pool,readonly",
    "-inline noauto",
    "-O3,s",
    "-schedule off",
    "-sym on",
    "-fp_contract off",
]

# JSystem flags — same as framework but WITHOUT -schedule off
# FSA's JSystem libs were compiled without instruction scheduling
cflags_jsystem = [f for f in cflags_framework if f != "-schedule off"]

# TWW game code flags
cflags_dolzel = [
    *cflags_framework,
]

if config.version == "D44J01":
    cflags_dolzel.extend(['-pragma "opt_propagation off"'])

# REL flags
cflags_rel = [
    *cflags_dolzel,
    "-sdata 0",
    "-sdata2 0",
]

config.linker_version = "GC/1.3.2"

# Glob nonmatch seg files sorted by address for the nonmatch lib
_nonmatch_segs = sorted(
    Path("src/nonmatch").glob("seg_*.c"),
    key=lambda p: int(p.stem.split("_")[1], 16)
)


# Helper function for Dolphin libraries
def DolphinLib(lib_name: str, objects: List[Object]) -> Dict[str, Any]:
    return {
        "lib": lib_name,
        "mw_version": "GC/1.2.5n",
        "cflags": cflags_dolphin,
        "progress_category": "sdk",
        "host": False,
        "objects": objects,
    }


# Helper function for REL script objects
def Rel(lib_name: str, objects: List[Object]) -> Dict[str, Any]:
    return {
        "lib": lib_name,
        "mw_version": "GC/1.3.2",
        "cflags": cflags_rel,
        "progress_category": "game",
        "host": True,
        "objects": objects,
    }


# Helper function for actor RELs
def ActorRel(status, rel_name, extra_cflags=[]):
    return Rel(rel_name, [Object(
        status, f"d/actor/{rel_name}.cpp",
        extra_cflags=extra_cflags,
        scratch_preset_id=228, # The Legend of Zelda: Four Swords Adventures (DOL)
    )])


# Helper function for JSystem libraries
def JSystemLib(lib_name, objects, progress_category="third_party"):
    return {
        "lib": lib_name,
        "mw_version": "GC/1.3.2",
        "cflags": cflags_jsystem,
        "progress_category": progress_category,
        "host": True,
        "objects": objects,
    }

Matching = True                   # Object matches and should be linked
NonMatching = False               # Object does not match and should not be linked
Equivalent = config.non_matching  # Object should be linked when configured with --non-matching


# Object is only matching for specific versions
def MatchingFor(*versions):
    return config.version in versions

def EquivalentFor(*versions):
    return False

config.warn_missing_config = True
config.warn_missing_source = False
config.precompiled_headers = []
config.libs = [
    {
        "lib": "main",
        "mw_version": "GC/1.3.2",
        "cflags": cflags_dolzel,
        "progress_category": "game",
        "host": True,
        "objects": [
            Object(NonMatching, "main/main.cpp"),
            # Main-game matches from 2026-04-19 Gate 4 sweep (see
            # port-agent/configure_additions.txt for the full list).
            Object(Matching, "d/d_camera.cpp"),
            Object(Matching, "d/d_cc_mass_s.cpp"),
            Object(Matching, "d/d_door.cpp"),
            Object(Matching, "d/d_s_menu.cpp"),
            Object(Matching, "d/d_s_name.cpp"),
            Object(Matching, "d/d_save.cpp"),
            Object(Matching, "d/d_stage.cpp"),
            Object(Matching, "f_op/f_op_msg_mng.cpp"),
            Object(Matching, "m_Do/m_Do_dvd_thread.cpp"),
        ],
    },
    DolphinLib("dvd", [
        Object(Matching, "dolphin/dvd/dvd.c"),
        Object(Matching, "dolphin/dvd/dvdfs.c"),
    ]),
    DolphinLib("gx", [
        Object(Matching, "dolphin/gx/GXAttr.c"),
        Object(Matching, "dolphin/gx/GXFifo.c"),
        Object(Matching, "dolphin/gx/GXLight.c"),
        Object(Matching, "dolphin/gx/GXTransform.c"),
    ]),
    DolphinLib("mtx", [
        Object(Matching, "dolphin/mtx/mtx.c"),
        Object(Matching, "dolphin/mtx/mtxvec.c"),
        Object(Matching, "dolphin/mtx/vec.c"),
    ]),
    DolphinLib("os", [
        Object(Matching, "dolphin/os/OS.c"),
        Object(Matching, "dolphin/os/OSCache.c"),
        Object(Matching, "dolphin/os/OSTime.c"),
        Object(Matching, "dolphin/os/OSInterrupt.c"),
        Object(Matching, "dolphin/os/OSSync.c"),
        Object(Matching, "dolphin/os/OSAlloc.c"),
        Object(Matching, "dolphin/os/OSContext.c"),
        Object(Matching, "dolphin/os/OSLink.c"),
        Object(Matching, "dolphin/os/OSMemory.c"),
        Object(Matching, "dolphin/os/OSReboot.c"),
        Object(Matching, "dolphin/os/OSReset.c"),
        Object(Matching, "dolphin/os/OSThread.c"),
    ]),
    JSystemLib("J2DGraph", [
        Object(Matching, "JSystem/J2DGraph/J2DGrafContext.cpp"),
        Object(Matching, "JSystem/J2DGraph/J2DPrint.cpp"),
    ]),
    JSystemLib("J3DGraphAnimator", [
        Object(Matching, "JSystem/J3DGraphAnimator/J3DModel.cpp"),
    ]),
    JSystemLib("J3DGraphBase", [
        Object(Matching, "JSystem/J3DGraphBase/J3DMatBlock.cpp"),
        Object(Matching, "JSystem/J3DGraphBase/J3DMaterial.cpp"),
        Object(Matching, "JSystem/J3DGraphBase/J3DPacket.cpp"),
        Object(Matching, "JSystem/J3DGraphBase/J3DShapeMtx.cpp"),
        Object(Matching, "JSystem/J3DGraphBase/J3DSys.cpp"),
        Object(Matching, "JSystem/J3DGraphBase/J3DTevs.cpp"),
        Object(Matching, "JSystem/J3DGraphBase/J3DVertex.cpp"),
    ]),
    JSystemLib("J3DGraphLoader", [
        Object(Matching, "JSystem/J3DGraphLoader/J3DMaterialFactory.cpp"),
        Object(Matching, "JSystem/J3DGraphLoader/J3DModelLoaderCalcSize.cpp"),
        Object(Matching, "JSystem/J3DGraphLoader/J3DShapeFactory.cpp"),
    ]),
    JSystemLib("JAudio", [
        Object(Matching, "JSystem/JAudio/JASBNKParser.cpp"),
        Object(Matching, "JSystem/JAudio/JASBasicInst.cpp"),
        Object(Matching, "JSystem/JAudio/JASBasicWaveBank.cpp"),
        Object(Matching, "JSystem/JAudio/JASCmdStack.cpp"),
        Object(Matching, "JSystem/JAudio/JASDSPChannel.cpp"),
        Object(Matching, "JSystem/JAudio/JASDSPInterface.cpp"),
        Object(Matching, "JSystem/JAudio/JASDrumSet.cpp"),
    ]),
    JSystemLib("JGadget", [
        Object(Matching, "JSystem/JGadget/linklist.cpp"),
        Object(Matching, "JSystem/JGadget/std-vector.cpp"),
    ]),
    JSystemLib("JKernel", [
        Object(Matching, "JSystem/JKernel/JKRDisposer.cpp"),
        Object(Matching, "JSystem/JKernel/JKRArchivePri.cpp"),
        Object(Matching, "JSystem/JKernel/JKRExpHeap.cpp"),
        Object(Matching, "JSystem/JKernel/JKRFileLoader.cpp"),
        Object(Matching, "JSystem/JKernel/JKRSolidHeap.cpp"),
    ]),
    JSystemLib("JMessage", [
        Object(Matching, "JSystem/JMessage/processor.cpp"),
    ]),
    JSystemLib("JParticle", [
        Object(Matching, "JSystem/JParticle/JPABaseShape.cpp"),
    ]),
    JSystemLib("JSupport", [
        Object(Matching, "JSystem/JSupport/JSUInputStream.cpp"),
        Object(Matching, "JSystem/JSupport/JSUList.cpp"),
        Object(Matching, "JSystem/JSupport/JSUMemoryStream.cpp"),
    ]),
    JSystemLib("JUtility", [
        Object(Matching, "JSystem/JUtility/JUTConsole.cpp"),
        Object(Matching, "JSystem/JUtility/JUTDbPrint.cpp"),
        Object(Matching, "JSystem/JUtility/JUTDirectFile.cpp"),
        Object(Matching, "JSystem/JUtility/JUTException.cpp"),
        Object(Matching, "JSystem/JUtility/JUTFader.cpp"),
        Object(Matching, "JSystem/JUtility/JUTGamePad.cpp"),
        Object(Matching, "JSystem/JUtility/JUTNameTab.cpp"),
        Object(Matching, "JSystem/JUtility/JUTProcBar.cpp"),
        Object(Matching, "JSystem/JUtility/JUTResFont.cpp"),
        Object(Matching, "JSystem/JUtility/JUTXfb.cpp"),
    ]),
    # TODO: 14 PowerPC_EABI_Support units (MSL_C + Runtime) need new helper
    #       functions mirroring DolphinLib — see port-agent/configure_additions.txt.
]


# Build nonmatch seg files via custom ninja steps (they have no DOL range, so
# they can't go through the normal splits.txt / config.json pipeline).
# Strip missing MSL include dirs, add src/nonmatch for nonmatch.h
_nm_cflags = " ".join(
    f for f in cflags_dolzel
    if "PowerPC_EABI_Support" not in f
) + " -i src/nonmatch"
_nm_mw = "GC/1.3.2"
_nonmatch_build_steps = []
for _seg in _nonmatch_segs:
    _src  = Path("src") / "nonmatch" / _seg.name
    _out  = config.build_dir / config.version / "src" / "nonmatch" / _seg.with_suffix(".o").name
    _nonmatch_build_steps.append({
        "outputs": str(_out),
        "rule": "mwcc",
        "inputs": str(_src),
        "variables": {
            "mw_version": _nm_mw,
            "cflags": _nm_cflags,
            "basedir": str(_out.parent),
            "basefile": str(_out.with_suffix("")),
        },
    })

config.custom_build_rules = []
config.custom_build_steps = {
    "pre-compile": _nonmatch_build_steps,
}

# Grab the specific GameID so we can format our strings properly
version = VERSIONS[version_num]
out_dir = config.build_dir / version


# Optional callback to adjust link order. This can be used to add, remove, or reorder objects.
# This is called once per module, with the module ID and the current link order.
#
# For example, this adds "dummy.c" to the end of the DOL link order if configured with --non-matching.
# "dummy.c" *must* be configured as a Matching (or Equivalent) object in order to be linked.
def link_order_callback(module_id: int, objects: List[str]) -> List[str]:
    # Don't modify the link order for matching builds
    if not config.non_matching:
        return objects
    if module_id == 0:  # DOL
        return objects + ["dummy.c"]
    return objects

# Uncomment to enable the link order callback.
# config.link_order_callback = link_order_callback


# Optional extra categories for progress tracking
config.progress_categories = [
    ProgressCategory("game", "FSA Game Code"),
    ProgressCategory("sdk", "SDK"),
    ProgressCategory("third_party", "Third Party"),
]
config.progress_each_module = args.verbose
# Optional extra arguments to `objdiff-cli report generate`
config.progress_report_args = [
    # Marks relocations as mismatching if the target value is different
    # Default is "functionRelocDiffs=none", which is most lenient
    "--config functionRelocDiffs=data_value",
]

# Disable missing return type warnings for incomplete objects
for lib in config.libs:
    for obj in lib["objects"]:
        if not obj.completed:
            obj.options["extra_clang_flags"].append("-Wno-return-type")

if args.mode == "configure":
    # Write build.ninja and objdiff.json
    generate_build(config)
elif args.mode == "progress":
    # Print progress information
    calculate_progress(config)
else:
    sys.exit("Unknown mode: " + args.mode)
