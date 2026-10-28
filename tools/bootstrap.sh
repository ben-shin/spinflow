#!/usr/bin/env bash
# Install SpinFlow and its compiled native backend in a local Linux virtual environment.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash tools/bootstrap.sh [--test] [--gui]

Creates .venv in this checkout and installs SpinFlow from source. Python 3.12+
and a C++20 compiler are required. --test adds pytest and native build tools;
--gui adds Qt.
Set PYTHON to select an interpreter, for example PYTHON=python3.13.
EOF
}

with_test=0
with_gui=0
for arg in "$@"; do
  case "$arg" in
    --test) with_test=1 ;;
    --gui) with_gui=1 ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$arg" >&2; usage >&2; exit 2 ;;
  esac
done

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
venv="$root/.venv"
python="${PYTHON:-python3}"
"$python" -c 'import sys; assert sys.version_info >= (3, 12), "SpinFlow requires Python 3.12 or newer"'
if ! command -v c++ >/dev/null 2>&1; then
  printf 'A C++20 compiler is required (g++ or clang++).\n' >&2
  exit 1
fi
if [ ! -x "$venv/bin/python" ]; then
  "$python" -m venv "$venv"
fi

extras=""
if [ "$with_test" -eq 1 ] && [ "$with_gui" -eq 1 ]; then
  extras="[test,gui]"
elif [ "$with_test" -eq 1 ]; then
  extras="[test]"
elif [ "$with_gui" -eq 1 ]; then
  extras="[gui]"
fi

"$venv/bin/python" -m pip install --upgrade 'pip>=25,<27'
"$venv/bin/python" -m pip install --only-binary=:all: "$root$extras"
"$venv/bin/python" -m pip check
"$venv/bin/python" -m nmrnext doctor
printf 'Ready: %s\n' "$venv/bin/python"
