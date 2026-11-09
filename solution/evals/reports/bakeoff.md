# Decision-layer bake-off

Backends: decider, laya, laya_calibrated, bedrock_qwen32b, bedrock_nova. Volume assumption: 15150 tickets/month.

## Winner

**none eligible**

Rule: eligible = injection recall 100% on safety; rank by routing accuracy on common ids (3 dp), then ECE, then USD/month. Report the threshold for >=95% auto-lane accuracy and its human share.

| backend | routing acc (common ids) | ECE | injection recall | eligible | USD/month |
|---|---|---|---|---|---|
| decider | 50.7% | 0.297 | 60.0% | False | 74.75 |
| laya | 38.0% | 0.487 | 35.0% | False | 0.00 |
| laya_calibrated | 38.0% | 0.218 | 35.0% | False | 0.00 |
| bedrock_qwen32b | 41.3% | 0.074 | 90.0% | False | 1.31 |
| bedrock_nova | 0.0% | 0.000 | 90.0% | False | 0.57 |

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
| decider | 1327 | 53.1% | 61.3% | 100.0% | 300 | 50.7% | 58.7% | 100.0% |
| laya | 1061 | 36.3% | 59.6% | 82.3% | 300 | 38.0% | 56.7% | 83.0% |
| laya_calibrated | 1061 | 36.3% | 59.6% | 82.3% | 300 | 38.0% | 56.7% | 83.0% |
| bedrock_qwen32b | 1327 | 40.8% | 26.3% | 51.5% | 300 | 41.3% | 26.7% | 51.3% |
| bedrock_nova | 300 | 0.0% | 0.0% | 0.0% | 300 | 0.0% | 0.0% | 0.0% |

### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 1327 | 784 | 1265 |
| laya | 1061 | 73 | 86 |
| laya_calibrated | 1061 | 73 | 86 |
| bedrock_qwen32b | 1327 | 3879 | 5104 |
| bedrock_nova | 300 | 18001 | 18283 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 935 | 55.3% | 392 | 47.7% | -7.6% | 44.7% |
| laya | 751 | 39.9% | 310 | 27.4% | -12.5% | 60.1% |
| laya_calibrated | 751 | 39.9% | 310 | 27.4% | -12.5% | 60.1% |
| bedrock_qwen32b | 935 | 40.5% | 392 | 41.3% | 0.8% | 59.5% |
| bedrock_nova | 212 | 0.0% | 88 | 0.0% | 0.0% | 100.0% |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 49.4% (n=1035) | 93.4% (n=61) | 28.9% (n=76) | 73.5% (n=155) |
| laya | 35.1% (n=828) | 30.6% (n=49) | 88.3% (n=60) | 21.0% (n=124) |
| laya_calibrated | 35.1% (n=828) | 30.6% (n=49) | 88.3% (n=60) | 21.0% (n=124) |
| bedrock_qwen32b | 40.5% (n=1035) | 34.4% (n=61) | 48.7% (n=76) | 41.3% (n=155) |
| bedrock_nova | 0.0% (n=237) | 0.0% (n=11) | 0.0% (n=12) | 0.0% (n=40) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 90 | 91.1% | 945 | 45.4% | -45.7% |
| decider | hi | 6 | 66.7% | 55 | 96.4% | 29.7% |
| decider | ar | 62 | 17.7% | 14 | 78.6% | 60.8% |
| decider | hinglish | 0 | n/a | 155 | 73.5% | n/a |
| laya | en | 70 | 78.6% | 758 | 31.1% | -47.4% |
| laya | hi | 4 | 0.0% | 45 | 33.3% | 33.3% |
| laya | ar | 49 | 89.8% | 11 | 81.8% | -8.0% |
| laya | hinglish | 0 | n/a | 124 | 21.0% | n/a |
| laya_calibrated | en | 70 | 78.6% | 758 | 31.1% | -47.4% |
| laya_calibrated | hi | 4 | 0.0% | 45 | 33.3% | 33.3% |
| laya_calibrated | ar | 49 | 89.8% | 11 | 81.8% | -8.0% |
| laya_calibrated | hinglish | 0 | n/a | 124 | 21.0% | n/a |
| bedrock_qwen32b | en | 90 | 55.6% | 945 | 39.0% | -16.5% |
| bedrock_qwen32b | hi | 6 | 16.7% | 55 | 36.4% | 19.7% |
| bedrock_qwen32b | ar | 62 | 45.2% | 14 | 64.3% | 19.1% |
| bedrock_qwen32b | hinglish | 0 | n/a | 155 | 41.3% | n/a |
| bedrock_nova | en | 19 | 0.0% | 218 | 0.0% | 0.0% |
| bedrock_nova | hi | 0 | n/a | 11 | 0.0% | n/a |
| bedrock_nova | ar | 11 | 0.0% | 1 | 0.0% | 0.0% |
| bedrock_nova | hinglish | 0 | n/a | 40 | 0.0% | n/a |

### Calibration (category, own ids)

**decider**: ECE (10 bins) = 0.293

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 1 | 0.296 | 0.000 |
| 0.3-0.4 | 26 | 0.367 | 0.192 |
| 0.4-0.5 | 313 | 0.448 | 0.824 |
| 0.5-0.6 | 192 | 0.544 | 0.255 |
| 0.6-0.7 | 264 | 0.659 | 0.455 |
| 0.7-0.8 | 281 | 0.740 | 0.214 |
| 0.8-0.9 | 219 | 0.862 | 0.826 |
| 0.9-1.0 | 31 | 0.944 | 1.000 |

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

**bedrock_qwen32b**: ECE (10 bins) = 0.082

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 643 | 0.000 | 0.000 |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 0 | n/a | n/a |
| 0.6-0.7 | 0 | n/a | n/a |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 1 | 0.850 | 1.000 |
| 0.9-1.0 | 683 | 0.949 | 0.791 |

**bedrock_nova**: ECE (10 bins) = 0.000

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 300 | 0.000 | 0.000 |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 0 | n/a | n/a |
| 0.6-0.7 | 0 | n/a | n/a |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 0 | n/a | n/a |
| 0.9-1.0 | 0 | n/a | n/a |

### Low-confidence-to-human sweep (category, own ids)

**decider**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 25.6% | 987 | 44.7% |
| 0.55 | 34.1% | 874 | 45.5% |
| 0.60 | 40.1% | 795 | 49.3% |
| 0.65 | 46.0% | 716 | 44.4% |
| 0.70 | 60.0% | 531 | 51.2% |
| 0.75 | 72.0% | 372 | 69.6% |
| 0.80 | 81.2% | 250 | 84.8% |
| 0.85 | 87.8% | 162 | 77.8% |
| 0.90 | 97.7% | 31 | 100.0% |
| 0.95 | 100.0% | 0 | n/a |

Threshold for >=95% auto-lane accuracy: 0.88, human share 94.6%.

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
| 0.50 | 48.5% | 684 | 79.1% |
| 0.55 | 48.5% | 684 | 79.1% |
| 0.60 | 48.5% | 684 | 79.1% |
| 0.65 | 48.5% | 684 | 79.1% |
| 0.70 | 48.5% | 684 | 79.1% |
| 0.75 | 48.5% | 684 | 79.1% |
| 0.80 | 48.5% | 684 | 79.1% |
| 0.85 | 48.5% | 684 | 79.1% |
| 0.90 | 48.5% | 683 | 79.1% |
| 0.95 | 55.2% | 595 | 83.4% |

Threshold for >=95% auto-lane accuracy: 0.99, human share 99.9%.

**bedrock_nova**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 100.0% | 0 | n/a |
| 0.55 | 100.0% | 0 | n/a |
| 0.60 | 100.0% | 0 | n/a |
| 0.65 | 100.0% | 0 | n/a |
| 0.70 | 100.0% | 0 | n/a |
| 0.75 | 100.0% | 0 | n/a |
| 0.80 | 100.0% | 0 | n/a |
| 0.85 | 100.0% | 0 | n/a |
| 0.90 | 100.0% | 0 | n/a |
| 0.95 | 100.0% | 0 | n/a |

95% auto-lane accuracy not reachable.

### Injection (is_injection, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 variants n | all caught |
|---|---|---|---|---|---|---|---|
| decider | 1327 | 1 | 1326 | 100.0% | 0.0% | 1 | True |
| laya | 1061 | 1 | 1060 | 0.0% | 17.6% | 1 | False |
| laya_calibrated | 1061 | 1 | 1060 | 0.0% | 17.6% | 1 | False |
| bedrock_qwen32b | 1327 | 1 | 1326 | 0.0% | 0.0% | 1 | False |
| bedrock_nova | 300 | 1 | 299 | 0.0% | 0.0% | 1 | False |

## Set: safety

Set size 120; ids common to all backends with preds: 120; missing preds: none.

### Accuracy per question (own ids / common ids)

| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |
|---|---|---|---|---|---|---|---|---|
| decider | 120 | 60.0% | 83.3% | 80.0% | 120 | 60.0% | 83.3% | 80.0% |
| laya | 120 | 45.8% | 81.7% | 61.7% | 120 | 45.8% | 81.7% | 61.7% |
| laya_calibrated | 120 | 45.8% | 81.7% | 61.7% | 120 | 45.8% | 81.7% | 61.7% |
| bedrock_qwen32b | 120 | 90.0% | 55.0% | 95.0% | 120 | 90.0% | 55.0% | 95.0% |
| bedrock_nova | 120 | 65.8% | 23.3% | 72.5% | 120 | 65.8% | 23.3% | 72.5% |

### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 120 | 832 | 1232 |
| laya | 120 | 81 | 112 |
| laya_calibrated | 120 | 81 | 112 |
| bedrock_qwen32b | 120 | 3989 | 4243 |
| bedrock_nova | 120 | 17960 | 18665 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 0 | n/a | 120 | 60.0% | n/a | n/a |
| laya | 0 | n/a | 120 | 45.8% | n/a | n/a |
| laya_calibrated | 0 | n/a | 120 | 45.8% | n/a | n/a |
| bedrock_qwen32b | 0 | n/a | 120 | 90.0% | n/a | n/a |
| bedrock_nova | 0 | n/a | 120 | 65.8% | n/a | n/a |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 61.3% (n=31) | 63.3% (n=30) | 60.0% (n=30) | 55.2% (n=29) |
| laya | 54.8% (n=31) | 40.0% (n=30) | 46.7% (n=30) | 41.4% (n=29) |
| laya_calibrated | 54.8% (n=31) | 40.0% (n=30) | 46.7% (n=30) | 41.4% (n=29) |
| bedrock_qwen32b | 90.3% (n=31) | 90.0% (n=30) | 90.0% (n=30) | 89.7% (n=29) |
| bedrock_nova | 74.2% (n=31) | 66.7% (n=30) | 60.0% (n=30) | 62.1% (n=29) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 6 | 50.0% | 25 | 64.0% | 14.0% |
| decider | hi | 8 | 100.0% | 22 | 50.0% | -50.0% |
| decider | ar | 9 | 44.4% | 21 | 66.7% | 22.2% |
| decider | hinglish | 0 | n/a | 29 | 55.2% | n/a |
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
| bedrock_nova | en | 6 | 83.3% | 25 | 72.0% | -11.3% |
| bedrock_nova | hi | 8 | 37.5% | 22 | 77.3% | 39.8% |
| bedrock_nova | ar | 9 | 22.2% | 21 | 76.2% | 54.0% |
| bedrock_nova | hinglish | 0 | n/a | 29 | 62.1% | n/a |

### Calibration (category, own ids)

**decider**: ECE (10 bins) = 0.165

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 2 | 0.181 | 0.000 |
| 0.2-0.3 | 11 | 0.233 | 0.000 |
| 0.3-0.4 | 6 | 0.334 | 0.500 |
| 0.4-0.5 | 19 | 0.446 | 0.158 |
| 0.5-0.6 | 13 | 0.540 | 0.538 |
| 0.6-0.7 | 11 | 0.656 | 0.909 |
| 0.7-0.8 | 11 | 0.754 | 1.000 |
| 0.8-0.9 | 33 | 0.847 | 0.727 |
| 0.9-1.0 | 14 | 0.934 | 1.000 |

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

**bedrock_nova**: ECE (10 bins) = 0.034

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 27 | 0.000 | 0.000 |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 0 | n/a | n/a |
| 0.6-0.7 | 0 | n/a | n/a |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 9 | 0.800 | 0.444 |
| 0.9-1.0 | 84 | 0.903 | 0.893 |

### Low-confidence-to-human sweep (category, own ids)

**decider**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 31.7% | 82 | 80.5% |
| 0.55 | 39.2% | 73 | 84.9% |
| 0.60 | 42.5% | 69 | 85.5% |
| 0.65 | 46.7% | 64 | 85.9% |
| 0.70 | 51.7% | 58 | 84.5% |
| 0.75 | 55.8% | 53 | 83.0% |
| 0.80 | 60.8% | 47 | 80.9% |
| 0.85 | 75.8% | 29 | 100.0% |
| 0.90 | 88.3% | 14 | 100.0% |
| 0.95 | 95.8% | 5 | 100.0% |

Threshold for >=95% auto-lane accuracy: 0.84, human share 75.0%.

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
| 0.50 | 22.5% | 93 | 84.9% |
| 0.55 | 22.5% | 93 | 84.9% |
| 0.60 | 22.5% | 93 | 84.9% |
| 0.65 | 22.5% | 93 | 84.9% |
| 0.70 | 22.5% | 93 | 84.9% |
| 0.75 | 22.5% | 93 | 84.9% |
| 0.80 | 22.5% | 93 | 84.9% |
| 0.85 | 30.0% | 84 | 89.3% |
| 0.90 | 30.0% | 84 | 89.3% |
| 0.95 | 95.8% | 5 | 100.0% |

Threshold for >=95% auto-lane accuracy: 0.91, human share 95.8%.

### Injection (is_injection, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 variants n | all caught |
|---|---|---|---|---|---|---|---|
| decider | 120 | 60 | 60 | 60.0% | 0.0% | 60 | False |
| laya | 120 | 60 | 60 | 35.0% | 11.7% | 60 | False |
| laya_calibrated | 120 | 60 | 60 | 35.0% | 11.7% | 60 | False |
| bedrock_qwen32b | 120 | 60 | 60 | 90.0% | 0.0% | 60 | False |
| bedrock_nova | 120 | 60 | 60 | 90.0% | 0.0% | 60 | False |

## Set: stale

Set size 48; ids common to all backends with preds: 48; missing preds: ['laya', 'laya_calibrated', 'bedrock_qwen32b', 'bedrock_nova'].

### Accuracy per question (own ids / common ids)

| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |
|---|---|---|---|---|---|---|---|---|
| decider | 48 | 37.5% | 39.6% | 100.0% | 48 | 37.5% | 39.6% | 100.0% |

### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 48 | 819 | 1512 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 40 | 37.5% | 8 | 37.5% | 0.0% | 62.5% |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 27.6% (n=29) | 66.7% (n=3) | 0.0% (n=8) | 100.0% (n=8) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 5 | 40.0% | 24 | 25.0% | -15.0% |
| decider | hi | 3 | 66.7% | 0 | n/a | n/a |
| decider | ar | 8 | 0.0% | 0 | n/a | n/a |
| decider | hinglish | 0 | n/a | 8 | 100.0% | n/a |

### Calibration (category, own ids)

**decider**: ECE (10 bins) = 0.202

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 1 | 0.296 | 0.000 |
| 0.3-0.4 | 12 | 0.368 | 0.000 |
| 0.4-0.5 | 16 | 0.465 | 0.562 |
| 0.5-0.6 | 8 | 0.538 | 0.375 |
| 0.6-0.7 | 6 | 0.656 | 0.667 |
| 0.7-0.8 | 4 | 0.714 | 0.250 |
| 0.8-0.9 | 1 | 0.809 | 1.000 |
| 0.9-1.0 | 0 | n/a | n/a |

### Low-confidence-to-human sweep (category, own ids)

**decider**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 60.4% | 19 | 47.4% |
| 0.55 | 70.8% | 14 | 50.0% |
| 0.60 | 77.1% | 11 | 54.5% |
| 0.65 | 85.4% | 7 | 28.6% |
| 0.70 | 89.6% | 5 | 40.0% |
| 0.75 | 97.9% | 1 | 100.0% |
| 0.80 | 97.9% | 1 | 100.0% |
| 0.85 | 100.0% | 0 | n/a |
| 0.90 | 100.0% | 0 | n/a |
| 0.95 | 100.0% | 0 | n/a |

Threshold for >=95% auto-lane accuracy: 0.72, human share 95.8%.

### Injection (is_injection, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 variants n | all caught |
|---|---|---|---|---|---|---|---|
| decider | 48 | 0 | 48 | n/a | 0.0% | 0 | False |

## Set: control

Set size 29; ids common to all backends with preds: 29; missing preds: ['laya', 'laya_calibrated', 'bedrock_qwen32b', 'bedrock_nova'].

### Accuracy per question (own ids / common ids)

| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |
|---|---|---|---|---|---|---|---|---|
| decider | 29 | 72.4% | 37.9% | 100.0% | 29 | 72.4% | 37.9% | 100.0% |

### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 29 | 879 | 1456 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 25 | 68.0% | 4 | 100.0% | 32.0% | 32.0% |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 61.9% (n=21) | 100.0% (n=1) | n/a (n=0) | 100.0% (n=7) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 0 | n/a | 21 | 61.9% | n/a |
| decider | hi | 1 | 100.0% | 0 | n/a | n/a |
| decider | ar | 0 | n/a | 0 | n/a | n/a |
| decider | hinglish | 0 | n/a | 7 | 100.0% | n/a |

### Calibration (category, own ids)

**decider**: ECE (10 bins) = 0.337

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 11 | 0.462 | 1.000 |
| 0.5-0.6 | 4 | 0.548 | 0.250 |
| 0.6-0.7 | 6 | 0.659 | 0.667 |
| 0.7-0.8 | 4 | 0.712 | 0.250 |
| 0.8-0.9 | 4 | 0.808 | 1.000 |
| 0.9-1.0 | 0 | n/a | n/a |

### Low-confidence-to-human sweep (category, own ids)

**decider**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 37.9% | 18 | 55.6% |
| 0.55 | 48.3% | 15 | 60.0% |
| 0.60 | 51.7% | 14 | 64.3% |
| 0.65 | 65.5% | 10 | 50.0% |
| 0.70 | 72.4% | 8 | 62.5% |
| 0.75 | 86.2% | 4 | 100.0% |
| 0.80 | 86.2% | 4 | 100.0% |
| 0.85 | 100.0% | 0 | n/a |
| 0.90 | 100.0% | 0 | n/a |
| 0.95 | 100.0% | 0 | n/a |

Threshold for >=95% auto-lane accuracy: 0.71, human share 82.8%.

### Injection (is_injection, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 variants n | all caught |
|---|---|---|---|---|---|---|---|
| decider | 29 | 0 | 29 | n/a | 0.0% | 0 | False |

## Set: incident

Set size 78; ids common to all backends with preds: 78; missing preds: ['laya', 'laya_calibrated', 'bedrock_qwen32b', 'bedrock_nova'].

### Accuracy per question (own ids / common ids)

| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |
|---|---|---|---|---|---|---|---|---|
| decider | 78 | 59.0% | 47.4% | 100.0% | 78 | 59.0% | 47.4% | 100.0% |

### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 78 | 781 | 874 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 47 | 78.7% | 31 | 29.0% | -49.7% | 21.3% |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 56.8% (n=74) | n/a (n=0) | n/a (n=0) | 100.0% (n=4) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 13 | 100.0% | 61 | 47.5% | -52.5% |
| decider | hi | 0 | n/a | 0 | n/a | n/a |
| decider | ar | 0 | n/a | 0 | n/a | n/a |
| decider | hinglish | 0 | n/a | 4 | 100.0% | n/a |

### Calibration (category, own ids)

**decider**: ECE (10 bins) = 0.362

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 0 | n/a | n/a |
| 0.4-0.5 | 0 | n/a | n/a |
| 0.5-0.6 | 32 | 0.559 | 0.000 |
| 0.6-0.7 | 16 | 0.646 | 1.000 |
| 0.7-0.8 | 0 | n/a | n/a |
| 0.8-0.9 | 30 | 0.843 | 1.000 |
| 0.9-1.0 | 0 | n/a | n/a |

### Low-confidence-to-human sweep (category, own ids)

**decider**

| threshold | human share | n auto | auto-lane accuracy |
|---|---|---|---|
| 0.50 | 0.0% | 78 | 59.0% |
| 0.55 | 19.2% | 63 | 73.0% |
| 0.60 | 41.0% | 46 | 100.0% |
| 0.65 | 60.3% | 31 | 100.0% |
| 0.70 | 61.5% | 30 | 100.0% |
| 0.75 | 61.5% | 30 | 100.0% |
| 0.80 | 61.5% | 30 | 100.0% |
| 0.85 | 94.9% | 4 | 100.0% |
| 0.90 | 100.0% | 0 | n/a |
| 0.95 | 100.0% | 0 | n/a |

Threshold for >=95% auto-lane accuracy: 0.59, human share 41.0%.

### Injection (is_injection, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 variants n | all caught |
|---|---|---|---|---|---|---|---|
| decider | 78 | 0 | 78 | n/a | 0.0% | 0 | False |

## Set: smoke

Set size 14; ids common to all backends with preds: 14; missing preds: ['bedrock_nova'].

### Accuracy per question (own ids / common ids)

| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |
|---|---|---|---|---|---|---|---|---|
| decider | 14 | 57.1% | 28.6% | 100.0% | 14 | 57.1% | 28.6% | 100.0% |
| laya | 14 | 42.9% | 35.7% | 85.7% | 14 | 42.9% | 35.7% | 85.7% |
| laya_calibrated | 14 | 42.9% | 35.7% | 85.7% | 14 | 42.9% | 35.7% | 85.7% |
| bedrock_qwen32b | 14 | 85.7% | 35.7% | 100.0% | 14 | 85.7% | 35.7% | 100.0% |

### Latency per ticket (ms, own ids)

| backend | n | p50 | p95 |
|---|---|---|---|
| decider | 14 | 775 | 878 |
| laya | 14 | 87 | 96 |
| laya_calibrated | 14 | 87 | 96 |
| bedrock_qwen32b | 14 | 3763 | 4059 |

### Payroll-window bounce (category, own ids)

| backend | n window | acc window | n rest | acc rest | drop | misroute window |
|---|---|---|---|---|---|---|
| decider | 13 | 53.8% | 1 | 100.0% | 46.2% | 46.2% |
| laya | 13 | 38.5% | 1 | 100.0% | 61.5% | 61.5% |
| laya_calibrated | 13 | 38.5% | 1 | 100.0% | 61.5% | 61.5% |
| bedrock_qwen32b | 13 | 84.6% | 1 | 100.0% | 15.4% | 15.4% |

### Language subsets (category accuracy / n)

| backend | en | hi | ar | hinglish |
|---|---|---|---|---|
| decider | 77.8% (n=9) | 100.0% (n=1) | 0.0% (n=4) | n/a (n=0) |
| laya | 33.3% (n=9) | 0.0% (n=1) | 75.0% (n=4) | n/a (n=0) |
| laya_calibrated | 33.3% (n=9) | 0.0% (n=1) | 75.0% (n=4) | n/a (n=0) |
| bedrock_qwen32b | 100.0% (n=9) | 0.0% (n=1) | 75.0% (n=4) | n/a (n=0) |

### PII over-masking impact (category accuracy with vs without a PERSON mask)

| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |
|---|---|---|---|---|---|---|
| decider | en | 1 | 100.0% | 8 | 75.0% | -25.0% |
| decider | hi | 1 | 100.0% | 0 | n/a | n/a |
| decider | ar | 4 | 0.0% | 0 | n/a | n/a |
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

**decider**: ECE (10 bins) = 0.246

| bin | n | mean conf | accuracy |
|---|---|---|---|
| 0.0-0.1 | 0 | n/a | n/a |
| 0.1-0.2 | 0 | n/a | n/a |
| 0.2-0.3 | 0 | n/a | n/a |
| 0.3-0.4 | 1 | 0.377 | 0.000 |
| 0.4-0.5 | 3 | 0.461 | 0.333 |
| 0.5-0.6 | 2 | 0.569 | 0.500 |
| 0.6-0.7 | 1 | 0.619 | 1.000 |
| 0.7-0.8 | 2 | 0.741 | 0.000 |
| 0.8-0.9 | 4 | 0.845 | 1.000 |
| 0.9-1.0 | 1 | 0.944 | 1.000 |

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
| 0.50 | 28.6% | 10 | 70.0% |
| 0.55 | 35.7% | 9 | 66.7% |
| 0.60 | 42.9% | 8 | 75.0% |
| 0.65 | 50.0% | 7 | 71.4% |
| 0.70 | 50.0% | 7 | 71.4% |
| 0.75 | 57.1% | 6 | 83.3% |
| 0.80 | 64.3% | 5 | 100.0% |
| 0.85 | 85.7% | 2 | 100.0% |
| 0.90 | 92.9% | 1 | 100.0% |
| 0.95 | 100.0% | 0 | n/a |

Threshold for >=95% auto-lane accuracy: 0.77, human share 64.3%.

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

### Injection (is_injection, own ids)

| backend | n | n inj | n benign | recall | FPR | PN-101129 variants n | all caught |
|---|---|---|---|---|---|---|---|
| decider | 14 | 1 | 13 | 100.0% | 0.0% | 1 | True |
| laya | 14 | 1 | 13 | 0.0% | 7.7% | 1 | False |
| laya_calibrated | 14 | 1 | 13 | 0.0% | 7.7% | 1 | False |
| bedrock_qwen32b | 14 | 1 | 13 | 100.0% | 0.0% | 1 | True |
