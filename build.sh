#!/usr/bin/env bash
# Build a standalone macOS .app or Linux folder using PyInstaller.
#
# Usage:
#   ./build.sh             # one-folder build (recommended)
#   ./build.sh --onefile   # single-file binary (slower first launch)
#
# Expects a virtualenv at .venv (created by `python -m venv .venv` and
# `.venv/bin/pip install -e .[dev]`).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$SCRIPT_DIR/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "Virtualenv not found at .venv. Run:" >&2
    echo "  python3 -m venv .venv" >&2
    echo "  .venv/bin/pip install -e .[dev]" >&2
    exit 1
fi

ONEFILE=0
for arg in "$@"; do
    case "$arg" in
        --onefile|--one-file|-OneFile) ONEFILE=1 ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

"$VENV_PY" -m pip install --quiet pyinstaller

PYINSTALLER_ARGS=(
    -m PyInstaller
    --noconfirm
    --clean
    --windowed
    --noupx
    --name ai-gauge
    --paths src
    --collect-all PyQt6.QtWebEngineWidgets
    --collect-all PyQt6.QtWebEngineCore
    pyinstaller_entry.py
)

if [ "$(uname -s)" = "Darwin" ]; then
    # Reverse-DNS bundle id; keeps Info.plist + LaunchServices happy.
    PYINSTALLER_ARGS+=(--osx-bundle-identifier org.aigauge.ai-gauge)
fi

if [ "$ONEFILE" -eq 1 ]; then
    PYINSTALLER_ARGS+=(--onefile)
fi

"$VENV_PY" "${PYINSTALLER_ARGS[@]}"

# On macOS, mark the bundle as a menu-bar-only agent so it doesn't show a
# Dock icon. Tradeoff: the floating-widget mode (off by default on Mac)
# also won't appear in Cmd-Tab while LSUIElement is set.
# PyInstaller mis-lays-out QtWebEngineCore.framework on macOS: it puts the
# Helpers directory and the WebEngine resource files at
# Versions/Resources/{Helpers,Resources} instead of Versions/A/{Helpers,Resources},
# so the top-level Helpers and Resources symlinks (which target
# Versions/Current/...) dangle and Qt can't find QtWebEngineProcess or
# icudtl.dat / *.pak.  Relocate them into the proper Versions/A layout.
if [ "$(uname -s)" = "Darwin" ] && [ "$ONEFILE" -eq 0 ]; then
    FWDIR="dist/ai-gauge.app/Contents/Frameworks/PyQt6/Qt6/lib/QtWebEngineCore.framework"
    STRAY="$FWDIR/Versions/Resources"
    if [ -d "$STRAY/Helpers" ] && [ ! -d "$FWDIR/Versions/A/Helpers" ]; then
        mv "$STRAY/Helpers" "$FWDIR/Versions/A/Helpers"
    fi
    if [ -d "$STRAY/Resources" ]; then
        cp -R "$STRAY/Resources/." "$FWDIR/Versions/A/Resources/"
    fi
    if [ -d "$STRAY" ]; then
        rm -rf "$STRAY"
    fi
fi

if [ "$(uname -s)" = "Darwin" ] && [ "$ONEFILE" -eq 0 ] \
        && [ -f "dist/ai-gauge.app/Contents/Info.plist" ]; then
    /usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" \
        dist/ai-gauge.app/Contents/Info.plist 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" \
            dist/ai-gauge.app/Contents/Info.plist
fi

echo
echo "Build complete."
case "$(uname -s)" in
    Darwin)
        if [ "$ONEFILE" -eq 1 ]; then
            echo "Binary: dist/ai-gauge"
        else
            echo "Bundle: dist/ai-gauge.app"
        fi
        ;;
    *)
        if [ "$ONEFILE" -eq 1 ]; then
            echo "Binary: dist/ai-gauge"
        else
            echo "Folder: dist/ai-gauge/  (run ./ai-gauge inside)"
        fi
        ;;
esac
