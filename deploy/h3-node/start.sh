#!/bin/bash
set -eu
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1 HF_HOME=/opt/ocv-h3/cache PYTHONUNBUFFERED=1
cd /opt/ocv-h3/ComfyUI
exec /opt/ocv-h3/venv/bin/python -s main.py --listen 0.0.0.0 --port 8188 --extra-model-paths-config /opt/ocv-h3/extra_model_paths.yaml --input-directory /opt/ocv-h3/input --output-directory /opt/ocv-h3/output --temp-directory /opt/ocv-h3/temp --disable-auto-launch
