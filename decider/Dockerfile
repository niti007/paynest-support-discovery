# Strands Decider 2B service. Build for ARM64 (Graviton Fargate):
#   docker build --platform linux/arm64 -t a7-decider -f decider/Dockerfile .                       # stub smoke image (no weights)
#   docker build --platform linux/arm64 --build-arg BAKE_WEIGHTS=1 -t a7-decider:full -f decider/Dockerfile .   # AWS image
# Weights: BAKE_WEIGHTS=1 downloads both repos (adapter + Qwen3.5-2B-Base, ~5 GB) at BUILD time into /models and sets
# HF_HUB_OFFLINE=1. The AWS VPC has no NAT, so the AWS image MUST be built this way (no startup download possible).
# BAKE_WEIGHTS=0 (default) is for the stub smoke test only.
FROM python:3.12-slim

ARG BAKE_WEIGHTS=0
ENV PYTHONUNBUFFERED=1 HF_HOME=/models PIP_NO_CACHE_DIR=1

WORKDIR /app
# CPU torch + strands-decider (cpu extra) + server deps. strands-decider is imported lazily, so stub mode never loads it.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.7.1 \
 && pip install "strands-decider[cpu]==0.3.0" "fastapi>=0.115" "uvicorn[standard]>=0.30"

RUN if [ "$BAKE_WEIGHTS" = "1" ]; then \
      python -c "from huggingface_hub import snapshot_download as s; s('StrandsAgents/strands-decider-2B-qwen3.5-v1-2610'); s('Qwen/Qwen3.5-2B-Base')" \
      && echo "HF_HUB_OFFLINE=1" > /app/.offline; \
    fi

COPY decider /app/decider
ENV DECIDER_DEVICE=cpu
EXPOSE 8080
# A7_DECIDER_TOKEN must be supplied at runtime (Secrets Manager -> env). Without it every request is rejected (401).
CMD ["sh", "-c", "[ -f /app/.offline ] && export HF_HUB_OFFLINE=1; exec uvicorn decider.server:app --host 0.0.0.0 --port 8080"]
