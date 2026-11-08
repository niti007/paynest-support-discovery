"""P1 probe: CloudWatch Logs + Logs Insights IBAN count, Budgets create/describe/delete (no subscribers), one Cost Explorer call."""
import argparse
import datetime as dt
import time

from p1_common import *  # noqa: F403

PROBE = "p1_ops"
LG = "/paynest-probe/ops"
BUDGET = "paynest-probe-budget"


def cleanup(logs, bud):
    out = []
    try:
        logs.delete_log_group(logGroupName=LG)
        out.append("log group deleted")
    except Exception as e:  # noqa: BLE001
        out.append(f"log group: {err_text(e)}")
    try:
        bud.delete_budget(AccountId=ACCOUNT, BudgetName=BUDGET)
        out.append("budget deleted")
    except Exception as e:  # noqa: BLE001
        out.append(f"budget: {err_text(e)}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()
    logs, bud, ce = client("logs"), client("budgets", region="us-east-1"), client("ce", region="us-east-1")
    if args.cleanup:
        print(cleanup(logs, bud))
        return
    r = new_result()
    blocked = []
    try:
        try:
            logs.create_log_group(logGroupName=LG, tags={"project": "paynest-probe", "probe": PROBE})
            logs.put_retention_policy(logGroupName=LG, retentionInDays=1)
            logs.create_log_stream(logGroupName=LG, logStreamName="s1")
            now = int(time.time() * 1000)
            msgs = ["service started", "payroll batch 17 accepted", "user login ok", "refund issued to IBAN AE070331234567890123456", "heartbeat"]
            logs.put_log_events(logGroupName=LG, logStreamName="s1", logEvents=[{"timestamp": now + i, "message": m} for i, m in enumerate(msgs)])
            check(r, "logs_put_5_events", True, "5 events, retention=1d")
            count = None
            t0 = time.time()
            for _ in range(8):
                time.sleep(8)
                qid = logs.start_query(logGroupName=LG, startTime=int(time.time()) - 3600, endTime=int(time.time()) + 60,
                                       queryString=r"filter @message like /AE\d{21}/ | stats count(*) as n")["queryId"]
                for _ in range(30):
                    time.sleep(1)
                    q = logs.get_query_results(queryId=qid)
                    if q["status"] in ("Complete", "Failed", "Cancelled"):
                        break
                if q["status"] == "Complete" and q["results"]:
                    count = int(q["results"][0][0]["value"])
                    if count >= 1:
                        break
            r["latency_ms"]["insights_total"] = int((time.time() - t0) * 1000)
            check(r, "insights_iban_count_is_1", count == 1, f"count={count}")
            if count != 1:
                r["status"] = "FAIL"
        except Exception as e:  # noqa: BLE001
            check(r, "logs_insights", False, err_text(e))
            r["status"], r["error"] = "FAIL", err_text(e)
        try:
            bud.create_budget(AccountId=ACCOUNT, Budget={"BudgetName": BUDGET, "BudgetLimit": {"Amount": "20", "Unit": "USD"}, "TimeUnit": "MONTHLY", "BudgetType": "COST"})
            d = bud.describe_budget(AccountId=ACCOUNT, BudgetName=BUDGET)["Budget"]
            check(r, "budget_create_describe", d["BudgetLimit"]["Amount"] in ("20", "20.0"), f"{d['BudgetName']} {d['BudgetLimit']}")
        except Exception as e:  # noqa: BLE001
            check(r, "budget_create_describe", False, err_text(e))
            blocked.append(f"budgets: {err_text(e)}")
        try:
            end = dt.date.today()
            res = ce.get_cost_and_usage(TimePeriod={"Start": str(end - dt.timedelta(days=7)), "End": str(end)}, Granularity="DAILY", Metrics=["UnblendedCost"], GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}])
            tot = {}
            for d_ in res["ResultsByTime"]:
                for g in d_["Groups"]:
                    tot[g["Keys"][0]] = tot.get(g["Keys"][0], 0.0) + float(g["Metrics"]["UnblendedCost"]["Amount"])
            r["ids_resolved"]["ce_7d_by_service"] = {k: round(v, 4) for k, v in tot.items()}
            r["ids_resolved"]["ce_7d_total_usd"] = round(sum(tot.values()), 4)
            r["est_cost_usd"] += 0.01
            check(r, "cost_explorer_7d", True, f"total={sum(tot.values()):.4f} USD over {len(tot)} services")
        except Exception as e:  # noqa: BLE001
            check(r, "cost_explorer_7d", False, err_text(e))
            blocked.append(f"ce: {err_text(e)}")
    finally:
        r["ids_resolved"]["cleanup"] = cleanup(logs, bud)
        if blocked:
            r["error"] = (r["error"] + "; " if r["error"] else "") + "; ".join(blocked)
            if r["status"] == "PASS":
                r["status"] = "BLOCKED"
                r["fallback"] = "User creates the budget in the Billing console (and reads costs there); logs/Insights unaffected"
        save(PROBE, r)


if __name__ == "__main__":
    main()
