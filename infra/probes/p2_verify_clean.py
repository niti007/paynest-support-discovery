import sys
import boto3
s = boto3.Session(profile_name="paynest", region_name="eu-north-1")
ecs = s.client("ecs")
if "--fix-taskdefs" in sys.argv:
    for fam in ("paynest-probe-task-a", "paynest-probe-task-b"):
        for t in ecs.list_task_definitions(familyPrefix=fam, status="ACTIVE")["taskDefinitionArns"]:
            ecs.deregister_task_definition(taskDefinition=t)
            print("deregistered", t)
r = s.client("resourcegroupstaggingapi").get_resources(TagFilters=[{"Key": "project", "Values": ["paynest-probe"]}])
print("TAGGED:", [x["ResourceARN"] for x in r["ResourceTagMappingList"]])
ec2 = s.client("ec2")
print("VPCs:", [v["VpcId"] for v in ec2.describe_vpcs(Filters=[{"Name": "tag:project", "Values": ["paynest-probe"]}])["Vpcs"]])
print("TaskDefs active (p2 families):", [t for t in ecs.list_task_definitions(status="ACTIVE")["taskDefinitionArns"] if "task-a" in t or "task-b" in t])
