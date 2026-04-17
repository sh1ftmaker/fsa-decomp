#!/usr/bin/env bash
# Install tools needed for the FSA decompilation project.
# Run once after cloning. Safe to re-run.
set -e

echo "=== FSA Decompilation Tool Setup ==="

# --- uv (Python package manager) ---
if ! command -v uv &>/dev/null; then
    echo "[+] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Add uv to PATH for the rest of this script
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "[ok] uv $(uv --version)"
fi

# --- m2c (decompiler: assembly → C) ---
M2C_BIN="$HOME/.local/share/uv/tools/m2c/bin/m2c"
if [ ! -f "$M2C_BIN" ]; then
    echo "[+] Installing m2c (matt-kempster/m2c)..."
    uv tool install "git+https://github.com/matt-kempster/m2c.git"
else
    echo "[ok] m2c already installed"
fi

# --- wibo (Windows binary loader for Metrowerks tools on Linux) ---
WIBO="build/tools/wibo"
if [ ! -f "$WIBO" ]; then
    echo "[+] wibo not found — run: python configure.py (it downloads wibo automatically)"
else
    echo "[ok] wibo at $WIBO"
fi

# --- dtk (decomp-toolkit) ---
DTK="build/tools/dtk"
if [ ! -f "$DTK" ]; then
    echo "[+] dtk not found — run: python configure.py (it downloads dtk automatically)"
else
    echo "[ok] dtk at $DTK"
fi

# --- ninja ---
if ! command -v ninja &>/dev/null; then
    echo "[+] Installing ninja..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y ninja-build
    elif command -v brew &>/dev/null; then
        brew install ninja
    else
        echo "[!] Please install ninja manually: https://ninja-build.org/"
    fi
else
    echo "[ok] ninja $(ninja --version)"
fi

echo ""
echo "=== Done. Next steps ==="
echo "  python configure.py   # downloads dtk, wibo, compilers, generates build.ninja"
echo "  ninja                 # builds the project (link step fails on Linux — that's expected)"
echo "  python tools/m2c_batch.py            # mass-convert all unmatched functions"
echo "  python tools/m2c_batch.py --test 10  # quick test: convert 10 functions only"
