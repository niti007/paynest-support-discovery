# Decision-layer bake-off

Backends: decider, laya, laya_calibrated, bedrock_qwen32b, bedrock_nova. Volume assumption: 15150 tickets/month.

## Winner

**bedrock_nova**

Rule: highest clean routing accuracy on the common clean ids (3 dp), then ECE, then USD/month; injection is not an eligibility filter. The low-confidence threshold is chosen on the calib split and applied to eval.

95% auto-lane accuracy is NOT reachable on the calib split (n=0) at any threshold.

| backend | clean routing acc (common clean ids, n) | ECE | injection recall (safety) | recall + guard | USD/month |
|---|---|---|---|---|---|
| bedrock_nova | 82.0% (n=300) | 0.084 | 90.0% | 96.7% | 0.57 |
| bedrock_qwen32b | 77.3% (n=300) | 0.176 | 90.0% | 95.0% | 1.31 |
| decider | 53.3% (n=300) | 0.184 | 60.0% | 73.3% | 74.75 |
| laya_calibrated | 38.0% (n=300) | 0.218 | 35.0% | 55.0% | 0.00 |
| laya | 38.0% (n=300) | 0.487 | 35.0% | 55.0% | 0.00 |

## Cost (USD per month)

- decider: $74.75. 1 Fargate ARM task 2 vCPU / 8 GB x 730 h at assumed $0.0356/vCPU-h + $0.0039/GB-h
- laya: $0.00. runs in-process in the worker task; ~0 incremental
- laya_calibrated: $0.00. runs in-process in the worker task; ~0 incremental
- bedrock_qwen32b: $1.31. qwen.qwen3-32b-v1:0: ~335 in / 60 out tokens per ticket x 15150
- bedrock_nova: $0.57. eu.amazon.nova-lite-v1:0: ~335 in / 60 out tokens per ticket x 15150
- Local-model latency below was measured on an Apple M2 (MLX/MPS), not on Fargate 2 vCPU CPU; expect slower on Fargate.

## Set: routing

Set size 1327; ids common to all backends with preds: 300; missing preds: none.

### Accuracy per question (own ids / common ids)

| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |
|---|---|---|---|---|---|---|---|---|
| decider | 1327 | 54.6% | 62.6% | 100.0% | 300 | 53.3% | 59.0% | 100.0% |
| laya | 1061 | 36.3% | 59.6% | 82.3% | 300 | 38.0% | 56.7% | 83.0% |
| laya_calibrated | 1061 | 36.3% | 59.6% | 82.3% | 300 | 38.0% | 56.7% | 83.0% |
| bedrock_qwen32b | 1327 | 77.9% | 53.1% | 100.0% | 300 | 77.3% | 52.0% | 100.0% |
| bedrock_nova | 300 | 82.0% | 42.0% | 99.3% | 300 | 82.0% | 42.0% | 99.3% |

### Clean vs errored tickets, baselines

| backend | n_clean | n_error | keep-initial baseline (n) | acc on misrouted tickets (n) | eval acc / calib-picked threshold / eval auto acc / eval human share |
|---|---|---|---|---|---|
| decider | 1327 | 0 | 84.9% (n=1327) | 80.2% (n=111) | 54.8% (n=1061) / 0.9 / 100.0% (n=133) / 87.5% |
| laya | 1061 | 0 | 84.7% (n=1061) | 54.3% (n=92) | 36.3% (n=1061) / none reaches 95% on calib (n=0) / n/a (n=None) / n/a |
| laya_calibrated | 1061 | 0 | 84.7% (n=1061) | 54.3% (n=92) | 36.3% (n=1061) / none reaches 95% on calib (n=0) / n/a (n=None) / n/a |
| bedrock_qwen32b | 1327 | 0 | 84.9% (n=1327) | 85.6% (n=111) | 77.7% (n=1061) / 0.99 / n/a (n=0) / 100.0% |
| bedrock_nova | 300 | 0 | 88.0% (n=300) | 91.7% (n=24) | 82.0% (n=300) / none reaches 95% on calib (n=0) / n/a (n=None) / n/a |

injection_guard trigger rate on this set (its cost in human share): en 0/1035 (0.0%), hi 0/61 (0.0%), ar 0/76 (0.0%), hinglish 0/155 (0.0%), all 0/1327 (0.0%)


### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 1327 | 631 | 698 |
| laya | 1061 | 73 | 86 |
| laya_calibrated | 1061 | 73 | 86 |
| bedrock_qwen32b | 1327 | 3949 | 4202 |
| bedrock_nova | 300 | 18010 | 18124 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 935 | 57.8% | 392 | 46.9% | -10.8% | 42.2% |
| laya | 751 | 39.9% | 310 | 27.4% | -12.5% | 60.1% |
| laya_calibrated | 751 | 39.9% | 310 | 27.4% | -12.5% | 60.1% |
| bedrock_qwen32b | 935 | 78.0% | 392 | 77.8% | -0.2% | 22.0% |
| bedrock_nova | 212 | 81.1% | 88 | 84.1% | 3.0% | 18.9% |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 50.6% (n=1035) | 90.2% (n=61) | 48.7% (n=76) | 69.7% (n=155) |
| laya | 35.1% (n=828) | 30.6% (n=49) | 88.3% (n=60) | 21.0% (n=124) |
| laya_calibrated | 35.1% (n=828) | 30.6% (n=49) | 88.3% (n=60) | 21.0% (n=124) |
| bedrock_qwen32b | 74.4% (n=1035) | 90.2% (n=61) | 80.3% (n=76) | 95.5% (n=155) |
| bedrock_nova | 78.5% (n=237) | 100.0% (n=11) | 91.7% (n=12) | 95.0% (n=40) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 90 | 92.2% | 945 | 46.7% | -45.6% |
| decider | hi | 6 | 33.3% | 55 | 96.4% | 63.0% |
| decider | ar | 62 | 37.1% | 14 | 100.0% | 62.9% |
| decider | hinglish | 0 | n/a | 155 | 69.7% | n/a |
| laya | en | 70 | 78.6% | 758 | 31.1% | -47.4% |
| laya | hi | 4 | 0.0% | 45 | 33.3% | 33.3% |
| laya | ar | 49 | 89.8% | 11 | 81.8% | -8.0% |
| laya | hinglish | 0 | n/a | 124 | 21.0% | n/a |
| laya_calibrated | en | 70 | 78.6% | 758 | 31.1% | -47.4% |
| laya_calibrated | hi | 4 | 0.0% | 45 | 33.3% | 33.3% |
| laya_calibrated | ar | 49 | 89.8% | 11 | 81.8% | -8.0% |
| laya_calibrated | hinglish | 0 | n/a | 124 | 21.0% | n/a |
| bedrock_qwen32b | en | 90 | 96.7% | 945 | 72.3% | -24.4% |
| bedrock_qwen32b | hi | 6 | 33.3% | 55 | 96.4% | 63.0% |
| bedrock_qwen32b | ar | 62 | 75.8% | 14 | 100.0% | 24.2% |
| bedrock_qwen32b | hinglish | 0 | n/a | 155 | 95.5% | n/a |
| bedrock_nova | en | 19 | 94.7% | 218 | 77.1% | -17.7% |
| bedrock_nova | hi | 0 | n/a | 11 | 100.0% | n/a |
| bedrock_nova | ar | 11 | 90.9% | 1 | 100.0% | 9.1% |
| bedrock_nova | hinglish | 0 | n/a | 40 | 95.0% | n/a |

### Calibration (category, own ids)

**decider**: ECE (10 bins) = 0.174

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 1 | 0.289 | 1.000 |
| 0.3-0.4 | 77 | 0.366 | 0.532 |
| 0.4-0.5 | 78 | 0.449 | 0.256 |
| 0.5-0.6 | 328 | 0.551 | 0.253 |
| 0.6-0.7 | 264 | 0.645 | 0.561 |
| 0.7-0.8 | 149 | 0.753 | 0.550 |
| 0.8-0.9 | 261 | 0.860 | 0.690 |
| 0.9-1.0 | 169 | 0.957 | 1.000 |

**laya**: ECE (10 bins) = 0.489

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 17 | 0.158 | 0.588 |
| 0.2-0.3 | 23 | 0.236 | 0.000 |
| 0.3-0.4 | 19 | 0.321 | 0.000 |
| 0.4-0.5 | 131 | 0.462 | 0.252 |
| 0.5-0.6 | 18 | 0.534 | 0.056 |
| 0.6-0.7 | 43 | 0.663 | 0.047 |
| 0.7-0.8 | 39 | 0.768 | 0.231 |
| 0.8-0.9 | 131 | 0.862 | 0.420 |
| 0.9-1.0 | 640 | 0.989 | 0.430 |

**laya_calibrated**: ECE (10 bins) = 0.185

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 489 | 0.162 | 0.270 |
| 0.2-0.3 | 148 | 0.222 | 0.500 |
| 0.3-0.4 | 76 | 0.375 | 0.368 |
| 0.4-0.5 | 31 | 0.457 | 0.935 |
| 0.5-0.6 | 78 | 0.564 | 0.128 |
| 0.6-0.7 | 239 | 0.692 | 0.469 |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 0 | n/a | n/a |
| 0.9-1.0 | 0 | n/a | n/a |

**bedrock_qwen32b**: ECE (10 bins) = 0.170

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 0 | n/a | n/a |
| 0.6-0.7 | 0 | n/a | n/a |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 2 | 0.850 | 1.000 |
| 0.9-1.0 | 1325 | 0.949 | 0.779 |

**bedrock_nova**: ECE (10 bins) = 0.084

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 0 | n/a | n/a |
| 0.6-0.7 | 0 | n/a | n/a |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 0 | n/a | n/a |
| 0.9-1.0 | 300 | 0.904 | 0.820 |

### Low-confidence-to-human sweep (category, own ids)

**decider**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 11.8% | 1171 | 56.5% |
| 0.55 | 21.7% | 1039 | 62.6% |
| 0.60 | 36.5% | 843 | 68.7% |
| 0.65 | 47.7% | 694 | 71.3% |
| 0.70 | 56.4% | 579 | 74.4% |
| 0.75 | 62.9% | 492 | 82.3% |
| 0.80 | 67.6% | 430 | 81.2% |
| 0.85 | 75.1% | 331 | 88.8% |
| 0.90 | 87.3% | 169 | 100.0% |
| 0.95 | 91.6% | 111 | 100.0% |

Threshold for >=95% auto-lane accuracy: 0.90, human share 87.3%.

**laya**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 17.9% | 871 | 39.3% |
| 0.55 | 19.3% | 856 | 39.8% |
| 0.60 | 19.6% | 853 | 40.0% |
| 0.65 | 19.8% | 851 | 40.1% |
| 0.70 | 23.7% | 810 | 41.9% |
| 0.75 | 24.2% | 804 | 41.8% |
| 0.80 | 27.3% | 771 | 42.8% |
| 0.85 | 30.4% | 738 | 43.0% |
| 0.90 | 39.7% | 640 | 43.0% |
| 0.95 | 44.8% | 586 | 44.2% |

95% auto-lane accuracy not reachable.

**laya_calibrated**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 70.1% | 317 | 38.5% |
| 0.55 | 70.4% | 314 | 38.5% |
| 0.60 | 77.5% | 239 | 46.9% |
| 0.65 | 77.5% | 239 | 46.9% |
| 0.70 | 100.0% | 0 | n/a |
| 0.75 | 100.0% | 0 | n/a |
| 0.80 | 100.0% | 0 | n/a |
| 0.85 | 100.0% | 0 | n/a |
| 0.90 | 100.0% | 0 | n/a |
| 0.95 | 100.0% | 0 | n/a |

95% auto-lane accuracy not reachable.

**bedrock_qwen32b**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 0.0% | 1327 | 77.9% |
| 0.55 | 0.0% | 1327 | 77.9% |
| 0.60 | 0.0% | 1327 | 77.9% |
| 0.65 | 0.0% | 1327 | 77.9% |
| 0.70 | 0.0% | 1327 | 77.9% |
| 0.75 | 0.0% | 1327 | 77.9% |
| 0.80 | 0.0% | 1327 | 77.9% |
| 0.85 | 0.0% | 1327 | 77.9% |
| 0.90 | 0.2% | 1325 | 77.9% |
| 0.95 | 11.9% | 1169 | 81.6% |

Threshold for >=95% auto-lane accuracy: 0.99, human share 99.9%.

**bedrock_nova**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 0.0% | 300 | 82.0% |
| 0.55 | 0.0% | 300 | 82.0% |
| 0.60 | 0.0% | 300 | 82.0% |
| 0.65 | 0.0% | 300 | 82.0% |
| 0.70 | 0.0% | 300 | 82.0% |
| 0.75 | 0.0% | 300 | 82.0% |
| 0.80 | 0.0% | 300 | 82.0% |
| 0.85 | 0.0% | 300 | 82.0% |
| 0.90 | 0.0% | 300 | 82.0% |
| 0.95 | 92.3% | 23 | 95.7% |

Threshold for >=95% auto-lane accuracy: 0.91, human share 92.3%.

### Injection (is_injection, clean rows, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 original caught (n) | variants recall (n) | recall + guard | variants recall + guard | benign flagged or routed to human (+guard) |
|---|---|---|---|---|---|---|---|---|---|---|
| decider | 1327 | 1 | 1326 | 100.0% | 0.0% | True (n=1) | n/a (n=0) | 100.0% | n/a | 0.0% |
| laya | 1061 | 1 | 1060 | 0.0% | 17.6% | False (n=1) | n/a (n=0) | 0.0% | n/a | 17.6% |
| laya_calibrated | 1061 | 1 | 1060 | 0.0% | 17.6% | False (n=1) | n/a (n=0) | 0.0% | n/a | 17.6% |
| bedrock_qwen32b | 1327 | 1 | 1326 | 100.0% | 0.0% | True (n=1) | n/a (n=0) | 100.0% | n/a | 0.0% |
| bedrock_nova | 300 | 1 | 299 | 100.0% | 0.7% | True (n=1) | n/a (n=0) | 100.0% | n/a | 0.7% |

## Set: safety

Set size 120; ids common to all backends with preds: 120; missing preds: none.

### Accuracy per question (own ids / common ids)

| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |
|---|---|---|---|---|---|---|---|---|
| decider | 120 | 61.7% | 63.3% | 80.0% | 120 | 61.7% | 63.3% | 80.0% |
| laya | 120 | 45.8% | 81.7% | 61.7% | 120 | 45.8% | 81.7% | 61.7% |
| laya_calibrated | 120 | 45.8% | 81.7% | 61.7% | 120 | 45.8% | 81.7% | 61.7% |
| bedrock_qwen32b | 120 | 90.0% | 55.0% | 95.0% | 120 | 90.0% | 55.0% | 95.0% |
| bedrock_nova | 120 | 80.8% | 32.5% | 95.0% | 120 | 80.8% | 32.5% | 95.0% |

### Clean vs errored tickets, baselines

| backend | n_clean | n_error | keep-initial baseline (n) | acc on misrouted tickets (n) | eval acc / calib-picked threshold / eval auto acc / eval human share |
|---|---|---|---|---|---|
| decider | 120 | 0 | 100.0% (n=1) | n/a (n=0) | 100.0% (n=1) / none reaches 95% on calib (n=0) / n/a (n=None) / n/a |
| laya | 120 | 0 | 100.0% (n=1) | n/a (n=0) | 100.0% (n=1) / none reaches 95% on calib (n=0) / n/a (n=None) / n/a |
| laya_calibrated | 120 | 0 | 100.0% (n=1) | n/a (n=0) | 100.0% (n=1) / none reaches 95% on calib (n=0) / n/a (n=None) / n/a |
| bedrock_qwen32b | 120 | 0 | 100.0% (n=1) | n/a (n=0) | 100.0% (n=1) / none reaches 95% on calib (n=0) / n/a (n=None) / n/a |
| bedrock_nova | 120 | 0 | 100.0% (n=1) | n/a (n=0) | 100.0% (n=1) / none reaches 95% on calib (n=0) / n/a (n=None) / n/a |

injection_guard trigger rate on this set (its cost in human share): en 0/31 (0.0%), hi 10/30 (33.3%), ar 13/30 (43.3%), hinglish 0/29 (0.0%), all 23/120 (19.2%)


### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 120 | 636 | 716 |
| laya | 120 | 81 | 112 |
| laya_calibrated | 120 | 81 | 112 |
| bedrock_qwen32b | 120 | 3989 | 4243 |
| bedrock_nova | 120 | 17957 | 18665 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 0 | n/a | 120 | 61.7% | n/a | n/a |
| laya | 0 | n/a | 120 | 45.8% | n/a | n/a |
| laya_calibrated | 0 | n/a | 120 | 45.8% | n/a | n/a |
| bedrock_qwen32b | 0 | n/a | 120 | 90.0% | n/a | n/a |
| bedrock_nova | 0 | n/a | 120 | 80.8% | n/a | n/a |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 61.3% (n=31) | 73.3% (n=30) | 50.0% (n=30) | 62.1% (n=29) |
| laya | 54.8% (n=31) | 40.0% (n=30) | 46.7% (n=30) | 41.4% (n=29) |
| laya_calibrated | 54.8% (n=31) | 40.0% (n=30) | 46.7% (n=30) | 41.4% (n=29) |
| bedrock_qwen32b | 90.3% (n=31) | 90.0% (n=30) | 90.0% (n=30) | 89.7% (n=29) |
| bedrock_nova | 90.3% (n=31) | 83.3% (n=30) | 70.0% (n=30) | 79.3% (n=29) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 6 | 50.0% | 25 | 64.0% | 14.0% |
| decider | hi | 8 | 100.0% | 22 | 63.6% | -36.4% |
| decider | ar | 9 | 33.3% | 21 | 57.1% | 23.8% |
| decider | hinglish | 0 | n/a | 29 | 62.1% | n/a |
| laya | en | 6 | 50.0% | 25 | 56.0% | 6.0% |
| laya | hi | 8 | 50.0% | 22 | 36.4% | -13.6% |
| laya | ar | 9 | 33.3% | 21 | 52.4% | 19.0% |
| laya | hinglish | 0 | n/a | 29 | 41.4% | n/a |
| laya_calibrated | en | 6 | 50.0% | 25 | 56.0% | 6.0% |
| laya_calibrated | hi | 8 | 50.0% | 22 | 36.4% | -13.6% |
| laya_calibrated | ar | 9 | 33.3% | 21 | 52.4% | 19.0% |
| laya_calibrated | hinglish | 0 | n/a | 29 | 41.4% | n/a |
| bedrock_qwen32b | en | 6 | 66.7% | 25 | 96.0% | 29.3% |
| bedrock_qwen32b | hi | 8 | 100.0% | 22 | 86.4% | -13.6% |
| bedrock_qwen32b | ar | 9 | 66.7% | 21 | 100.0% | 33.3% |
| bedrock_qwen32b | hinglish | 0 | n/a | 29 | 89.7% | n/a |
| bedrock_nova | en | 6 | 100.0% | 25 | 88.0% | -12.0% |
| bedrock_nova | hi | 8 | 62.5% | 22 | 90.9% | 28.4% |
| bedrock_nova | ar | 9 | 44.4% | 21 | 81.0% | 36.5% |
| bedrock_nova | hinglish | 0 | n/a | 29 | 79.3% | n/a |

### Calibration (category, own ids)

**decider**: ECE (10 bins) = 0.197

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 15 | 0.230 | 0.067 |
| 0.3-0.4 | 7 | 0.350 | 0.429 |
| 0.4-0.5 | 9 | 0.446 | 0.778 |
| 0.5-0.6 | 7 | 0.573 | 0.143 |
| 0.6-0.7 | 9 | 0.621 | 0.333 |
| 0.7-0.8 | 4 | 0.778 | 0.000 |
| 0.8-0.9 | 24 | 0.845 | 0.583 |
| 0.9-1.0 | 45 | 0.942 | 1.000 |

**laya**: ECE (10 bins) = 0.427

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 3 | 0.249 | 0.333 |
| 0.3-0.4 | 3 | 0.344 | 0.333 |
| 0.4-0.5 | 2 | 0.454 | 0.500 |
| 0.5-0.6 | 1 | 0.526 | 0.000 |
| 0.6-0.7 | 9 | 0.654 | 0.556 |
| 0.7-0.8 | 10 | 0.765 | 0.400 |
| 0.8-0.9 | 13 | 0.842 | 0.538 |
| 0.9-1.0 | 79 | 0.985 | 0.456 |

**laya_calibrated**: ECE (10 bins) = 0.257

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 60 | 0.163 | 0.483 |
| 0.2-0.3 | 22 | 0.233 | 0.409 |
| 0.3-0.4 | 4 | 0.326 | 0.500 |
| 0.4-0.5 | 4 | 0.443 | 0.000 |
| 0.5-0.6 | 3 | 0.559 | 0.000 |
| 0.6-0.7 | 27 | 0.692 | 0.556 |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 0 | n/a | n/a |
| 0.9-1.0 | 0 | n/a | n/a |

**bedrock_qwen32b**: ECE (10 bins) = 0.085

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 0 | n/a | n/a |
| 0.6-0.7 | 0 | n/a | n/a |
| 0.7-0.8 | 1 | 0.750 | 0.000 |
| 0.8-0.9 | 48 | 0.850 | 0.771 |
| 0.9-1.0 | 71 | 0.920 | 1.000 |

**bedrock_nova**: ECE (10 bins) = 0.084

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 0 | n/a | n/a |
| 0.6-0.7 | 0 | n/a | n/a |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 13 | 0.800 | 0.385 |
| 0.9-1.0 | 107 | 0.903 | 0.860 |

### Low-confidence-to-human sweep (category, own ids)

**decider**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 25.8% | 89 | 70.8% |
| 0.55 | 26.7% | 88 | 71.6% |
| 0.60 | 31.7% | 82 | 75.6% |
| 0.65 | 38.3% | 74 | 79.7% |
| 0.70 | 39.2% | 73 | 80.8% |
| 0.75 | 40.0% | 72 | 81.9% |
| 0.80 | 42.5% | 69 | 85.5% |
| 0.85 | 54.2% | 55 | 94.5% |
| 0.90 | 62.5% | 45 | 100.0% |
| 0.95 | 84.2% | 19 | 100.0% |

Threshold for >=95% auto-lane accuracy: 0.90, human share 62.5%.

**laya**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 6.7% | 112 | 46.4% |
| 0.55 | 7.5% | 111 | 46.8% |
| 0.60 | 7.5% | 111 | 46.8% |
| 0.65 | 10.8% | 107 | 45.8% |
| 0.70 | 15.0% | 102 | 46.1% |
| 0.75 | 18.3% | 98 | 45.9% |
| 0.80 | 23.3% | 92 | 46.7% |
| 0.85 | 30.0% | 84 | 45.2% |
| 0.90 | 34.2% | 79 | 45.6% |
| 0.95 | 41.7% | 70 | 44.3% |

95% auto-lane accuracy not reachable.

**laya_calibrated**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 75.0% | 30 | 50.0% |
| 0.55 | 75.0% | 30 | 50.0% |
| 0.60 | 77.5% | 27 | 55.6% |
| 0.65 | 77.5% | 27 | 55.6% |
| 0.70 | 100.0% | 0 | n/a |
| 0.75 | 100.0% | 0 | n/a |
| 0.80 | 100.0% | 0 | n/a |
| 0.85 | 100.0% | 0 | n/a |
| 0.90 | 100.0% | 0 | n/a |
| 0.95 | 100.0% | 0 | n/a |

95% auto-lane accuracy not reachable.

**bedrock_qwen32b**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 0.0% | 120 | 90.0% |
| 0.55 | 0.0% | 120 | 90.0% |
| 0.60 | 0.0% | 120 | 90.0% |
| 0.65 | 0.0% | 120 | 90.0% |
| 0.70 | 0.0% | 120 | 90.0% |
| 0.75 | 0.0% | 120 | 90.0% |
| 0.80 | 0.8% | 119 | 90.8% |
| 0.85 | 0.8% | 119 | 90.8% |
| 0.90 | 40.8% | 71 | 100.0% |
| 0.95 | 75.8% | 29 | 100.0% |

Threshold for >=95% auto-lane accuracy: 0.86, human share 40.8%.

**bedrock_nova**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 0.0% | 120 | 80.8% |
| 0.55 | 0.0% | 120 | 80.8% |
| 0.60 | 0.0% | 120 | 80.8% |
| 0.65 | 0.0% | 120 | 80.8% |
| 0.70 | 0.0% | 120 | 80.8% |
| 0.75 | 0.0% | 120 | 80.8% |
| 0.80 | 0.0% | 120 | 80.8% |
| 0.85 | 10.8% | 107 | 86.0% |
| 0.90 | 10.8% | 107 | 86.0% |
| 0.95 | 94.2% | 7 | 100.0% |

Threshold for >=95% auto-lane accuracy: 0.91, human share 94.2%.

### Injection (is_injection, clean rows, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 original caught (n) | variants recall (n) | recall + guard | variants recall + guard | benign flagged or routed to human (+guard) |
|---|---|---|---|---|---|---|---|---|---|---|
| decider | 120 | 60 | 60 | 60.0% | 0.0% | True (n=1) | 59.3% (n=59) | 73.3% | 72.9% | 5.0% |
| laya | 120 | 60 | 60 | 35.0% | 11.7% | False (n=1) | 35.6% (n=59) | 55.0% | 55.9% | 16.7% |
| laya_calibrated | 120 | 60 | 60 | 35.0% | 11.7% | False (n=1) | 35.6% (n=59) | 55.0% | 55.9% | 16.7% |
| bedrock_qwen32b | 120 | 60 | 60 | 90.0% | 0.0% | True (n=1) | 89.8% (n=59) | 95.0% | 94.9% | 5.0% |
| bedrock_nova | 120 | 60 | 60 | 90.0% | 0.0% | True (n=1) | 89.8% (n=59) | 96.7% | 96.6% | 5.0% |

## Set: stale

Set size 48; ids common to all backends with preds: 48; missing preds: ['laya', 'laya_calibrated', 'bedrock_qwen32b', 'bedrock_nova'].

### Accuracy per question (own ids / common ids)

| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |
|---|---|---|---|---|---|---|---|---|
| decider | 48 | 70.8% | 39.6% | 100.0% | 48 | 70.8% | 39.6% | 100.0% |

### Clean vs errored tickets, baselines

| backend | n_clean | n_error | keep-initial baseline (n) | acc on misrouted tickets (n) | eval acc / calib-picked threshold / eval auto acc / eval human share |
|---|---|---|---|---|---|
| decider | 48 | 0 | 68.8% (n=48) | 91.7% (n=12) | 70.0% (n=40) / 0.6 / 60.0% (n=15) / 62.5% |

injection_guard trigger rate on this set (its cost in human share): en 0/29 (0.0%), hi 0/3 (0.0%), ar 0/8 (0.0%), hinglish 0/8 (0.0%), all 0/48 (0.0%)


### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 48 | 636 | 696 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 40 | 72.5% | 8 | 62.5% | -10.0% | 27.5% |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 75.9% (n=29) | 33.3% (n=3) | 100.0% (n=8) | 37.5% (n=8) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 5 | 80.0% | 24 | 75.0% | -5.0% |
| decider | hi | 3 | 33.3% | 0 | n/a | n/a |
| decider | ar | 8 | 100.0% | 0 | n/a | n/a |
| decider | hinglish | 0 | n/a | 8 | 37.5% | n/a |

### Calibration (category, own ids)

**decider**: ECE (10 bins) = 0.240

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 10 | 0.342 | 0.900 |
| 0.4-0.5 | 3 | 0.452 | 1.000 |
| 0.5-0.6 | 16 | 0.559 | 0.562 |
| 0.6-0.7 | 2 | 0.652 | 0.000 |
| 0.7-0.8 | 4 | 0.740 | 1.000 |
| 0.8-0.9 | 13 | 0.838 | 0.692 |
| 0.9-1.0 | 0 | n/a | n/a |

### Low-confidence-to-human sweep (category, own ids)

**decider**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 27.1% | 35 | 62.9% |
| 0.55 | 37.5% | 30 | 70.0% |
| 0.60 | 60.4% | 19 | 68.4% |
| 0.65 | 62.5% | 18 | 72.2% |
| 0.70 | 64.6% | 17 | 76.5% |
| 0.75 | 72.9% | 13 | 69.2% |
| 0.80 | 72.9% | 13 | 69.2% |
| 0.85 | 89.6% | 5 | 100.0% |
| 0.90 | 100.0% | 0 | n/a |
| 0.95 | 100.0% | 0 | n/a |

Threshold for >=95% auto-lane accuracy: 0.82, human share 83.3%.

### Injection (is_injection, clean rows, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 original caught (n) | variants recall (n) | recall + guard | variants recall + guard | benign flagged or routed to human (+guard) |
|---|---|---|---|---|---|---|---|---|---|---|
| decider | 48 | 0 | 48 | n/a | 0.0% | None (n=0) | n/a (n=0) | n/a | n/a | 0.0% |

## Set: control

Set size 29; ids common to all backends with preds: 29; missing preds: ['laya', 'laya_calibrated', 'bedrock_qwen32b', 'bedrock_nova'].

### Accuracy per question (own ids / common ids)

| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |
|---|---|---|---|---|---|---|---|---|
| decider | 29 | 82.8% | 51.7% | 100.0% | 29 | 82.8% | 51.7% | 100.0% |

### Clean vs errored tickets, baselines

| backend | n_clean | n_error | keep-initial baseline (n) | acc on misrouted tickets (n) | eval acc / calib-picked threshold / eval auto acc / eval human share |
|---|---|---|---|---|---|
| decider | 29 | 0 | 79.3% (n=29) | 80.0% (n=5) | 80.0% (n=25) / 0.0 / 80.0% (n=25) / 0.0% |

injection_guard trigger rate on this set (its cost in human share): en 0/21 (0.0%), hi 0/1 (0.0%), ar 0/0 (n/a), hinglish 0/7 (0.0%), all 0/29 (0.0%)


### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 29 | 636 | 693 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 25 | 88.0% | 4 | 50.0% | -38.0% | 12.0% |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 81.0% (n=21) | 0.0% (n=1) | n/a (n=0) | 100.0% (n=7) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 0 | n/a | 21 | 81.0% | n/a |
| decider | hi | 1 | 0.0% | 0 | n/a | n/a |
| decider | ar | 0 | n/a | 0 | n/a | n/a |
| decider | hinglish | 0 | n/a | 7 | 100.0% | n/a |

### Calibration (category, own ids)

**decider**: ECE (10 bins) = 0.283

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 4 | 0.496 | 1.000 |
| 0.5-0.6 | 5 | 0.563 | 0.000 |
| 0.6-0.7 | 0 | n/a | n/a |
| 0.7-0.8 | 5 | 0.739 | 1.000 |
| 0.8-0.9 | 15 | 0.861 | 1.000 |
| 0.9-1.0 | 0 | n/a | n/a |

### Low-confidence-to-human sweep (category, own ids)

**decider**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 13.8% | 25 | 80.0% |
| 0.55 | 17.2% | 24 | 83.3% |
| 0.60 | 31.0% | 20 | 100.0% |
| 0.65 | 31.0% | 20 | 100.0% |
| 0.70 | 31.0% | 20 | 100.0% |
| 0.75 | 48.3% | 15 | 100.0% |
| 0.80 | 48.3% | 15 | 100.0% |
| 0.85 | 58.6% | 12 | 100.0% |
| 0.90 | 100.0% | 0 | n/a |
| 0.95 | 100.0% | 0 | n/a |

Threshold for >=95% auto-lane accuracy: 0.58, human share 27.6%.

### Injection (is_injection, clean rows, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 original caught (n) | variants recall (n) | recall + guard | variants recall + guard | benign flagged or routed to human (+guard) |
|---|---|---|---|---|---|---|---|---|---|---|
| decider | 29 | 0 | 29 | n/a | 0.0% | None (n=0) | n/a (n=0) | n/a | n/a | 0.0% |

## Set: incident

Set size 78; ids common to all backends with preds: 78; missing preds: ['laya', 'laya_calibrated', 'bedrock_qwen32b', 'bedrock_nova'].

### Accuracy per question (own ids / common ids)

| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |
|---|---|---|---|---|---|---|---|---|
| decider | 78 | 80.8% | 76.9% | 100.0% | 78 | 80.8% | 76.9% | 100.0% |

### Clean vs errored tickets, baselines

| backend | n_clean | n_error | keep-initial baseline (n) | acc on misrouted tickets (n) | eval acc / calib-picked threshold / eval auto acc / eval human share |
|---|---|---|---|---|---|
| decider | 78 | 0 | 100.0% (n=78) | n/a (n=0) | 79.7% (n=64) / 0.62 / 91.1% (n=56) / 12.5% |

injection_guard trigger rate on this set (its cost in human share): en 0/74 (0.0%), hi 0/0 (n/a), ar 0/0 (n/a), hinglish 0/4 (0.0%), all 0/78 (0.0%)


### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 78 | 632 | 635 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 47 | 85.1% | 31 | 74.2% | -10.9% | 14.9% |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 79.7% (n=74) | n/a (n=0) | n/a (n=0) | 100.0% (n=4) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 13 | 100.0% | 61 | 75.4% | -24.6% |
| decider | hi | 0 | n/a | 0 | n/a | n/a |
| decider | ar | 0 | n/a | 0 | n/a | n/a |
| decider | hinglish | 0 | n/a | 4 | 100.0% | n/a |

### Calibration (category, own ids)

**decider**: ECE (10 bins) = 0.105

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 0 | n/a | n/a |
| 0.6-0.7 | 32 | 0.640 | 0.531 |
| 0.7-0.8 | 16 | 0.773 | 1.000 |
| 0.8-0.9 | 0 | n/a | n/a |
| 0.9-1.0 | 30 | 0.964 | 1.000 |

### Low-confidence-to-human sweep (category, own ids)

**decider**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 0.0% | 78 | 80.8% |
| 0.55 | 0.0% | 78 | 80.8% |
| 0.60 | 0.0% | 78 | 80.8% |
| 0.65 | 19.2% | 63 | 100.0% |
| 0.70 | 41.0% | 46 | 100.0% |
| 0.75 | 41.0% | 46 | 100.0% |
| 0.80 | 61.5% | 30 | 100.0% |
| 0.85 | 61.5% | 30 | 100.0% |
| 0.90 | 61.5% | 30 | 100.0% |
| 0.95 | 61.5% | 30 | 100.0% |

Threshold for >=95% auto-lane accuracy: 0.63, human share 16.7%.

### Injection (is_injection, clean rows, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 original caught (n) | variants recall (n) | recall + guard | variants recall + guard | benign flagged or routed to human (+guard) |
|---|---|---|---|---|---|---|---|---|---|---|
| decider | 78 | 0 | 78 | n/a | 0.0% | None (n=0) | n/a (n=0) | n/a | n/a | 0.0% |

## Set: smoke

Set size 14; ids common to all backends with preds: 14; missing preds: ['bedrock_nova'].

### Accuracy per question (own ids / common ids)

| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |
|---|---|---|---|---|---|---|---|---|
| decider | 14 | 71.4% | 35.7% | 100.0% | 14 | 71.4% | 35.7% | 100.0% |
| laya | 14 | 42.9% | 35.7% | 85.7% | 14 | 42.9% | 35.7% | 85.7% |
| laya_calibrated | 14 | 42.9% | 35.7% | 85.7% | 14 | 42.9% | 35.7% | 85.7% |
| bedrock_qwen32b | 14 | 85.7% | 35.7% | 100.0% | 14 | 85.7% | 35.7% | 100.0% |

### Clean vs errored tickets, baselines

| backend | n_clean | n_error | keep-initial baseline (n) | acc on misrouted tickets (n) | eval acc / calib-picked threshold / eval auto acc / eval human share |
|---|---|---|---|---|---|
| decider | 14 | 0 | 64.3% (n=14) | 75.0% (n=4) | 72.7% (n=11) / none reaches 95% on calib (n=2) / n/a (n=None) / n/a |
| laya | 14 | 0 | 64.3% (n=14) | 75.0% (n=4) | 45.5% (n=11) / none reaches 95% on calib (n=2) / n/a (n=None) / n/a |
| laya_calibrated | 14 | 0 | 64.3% (n=14) | 75.0% (n=4) | 45.5% (n=11) / none reaches 95% on calib (n=2) / n/a (n=None) / n/a |
| bedrock_qwen32b | 14 | 0 | 64.3% (n=14) | 100.0% (n=4) | 90.9% (n=11) / none reaches 95% on calib (n=2) / n/a (n=None) / n/a |

injection_guard trigger rate on this set (its cost in human share): en 0/9 (0.0%), hi 0/1 (0.0%), ar 0/4 (0.0%), hinglish 0/0 (n/a), all 0/14 (0.0%)


### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 14 | 634 | 693 |
| laya | 14 | 87 | 96 |
| laya_calibrated | 14 | 87 | 96 |
| bedrock_qwen32b | 14 | 3763 | 4059 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 13 | 69.2% | 1 | 100.0% | 30.8% | 30.8% |
| laya | 13 | 38.5% | 1 | 100.0% | 61.5% | 61.5% |
| laya_calibrated | 13 | 38.5% | 1 | 100.0% | 61.5% | 61.5% |
| bedrock_qwen32b | 13 | 84.6% | 1 | 100.0% | 15.4% | 15.4% |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 88.9% (n=9) | 0.0% (n=1) | 50.0% (n=4) | n/a (n=0) |
| laya | 33.3% (n=9) | 0.0% (n=1) | 75.0% (n=4) | n/a (n=0) |
| laya_calibrated | 33.3% (n=9) | 0.0% (n=1) | 75.0% (n=4) | n/a (n=0) |
| bedrock_qwen32b | 100.0% (n=9) | 0.0% (n=1) | 75.0% (n=4) | n/a (n=0) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 1 | 100.0% | 8 | 87.5% | -12.5% |
| decider | hi | 1 | 0.0% | 0 | n/a | n/a |
| decider | ar | 4 | 50.0% | 0 | n/a | n/a |
| decider | hinglish | 0 | n/a | 0 | n/a | n/a |
| laya | en | 1 | 100.0% | 8 | 25.0% | -75.0% |
| laya | hi | 1 | 0.0% | 0 | n/a | n/a |
| laya | ar | 4 | 75.0% | 0 | n/a | n/a |
| laya | hinglish | 0 | n/a | 0 | n/a | n/a |
| laya_calibrated | en | 1 | 100.0% | 8 | 25.0% | -75.0% |
| laya_calibrated | hi | 1 | 0.0% | 0 | n/a | n/a |
| laya_calibrated | ar | 4 | 75.0% | 0 | n/a | n/a |
| laya_calibrated | hinglish | 0 | n/a | 0 | n/a | n/a |
| bedrock_qwen32b | en | 1 | 100.0% | 8 | 100.0% | 0.0% |
| bedrock_qwen32b | hi | 1 | 0.0% | 0 | n/a | n/a |
| bedrock_qwen32b | ar | 4 | 75.0% | 0 | n/a | n/a |
| bedrock_qwen32b | hinglish | 0 | n/a | 0 | n/a | n/a |

### Calibration (category, own ids)

**decider**: ECE (10 bins) = 0.254

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 2 | 0.345 | 1.000 |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 5 | 0.571 | 0.400 |
| 0.6-0.7 | 0 | n/a | n/a |
| 0.7-0.8 | 1 | 0.763 | 0.000 |
| 0.8-0.9 | 4 | 0.854 | 1.000 |
| 0.9-1.0 | 2 | 0.975 | 1.000 |

**laya**: ECE (10 bins) = 0.463

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 2 | 0.136 | 1.000 |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 1 | 0.304 | 0.000 |
| 0.4-0.5 | 3 | 0.468 | 0.333 |
| 0.5-0.6 | 1 | 0.539 | 0.000 |
| 0.6-0.7 | 1 | 0.647 | 0.000 |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 1 | 0.868 | 0.000 |
| 0.9-1.0 | 5 | 0.998 | 0.600 |

**laya_calibrated**: ECE (10 bins) = 0.175

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 9 | 0.141 | 0.333 |
| 0.2-0.3 | 1 | 0.210 | 0.000 |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 1 | 0.565 | 1.000 |
| 0.6-0.7 | 3 | 0.692 | 0.667 |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 0 | n/a | n/a |
| 0.9-1.0 | 0 | n/a | n/a |

**bedrock_qwen32b**: ECE (10 bins) = 0.101

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 0 | n/a | n/a |
| 0.6-0.7 | 0 | n/a | n/a |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 1 | 0.850 | 1.000 |
| 0.9-1.0 | 13 | 0.943 | 0.846 |

### Low-confidence-to-human sweep (category, own ids)

**decider**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 14.3% | 12 | 66.7% |
| 0.55 | 21.4% | 11 | 72.7% |
| 0.60 | 50.0% | 7 | 85.7% |
| 0.65 | 50.0% | 7 | 85.7% |
| 0.70 | 50.0% | 7 | 85.7% |
| 0.75 | 50.0% | 7 | 85.7% |
| 0.80 | 57.1% | 6 | 100.0% |
| 0.85 | 64.3% | 5 | 100.0% |
| 0.90 | 85.7% | 2 | 100.0% |
| 0.95 | 85.7% | 2 | 100.0% |

Threshold for >=95% auto-lane accuracy: 0.77, human share 57.1%.

**laya**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 42.9% | 8 | 37.5% |
| 0.55 | 50.0% | 7 | 42.9% |
| 0.60 | 50.0% | 7 | 42.9% |
| 0.65 | 57.1% | 6 | 50.0% |
| 0.70 | 57.1% | 6 | 50.0% |
| 0.75 | 57.1% | 6 | 50.0% |
| 0.80 | 57.1% | 6 | 50.0% |
| 0.85 | 57.1% | 6 | 50.0% |
| 0.90 | 64.3% | 5 | 60.0% |
| 0.95 | 64.3% | 5 | 60.0% |

Threshold for >=95% auto-lane accuracy: 1.00, human share 85.7%.

**laya_calibrated**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 71.4% | 4 | 75.0% |
| 0.55 | 71.4% | 4 | 75.0% |
| 0.60 | 78.6% | 3 | 66.7% |
| 0.65 | 78.6% | 3 | 66.7% |
| 0.70 | 100.0% | 0 | n/a |
| 0.75 | 100.0% | 0 | n/a |
| 0.80 | 100.0% | 0 | n/a |
| 0.85 | 100.0% | 0 | n/a |
| 0.90 | 100.0% | 0 | n/a |
| 0.95 | 100.0% | 0 | n/a |

95% auto-lane accuracy not reachable.

**bedrock_qwen32b**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 0.0% | 14 | 85.7% |
| 0.55 | 0.0% | 14 | 85.7% |
| 0.60 | 0.0% | 14 | 85.7% |
| 0.65 | 0.0% | 14 | 85.7% |
| 0.70 | 0.0% | 14 | 85.7% |
| 0.75 | 0.0% | 14 | 85.7% |
| 0.80 | 0.0% | 14 | 85.7% |
| 0.85 | 0.0% | 14 | 85.7% |
| 0.90 | 7.1% | 13 | 84.6% |
| 0.95 | 28.6% | 10 | 90.0% |

Threshold for >=95% auto-lane accuracy: 0.96, human share 85.7%.

### Injection (is_injection, clean rows, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 original caught (n) | variants recall (n) | recall + guard | variants recall + guard | benign flagged or routed to human (+guard) |
|---|---|---|---|---|---|---|---|---|---|---|
| decider | 14 | 1 | 13 | 100.0% | 0.0% | True (n=1) | n/a (n=0) | 100.0% | n/a | 0.0% |
| laya | 14 | 1 | 13 | 0.0% | 7.7% | False (n=1) | n/a (n=0) | 0.0% | n/a | 7.7% |
| laya_calibrated | 14 | 1 | 13 | 0.0% | 7.7% | False (n=1) | n/a (n=0) | 0.0% | n/a | 7.7% |
| bedrock_qwen32b | 14 | 1 | 13 | 100.0% | 0.0% | True (n=1) | n/a (n=0) | 100.0% | n/a | 0.0% |
