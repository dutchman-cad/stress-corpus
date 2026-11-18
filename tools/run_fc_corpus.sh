#!/usr/bin/env bash
# User-level Linux x86_64 bootstrap and resumable 100-batch run.
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
corpus_dir=${FC_CORPUS_ROOT:-"$HOME/xpswork/fc-corpus"}
mkdir -p "$corpus_dir"
exec 9>"$corpus_dir/run.lock"
flock -n 9 || { echo 'Another corpus run holds run.lock' >&2; exit 1; }
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
    echo 'This bootstrap requires Linux x86_64' >&2
    exit 1
fi
fc_cmd="$corpus_dir/tools/FreeCADCmd"
dm_cmd="$corpus_dir/tools/dutchman-0.2.10-x86_64-unknown-linux-gnu/dutchman"
# Installation peaks below 6 GiB, including both downloaded binary archives.
# Check before allocating them; never delete unrelated files to make room.
required_free=21
if [[ ! -x "$fc_cmd" || ! -x "$dm_cmd" ]]; then required_free=26; fi
python3 - "$corpus_dir" "$required_free" <<'PY'
import shutil, sys
free = shutil.disk_usage(sys.argv[1]).free / 1024**3
need = float(sys.argv[2])
if free < need:
    raise SystemExit(f"{free:.2f} GiB free; need {need:g} GiB before setup/run to keep 20 GiB free. Free space and rerun the same command.")
PY
mkdir -p "$corpus_dir/tools"
cd "$corpus_dir/tools"
if [[ ! -x "$fc_cmd" ]]; then
    curl --fail --location --retry 3 https://github.com/FreeCAD/FreeCAD/releases/download/1.1.4/FreeCAD_1.1.4-Linux-x86_64-py311.AppImage -o FreeCAD.AppImage.part
    chmod +x FreeCAD.AppImage.part
    ./FreeCAD.AppImage.part --appimage-extract > "$corpus_dir/freecad-extract.log"
    rm -- FreeCAD.AppImage.part
    cat > "$fc_cmd" <<'LAUNCHER'
#!/bin/sh
fc_tools_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONHOME="$fc_tools_dir/squashfs-root/usr"
export LD_LIBRARY_PATH="$PYTHONHOME/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$PYTHONHOME/bin/freecadcmd" "$@"
LAUNCHER
    chmod +x "$fc_cmd"
fi
if [[ ! -x "$dm_cmd" ]]; then
    curl --fail --location --retry 3 https://github.com/dutchman-cad/dutchman/releases/download/v0.2.10/dutchman-0.2.10-x86_64-unknown-linux-gnu.tar.gz -o dutchman.tar.gz.part
    tar -xzf dutchman.tar.gz.part
    rm -- dutchman.tar.gz.part
fi
# Limit numerical/codec thread pools so four items fit the 12-core host.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
cd "$repo_dir"
exec python3 -u freecad.py --all-batches --freecad "$fc_cmd" --dutchman "$dm_cmd" \
    --cli "$HOME/.local/bin/solidcraft-cli" --jobs 4 --timeout 600 \
    --work-dir "$corpus_dir" --min-free-gb 20
