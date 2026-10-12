# Rebuilds compiler/runtime dependencies without touching the TTS base image.
# The deployed wheel/image manifest records the exact base ID and wheel SHA256.
ARG BASE_IMAGE=pytorch-env:2.11-tts25-rayfix2
FROM ${BASE_IMAGE}
ARG SAGE_COMMIT=d1a57a546c3d395b1ffcbeecc66d81db76f3b4b5
ENV DEBIAN_FRONTEND=noninteractive \
    CUDA_HOME=/usr/local/cuda-13.0 \
    TORCH_CUDA_ARCH_LIST=8.9 \
    MAX_JOBS=1 EXT_PARALLEL=1 NVCC_APPEND_FLAGS="--threads 1"
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git ca-certificates cuda-nvcc-13-0 cuda-cudart-dev-13-0
RUN /home/hy/miniconda3/envs/pytorch2.11/bin/python -m pip install ninja==1.13.2 wheel==0.46.3
RUN /home/hy/miniconda3/envs/pytorch2.11/bin/python -c "from pathlib import Path; root=Path('/home/hy/miniconda3/envs/pytorch2.11/lib/python3.13/site-packages/nvidia'); target=Path('/usr/local/cuda-13.0/targets/x86_64-linux/include'); [(target/h.name).symlink_to(h) for d in root.glob('*/include') for h in d.iterdir() if not (target/h.name).exists()]"
RUN git clone https://github.com/thu-ml/SageAttention.git /opt/sage-source && \
    cd /opt/sage-source && git checkout ${SAGE_COMMIT} && \
    /home/hy/miniconda3/envs/pytorch2.11/bin/python -m pip wheel . --no-build-isolation --no-deps -w /opt/ocv-image-wheels && \
    /home/hy/miniconda3/envs/pytorch2.11/bin/python -m pip install --no-deps /opt/ocv-image-wheels/sageattention-2.2.0-*.whl
# Existing /opt/ocv-h3/venv and ComfyUI are mounted by compose. That venv uses
# system site packages from the unchanged /home/hy/miniconda3/envs/pytorch2.11.
ENTRYPOINT ["/bin/bash", "/opt/ocv-h3/start.sh"]
