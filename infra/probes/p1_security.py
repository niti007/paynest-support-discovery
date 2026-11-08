"""P1 probe: KMS encrypt/decrypt round trip + Secrets Manager create/describe/delete. Never calls GetSecretValue."""
import argparse

from p1_common import *  # noqa: F403

PROBE = "p1_security"
SECRET = "paynest-probe-secret"
PLAINTEXT = b"IBAN AE070331234567890123456"


def cleanup(kms, sm):
    out = []
    try:
        sm.delete_secret(SecretId=SECRET, ForceDeleteWithoutRecovery=True)
        out.append("secret deleted")
    except Exception as e:  # noqa: BLE001
        out.append(f"secret: {err_text(e)}")
    for page in kms.get_paginator("list_aliases").paginate():
        for a in page["Aliases"]:
            if a["AliasName"].startswith("alias/paynest-probe-") and a.get("TargetKeyId"):
                try:
                    kms.schedule_key_deletion(KeyId=a["TargetKeyId"], PendingWindowInDays=7)
                    out.append(f"key {a['TargetKeyId']} scheduled")
                except Exception as e:  # noqa: BLE001
                    out.append(f"key {a['TargetKeyId']}: {err_text(e)}")
                try:
                    kms.delete_alias(AliasName=a["AliasName"])
                except Exception:  # noqa: BLE001
                    pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()
    kms, sm = client("kms"), client("secretsmanager")
    if args.cleanup:
        print(cleanup(kms, sm))
        return
    r = new_result()
    key_id = None
    try:
        try:
            k = kms.create_key(Description="paynest-probe key", KeyUsage="ENCRYPT_DECRYPT", KeySpec="SYMMETRIC_DEFAULT", Tags=[{"TagKey": "project", "TagValue": "paynest-probe"}, {"TagKey": "probe", "TagValue": PROBE}])
            key_id = k["KeyMetadata"]["KeyId"]
            kms.create_alias(AliasName=f"alias/paynest-probe-{key_id[:8]}", TargetKeyId=key_id)
            ct, ms1 = timed(kms.encrypt, KeyId=key_id, Plaintext=PLAINTEXT)
            pt, ms2 = timed(kms.decrypt, CiphertextBlob=ct["CiphertextBlob"])
            r["latency_ms"].update(kms_encrypt=ms1, kms_decrypt=ms2)
            check(r, "kms_roundtrip", pt["Plaintext"] == PLAINTEXT, f"key={key_id} ct_len={len(ct['CiphertextBlob'])} match={pt['Plaintext'] == PLAINTEXT}")
            r["ids_resolved"]["kms_key_id"] = key_id
        except Exception as e:  # noqa: BLE001
            check(r, "kms_roundtrip", False, err_text(e))
            r["status"], r["error"] = "FAIL", err_text(e)
        try:
            sc = sm.create_secret(Name=SECRET, SecretString="dummy-not-a-real-secret", Tags=TAGS(PROBE))
            d = sm.describe_secret(SecretId=SECRET)
            check(r, "secretsmanager_create_describe", d["Name"] == SECRET, f"arn={sc['ARN']} tags={[t['Key'] for t in d.get('Tags', [])]}")
            r["ids_resolved"]["secret_arn"] = sc["ARN"]
        except Exception as e:  # noqa: BLE001
            check(r, "secretsmanager_create_describe", False, err_text(e))
            r["status"], r["error"] = "FAIL", err_text(e)
    finally:
        cl = cleanup(kms, sm)
        r["ids_resolved"]["cleanup"] = cl
        check(r, "cleanup", any("scheduled" in c for c in cl) and any("secret deleted" in c for c in cl), cl)
        if key_id:
            r["ids_resolved"]["kms_state"] = kms.describe_key(KeyId=key_id)["KeyMetadata"]["KeyState"]
        if not r["checks"][-1]["pass"]:
            r["status"] = "FAIL"
        save(PROBE, r)


if __name__ == "__main__":
    main()
