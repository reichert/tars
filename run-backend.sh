#!/bin/bash
# Inicia o backend novo (ditado + reuniões + API local) com as libs CUDA (cuBLAS/cuDNN) do venv.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SITE="$(echo "$DIR"/.venv/lib/python3*/site-packages/nvidia)"

export LD_LIBRARY_PATH="$SITE/cublas/lib:$SITE/cudnn/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec "$DIR/.venv/bin/python" -m whisper_ditado.main "$@"
