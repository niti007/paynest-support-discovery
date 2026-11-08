# Gate 0 validation (independent), 2026-11-08

Account 554559231166, eu-north-1, branch aws-a7. Validator made no AWS changes; only --cleanup runs (found nothing) and cheap re-runs.

| Check | Result | Evidence | Notes |
|---|---|---|---|
| G0.1 MCP smoke | PASS | search_documentation(agent_skills) returned skill_name amazon-bedrock; retrieve_skill(amazon-bedrock) returned SKILL.md; STS Account 554559231166; freetier plan FREE / ACTIVE, expires 2027-05-07 | Remaining credits now USD 140 (builders recorded 100) |
| G0.2 Coverage | PASS | 13 rows map to 14 files, all PASS except p1_bedrock_eval BLOCKED (fallback "DeepEval-only judge, cross-checked by a second Bedrock model"); no FAIL in any JSON; evidence non-empty | p1_bedrock_llm.json status is PASS, while readiness doc calls the row BLOCKED-with-fallback (cosmetic) |
| G0.3 Re-run | PASS | plan FREE ACTIVE; fargate vCPU 12, rds 40, elasticache nodes 300 (unchanged); embed dims 1024 for en/hi/ar/control; Nova Lite, Qwen3-235B, Qwen3-32B, gpt-oss-120b PASS; Cohere v4 + Titan PASS; Claude Haiku 4.5 / Sonnet 4.6 FAIL (use-case form). Only diff vs builders: credits 100 -> 140, latencies | Cost under USD 0.02. Results restored via git checkout; tree clean |
| G0.4 Cleanup | PASS | all 6 exit 0, nothing deleted: eval 1 line (list_jobs AccessDenied, role NoSuchEntity); security 1 line ("secret deleted"); ops 1 line (log group and budget not found); p2_network 6 lines (0 ACTIVE task defs, RepositoryNotFound, LogGroup not found); p2_sqs 4 lines (all NotFound/NonExistentQueue); p3_data 2 lines (teardown start/done). p2_verify_clean exit 0: VPCs [], active TaskDefs [] | security prints "secret deleted" because ForceDeleteWithoutRecovery succeeds silently on a missing secret; sweep confirms 0 secrets. p2_verify_clean prints 16 TAGGED ARNs, all stale (see G0.5) |
| G0.5 Sweep | PASS | 0 LIVE probe resources; see lists below | Needed 2 run_script calls (10-service cap per script). us-east-1 tagging API denied by SCP |
| G0.6 config | PASS (1 nit) | MODEL_CHEAP eu.amazon.nova-lite-v1:0; HARD qwen.qwen3-235b-a22b-2507-v1:0; FALLBACK qwen.qwen3-32b-v1:0; EMBED eu.cohere.embed-v4:0; DIMS 1024; REGION eu-north-1; prices all cite Price List API, only Titan is None+TODO; no secrets | Stale comment "Model choice is PROVISIONAL: the user confirms"; PLAN credits 100.0 (live 140) |
| G0.7 Readiness doc | PASS | 13 rows present; models table equals config.py; spot checks match JSON: pgvector 0.8.2, Valkey TTL 30 (server 9.0.0), embed dims 1024, Fargate vCPU 12, bootstrap version 33, RDS 400,554 ms, Valkey 656,334 ms, Nova on-demand 10 RPM/200k TPM, total cost ~0.08 (sum 0.1114 minus p3 triple-count) | Mismatches: credits (100 vs 140 live); doc row status for Bedrock LLMs vs JSON PASS; doc says "Claude 5.5 quota 0" ok |
| G0.8 Hygiene | PASS | grep for AKIA/ASIA/aws_secret/session token/private key/FwoGZX: 0 hits; password literals: 0 hits; every creating probe has --cleanup and a finally (eval 107, ops 90, security 64, p2_network 397, p2_sqs 100, p3_data 378) | p1_bedrock_llm.json ids_resolved disagrees with config.py: cheap = qwen.qwen3-32b-v1:0 (config: eu.amazon.nova-lite-v1:0), hard = openai.gpt-oss-120b-1:0 (config: qwen3-235b), fallback = eu.amazon.nova-lite-v1:0 (config: qwen3-32b). Confirmed; doc already notes it |

## G0.5 (a) tagged project=paynest-probe, eu-north-1 (16 ARNs)
- vpc-endpoint vpce-0d45826fea9d4e702, vpce-05aecfb1d3b7b00a2, vpce-0734bc7622bedc2e8, vpce-062a147b70c37e126, vpce-0463f7c411be4e3e9, vpce-08d93ba3e69aa1f02, vpce-015d2032f8ef043a5, vpce-0202a7dfd8a7b007d: all STALE_INDEX (InvalidVpcEndpointId.NotFound)
- ecs cluster paynest-probe-data: STALE_INDEX (describe returns status INACTIVE, deleted tombstone)
- ecs cluster paynest-probe-cluster: STALE_INDEX (INACTIVE)
- ecs task paynest-probe-data/1993ae9f005a47e186ec2597a216af29, paynest-probe-cluster/a1e479d2bb5e46238ff472b1d7015a45, paynest-probe-cluster/b05d5321aaed444ab67f1f0c0198c5bf: STALE_INDEX (lastStatus STOPPED, history only)
- task-definition paynest-probe-task-a:1, paynest-probe-task-b:1: STALE_INDEX (INACTIVE)
- kms key 43c53102-ad91-4e38-98a8-16a0b188b46a: expected, PendingDeletion, DeletionDate 2026-11-15T15:27:11.230Z
- LIVE count: 0
us-east-1: GetResources denied by SCP (explicit deny tag:GetResources, p-xxwaz5nn). Not verified by tags; no probe created resources there.

## G0.5 (b) direct lists, eu-north-1
- VPCs: only default vpc-0bec3689522bd5b6c. VPC endpoints (any state): none. NAT gateways: none. EIPs: none. EC2 instances: none. EBS volumes: none.
- Load balancers: none. Target groups: none.
- RDS instances: none. RDS clusters: none. DB subnet groups: none.
- ElastiCache replication groups: none. Cache clusters: none. Serverless caches: none. Cache subnet groups: none.
- ECS clusters: none (ListClusters empty). ACTIVE task defs prefix paynest-probe: none.
- ECR: cdk-hnb659fds-container-assets-554559231166-eu-north-1 only (expected).
- SQS queues: none. Scheduler schedules: none (group "default" only).
- IAM roles prefix paynest-probe: none. Other non-service roles: AccountFullAccessRole, AWSManaged* x2, 5 cdk-hnb659fds-* roles (expected).
- Log groups (all and /paynest-probe): none.
- S3: cdk-hnb659fds-assets-554559231166-eu-north-1 only (expected).
- Secrets Manager (incl. planned deletion): none.
- Customer KMS keys: 43c53102-ad91-4e38-98a8-16a0b188b46a PendingDeletion, DeletionDate 2026-11-15T15:27:11.230Z.
- Non-default security groups: none (default SG only).
- CloudFormation stacks: CDKToolkit CREATE_COMPLETE. SSM param /cdk-bootstrap/hnb659fds/version.

## Items for the readiness doc "not recorded"
- KMS DeletionDate: 2026-11-15T15:27:11Z.
- Tag sweep: 16 tagged ARNs, 0 live (8 VPC endpoints gone, 2 clusters INACTIVE, 3 tasks STOPPED, 2 task defs INACTIVE, 1 KMS key pending deletion).
- Live credits: USD 140 (doc says 100).

## GATE 0: PASS
