#!/bin/bash
# Inicia o daemon de ditado com as libs CUDA (cuBLAS/cuDNN) instaladas via pip no venv.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SITE="$(echo "$DIR"/.venv/lib/python3*/site-packages/nvidia)"

export LD_LIBRARY_PATH="$SITE/cublas/lib:$SITE/cudnn/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec "$DIR/.venv/bin/python" "$DIR/ditado.py" "$@"
