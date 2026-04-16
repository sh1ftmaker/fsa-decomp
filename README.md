# The Legend of Zelda: Four Swords Adventures - Decompilation

A work-in-progress decompilation of The Legend of Zelda: Four Swords Adventures (GameCube, USA).

## Overview

| Property | Value |
|----------|-------|
| Game | The Legend of Zelda: Four Swords Adventures |
| Platform | Nintendo GameCube |
| Game ID | G4SE01 (USA) |
| Compiler | Metrowerks CodeWarrior GC/1.3.2 |
| Build System | ninja + decomp-toolkit |

## Building

### Prerequisites

- **Python** 3.6+ — [python.org](https://www.python.org/downloads/)
- **ninja** — [github.com/ninja-build/ninja/releases](https://github.com/ninja-build/ninja/releases) (or `winget install Ninja-build.Ninja` / `brew install ninja`)
- A copy of the original game (not provided)

All other tools — including the Metrowerks compiler, dtk, and objdiff — are downloaded automatically on the first build.

### Game extraction

Extract the game using [Dolphin Emulator](https://dolphin-emu.org/download/):

1. Add the game to Dolphin
2. Right-click → Properties → Filesystem tab
3. Right-click the root → **Export Partition Root**
4. Place the exported files in `orig/` so that `orig/sys/main.dol` exists

### Build (Linux / macOS)

```sh
python3 configure.py
ninja
```

### Build (Windows)

```bat
python configure.py
ninja
```

On Windows, the Metrowerks compiler runs natively — no Wine or WSL required.

> **Note for macOS:** Wine must be installed (`brew install --cask wine-stable`) since `wibo` only runs on Linux.

### Verifying with objdiff

1. Download the objdiff GUI from [github.com/encounter/objdiff/releases](https://github.com/encounter/objdiff/releases) for your platform
2. Place the binary in the project root and run it from there:
   ```sh
   # Linux / macOS
   ./objdiff

   # Windows
   objdiff-windows-x86_64.exe
   ```
3. objdiff will pick up `objdiff.json` automatically and load the project

All 11,556 functions from the original DOL are browsable under `fsa_game_code/main/main`. As you decompile functions, matching ones turn green.

## Notes for Contributors

### Splitting out a new source file

When you carve a range out of `main/main.cpp` into a new source file, the `extabindex` section
must be split to match. Every `extabindex` entry references the function it belongs to, so the
entry for any function you move must live in the same object as that function. Assign the
corresponding tail of `extabindex` to the new file in `splits.txt`. Failing to do so will cause
`dtk dol split` to abort with:

```
Bad extabindex relocation @ <address>
```

### `config/G4SE01/symbols.txt` has Wind Waker addresses

The symbols file was initially seeded from the Wind Waker decompilation. All function **names**
and **sizes** are correct — FSA and TWW share the same codebase and the same function set —
but the **addresses** for `.text` functions are TWW addresses, not FSA addresses.

This does not affect the build. `dtk` uses `splits.txt` addresses for all splitting and linking.
As functions are decompiled and their address ranges added to `splits.txt`, the correct FSA
addresses replace the TWW placeholders automatically.

---

## Contributing

### Decompiling a function

1. Find a function of interest in objdiff
2. Write equivalent C/C++ source in `src/`
3. Add it to `configure.py` under `config.libs`
4. Update `config/G4SE01/splits.txt` to map its address range to the new source file
5. Run `ninja` and verify the match turns green in objdiff

### decomp.me scratches

You can work on individual functions in the browser at [decomp.me](https://decomp.me) without a local build setup. Use the **[The Legend of Zelda: Four Swords Adventures (DOL)](https://decomp.me/preset/228)** preset (preset 228).

## Project Structure

```
configure.py          - Build configuration and object list
config/G4SE01/
  splits.txt          - Maps DOL address ranges to source files
  symbols.txt         - Symbol name definitions
  build.sha1          - Expected SHA1 of a fully-matching build
src/                  - Decompiled C/C++ source files
orig/                 - Original game files (not in repo)
build/                - Build output (not in repo)
```

## License

The decompiled source code in this repository is the result of reverse engineering and is provided for educational and preservation purposes only. The original game assets and code are the property of Nintendo.
