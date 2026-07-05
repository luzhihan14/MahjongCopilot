#!/usr/bin/env bash
# MahjongCopilot dev environment setup (macOS / Linux).
#
# Does everything needed to go from a fresh clone to a runnable app:
#   1. Finds (or creates) a Python 3.10-3.12 environment
#      - uses ./venv if it exists
#      - else creates ./venv from a suitable python on PATH
#      - else creates/reuses the "mjcopilot" conda env (python 3.11)
#   2. Installs requirements.txt (+ "numpy<2": numpy 2.x breaks the pinned torch 2.2.1)
#   3. Installs Playwright Chromium (PLAYWRIGHT_BROWSERS_PATH=0)
#   4. Downloads AI models from the Akagi v3 model repo
#      (github.com/shinkuan/Akagi-MjaiBot-Mortal) and installs:
#        models/mortal.pth      (4P)  models/mortal_3p.pth  (3P)
#        libriichi3p/*.so|*.pyd (3P engine bindings, all platforms)
#      NOTE: these are weak placeholder weights, for verifying the install.
#      Stronger models: https://discord.com/invite/Z2wjXUK8bN (Akagi Discord)
#   5. Runs a smoke test: imports, model load, one AI move (4P and 3P),
#      headless Chromium launch
#
# Env vars:
#   PYTHON_BIN=/path/to/python   use a specific interpreter (must be 3.10-3.12)
#   FORCE_MODELS=1               re-download models even if already present
#
# Windows users: see the README sample script / scripts/generate_exe.bat instead.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

MODEL_RELEASE_URL="https://github.com/shinkuan/Akagi-MjaiBot-Mortal/releases/download/v0.1.0"

step()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info()  { printf '    %s\n' "$*"; }
die()   { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

version_ok() {  # $1 = python binary; true if 3.10 <= version <= 3.12
    "$1" -c 'import sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)' 2>/dev/null
}

find_conda() {
    if command -v conda >/dev/null 2>&1; then command -v conda; return 0; fi
    for c in /opt/anaconda3/bin/conda /opt/miniconda3/bin/conda \
             "$HOME/anaconda3/bin/conda" "$HOME/miniconda3/bin/conda"; do
        [ -x "$c" ] && { echo "$c"; return 0; }
    done
    return 1
}

# ---------------------------------------------------------------- 1. python env
step "Locating Python 3.10-3.12 environment"
PY=""
ENV_KIND=""

if [ -x "venv/bin/python" ]; then
    version_ok "venv/bin/python" || die "./venv exists but its Python is not 3.10-3.12. Remove ./venv and re-run."
    PY="$ROOT/venv/bin/python"
    ENV_KIND="venv"
    info "Reusing existing ./venv"
else
    for cand in "${PYTHON_BIN:-}" python3.11 python3.12 python3.10 python3 python; do
        [ -n "$cand" ] || continue
        command -v "$cand" >/dev/null 2>&1 || continue
        if version_ok "$cand"; then
            info "Creating ./venv from $cand ($("$cand" --version 2>&1))"
            "$cand" -m venv venv
            PY="$ROOT/venv/bin/python"
            ENV_KIND="venv"
            break
        fi
    done
fi

if [ -z "$PY" ]; then
    CONDA="$(find_conda)" || die "No Python 3.10-3.12 found and no conda available.
Install Python 3.11 (e.g. 'brew install python@3.11') or Miniconda, then re-run."
    CONDA_BASE="$("$CONDA" info --base)"
    ENV_PREFIX="$CONDA_BASE/envs/mjcopilot"
    if [ -x "$ENV_PREFIX/bin/python" ]; then
        info "Reusing conda env 'mjcopilot'"
    else
        info "Creating conda env 'mjcopilot' (python 3.11)"
        "$CONDA" create -y -n mjcopilot python=3.11
    fi
    PY="$ENV_PREFIX/bin/python"
    ENV_KIND="conda"
fi

version_ok "$PY" || die "Resolved interpreter $PY is not Python 3.10-3.12"
info "Using: $PY ($("$PY" --version 2>&1))"

# ---------------------------------------------------------------- 2. dependencies
step "Installing dependencies (requirements.txt + numpy<2)"
"$PY" -m pip install --upgrade pip -q
"$PY" -m pip install -r requirements.txt "numpy<2"

# ---------------------------------------------------------------- 3. chromium
step "Installing Playwright Chromium (PLAYWRIGHT_BROWSERS_PATH=0)"
PLAYWRIGHT_BROWSERS_PATH=0 "$PY" -m playwright install chromium

# ---------------------------------------------------------------- 4. AI models
step "Installing AI models (Akagi v3 placeholder weights)"
have_3p_bindings() { ls libriichi3p/libriichi3p-* >/dev/null 2>&1; }
if [ "${FORCE_MODELS:-0}" != "1" ] && [ -f models/mortal.pth ] && [ -f models/mortal_3p.pth ] && have_3p_bindings; then
    info "Models already present (set FORCE_MODELS=1 to re-download) - skipping"
else
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    for rel in release4p release3p; do
        info "Downloading $rel.zip"
        curl -fL --retry 3 -o "$TMP/$rel.zip" "$MODEL_RELEASE_URL/$rel.zip"
        if command -v unzip >/dev/null 2>&1; then
            unzip -o -q "$TMP/$rel.zip" -d "$TMP/$rel"
        else
            "$PY" -m zipfile -e "$TMP/$rel.zip" "$TMP/$rel"
        fi
    done
    mkdir -p models
    cp "$TMP/release4p/mortal.pth" models/mortal.pth
    cp "$TMP/release3p/mortal.pth" models/mortal_3p.pth
    # 3P engine bindings: all platforms, flat into libriichi3p/ (per mjcopilot.com/help)
    cp "$TMP"/release3p/libriichi/libriichi3p-* libriichi3p/
    info "Installed models/mortal.pth, models/mortal_3p.pth, libriichi3p bindings"
    info "NOTE: placeholder weights - weak by design. Stronger models via Akagi Discord."
fi

# ---------------------------------------------------------------- 5. smoke test
step "Running smoke test"
PLAYWRIGHT_BROWSERS_PATH=0 PYTHONPATH="$ROOT" "$PY" - <<'PYEOF'
import sys
print(f"    python {sys.version.split()[0]}")
import numpy, torch
from mitmproxy import version as mitm_version
print(f"    torch {torch.__version__} / numpy {numpy.__version__} / mitmproxy {mitm_version.VERSION}")

from common.utils import GameMode
from bot.local.bot_local import BotMortalLocal
bot = BotMortalLocal({GameMode.MJ4P: "models/mortal.pth", GameMode.MJ3P: "models/mortal_3p.pth"})
modes = [m.value for m in bot.supported_modes]
print(f"    local bot modes: {modes}")
assert "4P" in modes, "4P model failed to load"

def first_move(mode, scores, tehais, tsumo_pai):
    bot.init_bot(seat=0, mode=mode)
    reaction = bot.react_batch([
        {"type": "start_game", "id": 0},
        {"type": "start_kyoku", "bakaze": "E", "dora_marker": "2s", "kyoku": 1,
         "honba": 0, "kyotaku": 0, "oya": 0, "scores": scores, "tehais": tehais},
        {"type": "tsumo", "actor": 0, "pai": tsumo_pai},
    ])
    assert reaction and reaction["type"] == "dahai", f"{mode} bot gave no discard"
    print(f"    {mode.value} bot first move: discard {reaction['pai']}")

first_move(GameMode.MJ4P, [25000] * 4,
           [["1m", "2m", "3m", "7m", "8m", "9m", "1p", "2p", "3p", "W", "W", "F", "C"],
            ["?"] * 13, ["?"] * 13, ["?"] * 13], "5p")
if "3P" in modes:  # scores/tehais stay 4-long with a dummy seat, as game_state.py sends them
    first_move(GameMode.MJ3P, [35000, 35000, 35000, 0],
               [["1m", "9m", "1p", "2p", "3p", "7p", "8p", "9p", "1s", "2s", "3s", "W", "C"],
                ["?"] * 13, ["?"] * 13, ["?"] * 13], "5s")
else:
    print("    WARNING: 3P mode unavailable (libriichi3p binding missing for this python/platform)")

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    browser.new_page().goto("about:blank")
    ver = browser.version
    browser.close()
print(f"    chromium {ver}: ok")
print("\n    ALL CHECKS PASSED")
PYEOF

# ---------------------------------------------------------------- done
step "Setup complete"
echo
echo "To run the app (PLAYWRIGHT_BROWSERS_PATH=0 is required at runtime):"
if [ "$ENV_KIND" = "conda" ]; then
    echo "    conda activate mjcopilot"
else
    echo "    source venv/bin/activate"
fi
echo "    PLAYWRIGHT_BROWSERS_PATH=0 python main.py"
echo
echo "Notes:"
echo "  - First launch installs the MITM CA certificate via sudo; run from a"
echo "    terminal so it can prompt, or install manually if it fails:"
echo "    sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain mitm_config/mitmproxy-ca-cert.pem"
echo "  - Bundled models are weak placeholders. Stronger weights: Akagi Discord"
echo "    (https://discord.com/invite/Z2wjXUK8bN) -> replace files in models/."
echo "    Online alternatives: MJAPI or AkagiOT (configure in Settings)."
