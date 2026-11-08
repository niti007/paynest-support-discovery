# decider/ : Strands Decider 2B service

Model: `StrandsAgents/strands-decider-2B-qwen3.5-v1-2610` (Apache-2.0, LoRA adapter + head on `Qwen/Qwen3.5-2B-Base`, ~5 GB of weights in total).
API: `POST /decide`, `POST /decide_multi`, `GET /health` (no auth). The other two need header `X-A7-Token` equal to env `A7_DECIDER_TOKEN`
(if the env var is unset the server rejects everything). Confidence returned = calibrated probability of the chosen option.
`decide_multi` answers all questions about one ticket (same `context`) in a single shared-prefix forward.

## Run natively on the Mac (this is how the bake-off runs)

```bash
A7_DECIDER_TOKEN=devtoken uv run uvicorn decider.server:app --port 8090     # binds 127.0.0.1 only
curl -s localhost:8090/health
curl -s -X POST localhost:8090/decide -H 'content-type: application/json' -H 'X-A7-Token: devtoken' \
  -d '{"question":"Which team?","options":["payroll","billing","howto"],"context":"masked ticket text"}'
```
Device: MLX if available (Apple silicon, `strands-decider[mlx]`), else MPS, else CPU; override with `DECIDER_DEVICE=mlx|mps|cpu`.
First start downloads the weights into the HF cache (~100 s on first load incl. download). In-process use needs no server: `StrandsDeciderBackend()` in `solution/a7/decide/decider.py`
(set `A7_DECIDER_URL` + `A7_DECIDER_TOKEN` to use the HTTP mode instead).

Memory: measured peak RSS about 2.9 GB on MLX (M2, 16 GB). MPS/torch bf16 is larger; CPU fp32 is roughly 2x the bf16 weights (not measured).

## Container (ARM64) for AWS

```bash
docker build --platform linux/arm64 -t a7-decider -f decider/Dockerfile .                                       # stub smoke image
docker run --rm -p 8080:8080 -e DECIDER_STUB=1 -e A7_DECIDER_TOKEN=x a7-decider                                  # stub: fake deterministic decisions
docker build --platform linux/arm64 --build-arg BAKE_WEIGHTS=1 -t a7-decider:full -f decider/Dockerfile .       # real image
```
- Weights are fetched at BUILD time (`BAKE_WEIGHTS=1`, into `/models`, offline at runtime) because the AWS VPC has no NAT. The default build has no weights and is only for the stub smoke test.
- The container binds 0.0.0.0:8080 (inside the container only). Put it behind the internal ALB / security group; the token is a second layer.
- Docker Desktop here has 3.8 GB and no Metal, so the real model is NOT run in Docker locally.
- Fargate size from the plan: 2 vCPU / 8 GB ARM64. The CPU path (fp32 torso, no MLX) has NOT been measured; expect it to be several times slower than MLX on the M2. Measure on the Phase 6 task before relying on the latency numbers.
