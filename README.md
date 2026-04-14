# The Legend of Zelda: Four Swords Adventures - Decompilation

A work-in-progress decompilation of The Legend of Zelda: Four Swords Adventures (GameCube, USA).

[![Progress](https://img.shields.io/endpoint?url=https://decomp.me/api/project/TODO/badge/)](https://decomp.me/project/TODO)

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

- Python 3.6+
- ninja
- A copy of the original game (not provided)

All other tools (compiler, dtk, objdiff) are downloaded automatically.

### Setup

1. Extract the game using Dolphin Emulator:
   - Right-click the game → Properties → Filesystem tab → Right-click the root → Export Partition Root
   - Place the result in `orig/` so that `orig/sys/main.dol` exists

2. Build:
   ```bash
   python3 configure.py
   ninja
   ```

The first build will download all required tools including the Metrowerks compiler.

### Verifying with objdiff

Run `objdiff` (or `build/tools/objdiff-cli`) in the project root. Functions can be browsed and compared in the `fsa_game_code/main/main` unit.

## Contributing

### Decompiling a function

1. Find a function of interest in objdiff
2. Write equivalent C/C++ code in `src/`
3. Register it in `configure.py` under `config.libs`
4. Update `config/G4SE01/splits.txt` to split it into its own unit
5. Run `ninja` and verify the match in objdiff

### decomp.me scratches

Create a scratch on [decomp.me](https://decomp.me) using the **FSA (DOL)** preset. This lets you iterate on functions in the browser without a local build setup.

## Project Structure

```
configure.py          - Build configuration and object list
config/G4SE01/
  splits.txt          - Maps DOL sections to source files
  symbols.txt         - Symbol name definitions
  build.sha1          - Expected hash of matching build
src/                  - Decompiled C/C++ source files
orig/                 - Original game files (not in repo)
build/                - Build output (not in repo)
```

## Related Projects

- [zeldaret/ww](https://github.com/zeldaret/ww) - Wind Waker decompilation (same engine)
- [zeldaret/oot](https://github.com/zeldaret/oot) - Ocarina of Time decompilation

## License

The decompiled source code in this repository is the result of reverse engineering and is provided for educational and preservation purposes only. The original game assets and code are the property of Nintendo.
