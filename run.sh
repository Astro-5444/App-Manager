#!/bin/bash
# App Manager – run from the source tree (native GTK window, random port).
set -e
cd "$(dirname "$0")"

# Use the local venv for Flask, but keep the system python so the GTK/WebKit
# bindings (python3-gi) remain importable.
if [ -d ".venv/lib" ]; then
    SITE="$(find .venv/lib -maxdepth 2 -type d -name site-packages | head -n1)"
    if [ -n "$SITE" ]; then
        export PYTHONPATH="$PWD/$SITE${PYTHONPATH:+:$PYTHONPATH}"
    fi
fi

exec python3 launcher.py "$@"
