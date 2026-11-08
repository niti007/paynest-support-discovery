"""P1 probe: Bedrock Evaluations (LLM-as-judge, bring-your-own responses). ONE create attempt only.
Creates tagged S3 bucket + IAM role, attempts the job, stops/deletes job, removes bucket and role in finally."""
import argparse
import json
import time
import uuid

from p1_common import *  # noqa: F403

PROBE = "p1_bedrock_eval"
ROLE = "paynest-probe-eval-role"
JUDGE = "qwen.qwen3-32b-v1:0"  # cheap tier that passed p1_bedrock_llm
FALLBACK = "DeepEval-only judge, cross-checked by a second Bedrock model"
RECORDS = [
    {"prompt": "What is the payroll cut-off?", "referenceResponse": "Payroll files must be submitted before 14:00 UAE time.",
     "modelResponses": [{"response": "Submit the file before 14:00 UAE time.", "modelIdentifier": "byo-model"}]},
    {"prompt": "How do I reset access?", "referenceResponse": "Ask your admin to re-send the invite.",
     "modelResponses": [{"response": "Your admin can re-send the invitation.", "modelIdentifier": "byo-model"}]},
]


def cleanup(s3, iam, br, bucket=None):
    out = []
    try:
        for j in br.list_evaluation_jobs()["jobSummaries"]:
            if j["jobName"].startswith("paynest-probe-"):
                try:
                    if j["status"] in ("InProgress", "Starting"):
                        br.stop_evaluation_job(jobIdentifier=j["jobArn"])
                    out.append(f"job {j['jobName']} stop requested")
                except Exception as e:  # noqa: BLE001
                    out.append(f"job {j['jobName']}: {err_text(e)}")
    except Exception as e:  # noqa: BLE001
        out.append(f"list_jobs: {err_text(e)}")
    buckets = [bucket] if bucket else [b["Name"] for b in s3.list_buckets()["Buckets"] if b["Name"].startswith("paynest-probe-eval-")]
    for b in buckets:
        try:
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=b):
                for o in page.get("Contents", []):
                    s3.delete_object(Bucket=b, Key=o["Key"])
            s3.delete_bucket(Bucket=b)
            out.append(f"bucket {b} deleted")
        except Exception as e:  # noqa: BLE001
            out.append(f"bucket {b}: {err_text(e)}")
    try:
        for p in iam.list_role_policies(RoleName=ROLE)["PolicyNames"]:
            iam.delete_role_policy(RoleName=ROLE, PolicyName=p)
        iam.delete_role(RoleName=ROLE)
        out.append("role deleted")
    except Exception as e:  # noqa: BLE001
        out.append(f"role: {err_text(e)}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()
    s3, iam, br = client("s3"), client("iam", region="us-east-1"), client("bedrock")
    if args.cleanup:
        print(cleanup(s3, iam, br))
        return
    r = new_result()
    bucket = f"paynest-probe-eval-{uuid.uuid4().hex[:10]}"
    job_arn = None
    try:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION})
        s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": TAGS(PROBE)})
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={k: True for k in ["BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"]})
        s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
        s3.put_object(Bucket=bucket, Key="in/dataset.jsonl", Body="\n".join(json.dumps(x, ensure_ascii=False) for x in RECORDS).encode())
        check(r, "s3_bucket_hardened", True, bucket)
        trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "bedrock.amazonaws.com"}, "Action": "sts:AssumeRole",
                 "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}, "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{REGION}:{ACCOUNT}:evaluation-job/*"}}}]}
        role_arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust), Tags=TAGS(PROBE))["Role"]["Arn"]
        iam.put_role_policy(RoleName=ROLE, PolicyName="eval", PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket", "s3:PutObject"], "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"]},
            {"Effect": "Allow", "Action": ["bedrock:InvokeModel"], "Resource": [f"arn:aws:bedrock:{REGION}::foundation-model/{JUDGE}"]}]}))
        check(r, "iam_role_created", True, role_arn)
        time.sleep(12)  # IAM propagation
        r["ids_resolved"].update(bucket=bucket, role=role_arn, judge=JUDGE)
        try:  # the single create attempt
            resp = br.create_evaluation_job(
                jobName=f"paynest-probe-eval-{uuid.uuid4().hex[:6]}", roleArn=role_arn, applicationType="ModelEvaluation",
                jobTags=TAGS_LC(PROBE) and [{"key": "project", "value": "paynest-probe"}, {"key": "probe", "value": PROBE}],
                evaluationConfig={"automated": {"datasetMetricConfigs": [{"taskType": "General", "dataset": {"name": "byo", "datasetLocation": {"s3Uri": f"s3://{bucket}/in/dataset.jsonl"}},
                                                                          "metricNames": ["Builtin.Correctness"]}],
                                                "evaluatorModelConfig": {"bedrockEvaluatorModels": [{"modelIdentifier": JUDGE}]}}},
                inferenceConfig={"models": [{"precomputedInferenceSource": {"inferenceSourceIdentifier": "byo-model"}}]},
                outputDataConfig={"s3Uri": f"s3://{bucket}/out/"})
            job_arn = resp["jobArn"]
            st = br.get_evaluation_job(jobIdentifier=job_arn)["status"]
            check(r, "create_evaluation_job_accepted", True, f"{job_arn} status={st}")
            r["ids_resolved"]["job_arn"] = job_arn
            br.stop_evaluation_job(jobIdentifier=job_arn)
            check(r, "stop_evaluation_job", True, "stop requested")
            try:
                br.batch_delete_evaluation_job(jobIdentifiers=[job_arn])
            except Exception as e:  # noqa: BLE001
                check(r, "delete_evaluation_job", False, err_text(e))
        except Exception as e:  # noqa: BLE001
            check(r, "create_evaluation_job_accepted", False, err_text(e))
            r["status"], r["error"], r["fallback"] = "BLOCKED", err_text(e), FALLBACK
            r["checks"].append({"name": "rag_eval", "pass": False, "evidence": "not attempted: single create attempt policy; same service blocked"})
    except Exception as e:  # noqa: BLE001
        r["status"], r["error"] = "FAIL", err_text(e)
    finally:
        r["ids_resolved"]["cleanup"] = cleanup(s3, iam, br, bucket)
        save(PROBE, r)


if __name__ == "__main__":
    main()
