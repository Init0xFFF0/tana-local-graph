#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$(pwd)/src${PYTHONPATH:+:$PYTHONPATH}"
"${PYTHON_BIN:-python3}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ required; select it with PYTHON_BIN")'
if [ "$#" -eq 0 ]; then set -- --demo; fi
exec "${PYTHON_BIN:-python3}" -m tana_graph "$@"
