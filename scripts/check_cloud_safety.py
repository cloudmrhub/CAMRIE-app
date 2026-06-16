#!/usr/bin/env python3
"""
Check CAMRIE cloud resources for unexpected or long-running activity.

The script inspects:
  - EC2 running/pending instances
  - AWS Batch compute environments and active jobs
  - ECS running tasks from the older Fargate path
  - Step Functions running executions

Examples:
  python scripts/check_cloud_safety.py --profile nyu --region us-east-1
  python scripts/check_cloud_safety.py --profile nyu --fail-on-warn
  python scripts/check_cloud_safety.py --allow-name Cancelit-env-1
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


ACTIVE_BATCH_STATUSES = ("SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING")
COMPLETED_BATCH_STATUSES = ("FAILED", "SUCCEEDED")


@dataclass
class Issue:
    level: str
    message: str


class Report:
    def __init__(self):
        self.issues = []

    def issue(self, level, message):
        self.issues.append(Issue(level, message))
        print(f"  {level}: {message}")

    def ok(self, message):
        print(f"  OK: {message}")

    def info(self, message):
        print(f"  INFO: {message}")

    def section(self, title):
        print(f"\n== {title} ==")

    def exit_code(self, fail_on_warn=False):
        if any(i.level == "CRITICAL" for i in self.issues):
            return 2
        if fail_on_warn and any(i.level == "WARN" for i in self.issues):
            return 2
        return 0


class AwsCli:
    def __init__(self, profile, region, report):
        self.profile = profile
        self.region = region
        self.report = report

    def call(self, service_args, description):
        cmd = [
            "aws",
            "--profile", self.profile,
            "--region", self.region,
            "--output", "json",
            "--no-cli-pager",
            *service_args,
        ]
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            self.report.issue("WARN", f"{description}: {exc}")
            return None
        if proc.returncode != 0:
            message = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
            self.report.issue("WARN", f"{description}: {message}")
            return None
        if not proc.stdout.strip():
            return {}
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            self.report.issue("WARN", f"{description}: invalid JSON from AWS CLI: {exc}")
            return None


def utc_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            return from_batch_millis(value)
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        cleaned = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            return None
    return None


def as_utc(value):
    value = parse_time(value) if not isinstance(value, datetime) else value
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def from_batch_millis(value):
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc)


def age(value, now):
    value = as_utc(value)
    if value is None:
        return None
    return max(now - value, timedelta())


def fmt_age(delta):
    if delta is None:
        return "unknown"
    seconds = int(delta.total_seconds())
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def tag_value(tags, key):
    for tag in tags or []:
        if tag.get("Key") == key:
            return tag.get("Value")
    return None


def tags_text(tags):
    return " ".join(
        f"{tag.get('Key', '')}:{tag.get('Value', '')}"
        for tag in tags or []
    ).lower()


def is_camrie_instance(instance, keyword):
    text = tags_text(instance.get("Tags", []))
    return keyword.lower() in text


def is_allowed_instance(instance, args):
    instance_id = instance["InstanceId"]
    name = tag_value(instance.get("Tags", []), "Name") or ""
    if instance_id in args.allow_instance_id:
        return True
    return any(fragment.lower() in name.lower() for fragment in args.allow_name)


def print_table(headers, rows):
    if not rows:
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def get_stack_context(aws, args):
    resp = aws.call(
        ["cloudformation", "describe-stacks", "--stack-name", args.stack],
        f"Could not read CloudFormation stack {args.stack}",
    )
    stack = (resp or {}).get("Stacks", [None])[0]
    outputs = {}
    params = {}
    if not stack:
        return outputs, params
    for item in stack.get("Outputs", []):
        outputs[item["OutputKey"]] = item["OutputValue"]
    for item in stack.get("Parameters", []):
        params[item["ParameterKey"]] = item["ParameterValue"]
    return outputs, params


def discover_job_queues(aws, outputs, args):
    queues = {}
    if outputs.get("CpuJobQueueArn"):
        queues["cpu"] = outputs["CpuJobQueueArn"]
    if outputs.get("GpuJobQueueArn"):
        queues["gpu"] = outputs["GpuJobQueueArn"]
    if queues:
        return queues

    found = {}
    resp = aws.call(["batch", "describe-job-queues"], "Could not discover Batch job queues")
    for queue in (resp or {}).get("jobQueues", []):
        name = queue.get("jobQueueName", "")
        lower = name.lower()
        if args.keyword.lower() not in lower:
            continue
        if "gpu" in lower:
            found.setdefault("gpu", queue["jobQueueArn"])
        elif "cpu" in lower:
            found.setdefault("cpu", queue["jobQueueArn"])
    return found


def check_ec2(aws, args, report, now):
    report.section("EC2 Instances")
    resp = aws.call(
        [
            "ec2", "describe-instances",
            "--filters", "Name=instance-state-name,Values=running,pending",
        ],
        "Could not list EC2 instances",
    )
    if not resp:
        return

    rows = []
    running = []
    for reservation in resp.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            running.append(instance)
            tags = instance.get("Tags", [])
            launch_age = age(instance.get("LaunchTime"), now)
            rows.append([
                instance["InstanceId"],
                instance.get("InstanceType", "?"),
                instance.get("State", {}).get("Name", "?"),
                fmt_age(launch_age),
                tag_value(tags, "Name") or "-",
                tag_value(tags, "aws:batch:compute-environment") or "-",
                tag_value(tags, "aws:ecs:clusterName") or "-",
            ])

    if not running:
        report.ok("No running or pending EC2 instances.")
        return

    print_table(
        ["instance", "type", "state", "age", "name", "batch_ce", "ecs_cluster"],
        rows,
    )

    max_camrie_age = timedelta(hours=args.max_job_hours)
    max_unknown_age = timedelta(hours=args.max_unknown_hours)
    for instance in running:
        if is_allowed_instance(instance, args):
            continue
        instance_id = instance["InstanceId"]
        name = tag_value(instance.get("Tags", []), "Name") or "-"
        launch_age = age(instance.get("LaunchTime"), now)
        if is_camrie_instance(instance, args.keyword):
            if launch_age and launch_age > max_camrie_age:
                report.issue(
                    "CRITICAL",
                    f"CAMRIE EC2 instance {instance_id} ({name}) has been up for {fmt_age(launch_age)}.",
                )
        elif launch_age and launch_age > max_unknown_age:
            report.issue(
                "WARN",
                f"Unrelated EC2 instance {instance_id} ({name}) has been up for {fmt_age(launch_age)}.",
            )
        else:
            report.info(f"Unrelated EC2 instance {instance_id} ({name}) is running.")


def check_compute_environments(aws, args, report):
    report.section("Batch Compute Environments")
    resp = aws.call(
        ["batch", "describe-compute-environments"],
        "Could not list Batch compute environments",
    )
    if not resp:
        return

    rows = []
    found = []
    for env in resp.get("computeEnvironments", []):
        name = env.get("computeEnvironmentName", "")
        if args.keyword.lower() not in name.lower():
            continue
        found.append(env)
        resources = env.get("computeResources", {})
        rows.append([
            name,
            env.get("state", "?"),
            env.get("status", "?"),
            resources.get("type", "-"),
            resources.get("minvCpus", "-"),
            resources.get("desiredvCpus", "-"),
            resources.get("maxvCpus", "-"),
        ])

    if not found:
        report.issue("WARN", f"No Batch compute environments matched keyword '{args.keyword}'.")
        return

    print_table(["name", "state", "status", "type", "min", "desired", "max"], rows)
    for env in found:
        name = env.get("computeEnvironmentName", "?")
        state = env.get("state")
        status = env.get("status")
        if status != "VALID":
            report.issue("CRITICAL", f"Batch compute environment {name} is {status}.")
        elif state != "ENABLED":
            report.issue("WARN", f"Batch compute environment {name} is {state}.")


def check_batch_jobs(aws, queues, args, report, now):
    report.section("Batch Jobs")
    if not queues:
        report.issue("WARN", "No CAMRIE Batch job queues were discovered.")
        return

    active_rows = []
    max_run_age = timedelta(hours=args.max_job_hours)
    max_queue_age = timedelta(minutes=args.max_queue_minutes)

    for queue_name, queue_arn in queues.items():
        for status in ACTIVE_BATCH_STATUSES:
            resp = aws.call(
                [
                    "batch", "list-jobs",
                    "--job-queue", queue_arn,
                    "--job-status", status,
                    "--max-results", str(args.max_jobs),
                ],
                f"Could not list {status} Batch jobs for {queue_name}",
            )
            if not resp:
                continue
            for job in resp.get("jobSummaryList", []):
                created_at = from_batch_millis(job.get("createdAt"))
                started_at = from_batch_millis(job.get("startedAt"))
                basis = started_at or created_at
                job_age = age(basis, now)
                active_rows.append([
                    queue_name,
                    status,
                    job.get("jobName", "-"),
                    job.get("jobId", "-"),
                    fmt_age(job_age),
                ])
                if status in ("STARTING", "RUNNING") and job_age and job_age > max_run_age:
                    report.issue(
                        "CRITICAL",
                        f"Batch job {job.get('jobName')} on {queue_name} has been {status.lower()} for {fmt_age(job_age)}.",
                    )
                elif status in ("SUBMITTED", "PENDING", "RUNNABLE") and job_age and job_age > max_queue_age:
                    report.issue(
                        "WARN",
                        f"Batch job {job.get('jobName')} on {queue_name} has been {status.lower()} for {fmt_age(job_age)}.",
                    )

    if active_rows:
        print_table(["queue", "status", "job", "job_id", "age"], active_rows)
    else:
        report.ok("No active Batch jobs in CAMRIE queues.")

    if args.show_completed:
        completed_rows = []
        for queue_name, queue_arn in queues.items():
            for status in COMPLETED_BATCH_STATUSES:
                resp = aws.call(
                    [
                        "batch", "list-jobs",
                        "--job-queue", queue_arn,
                        "--job-status", status,
                        "--max-results", str(args.completed_limit),
                    ],
                    f"Could not list {status} Batch jobs for {queue_name}",
                )
                if not resp:
                    continue
                for job in resp.get("jobSummaryList", []):
                    stopped_at = from_batch_millis(job.get("stoppedAt"))
                    completed_rows.append([
                        queue_name,
                        status,
                        job.get("jobName", "-"),
                        job.get("jobId", "-"),
                        fmt_age(age(stopped_at, now)),
                    ])
        if completed_rows:
            print("\nRecent completed Batch jobs:")
            print_table(["queue", "status", "job", "job_id", "completed"], completed_rows)


def check_ecs_tasks(aws, args, report, now):
    report.section("ECS Tasks")
    resp = aws.call(
        [
            "ecs", "list-tasks",
            "--cluster", args.ecs_cluster,
            "--desired-status", "RUNNING",
        ],
        f"Could not list ECS tasks in cluster {args.ecs_cluster}",
    )
    if not resp:
        return
    task_arns = resp.get("taskArns", [])
    if not task_arns:
        report.ok(f"No running ECS tasks in {args.ecs_cluster}.")
        return

    desc = aws.call(
        [
            "ecs", "describe-tasks",
            "--cluster", args.ecs_cluster,
            "--tasks",
            *task_arns,
        ],
        f"Could not describe ECS tasks in cluster {args.ecs_cluster}",
    )
    if not desc:
        return

    rows = []
    max_run_age = timedelta(hours=args.max_job_hours)
    for task in desc.get("tasks", []):
        started_at = task.get("startedAt") or task.get("createdAt")
        task_age = age(started_at, now)
        task_id = task.get("taskArn", "").split("/")[-1]
        rows.append([
            task_id,
            task.get("lastStatus", "-"),
            task.get("desiredStatus", "-"),
            fmt_age(task_age),
            task.get("taskDefinitionArn", "-").split("/")[-1],
        ])
        if task_age and task_age > max_run_age:
            report.issue(
                "CRITICAL",
                f"ECS task {task_id} has been running for {fmt_age(task_age)}.",
            )

    print_table(["task", "last", "desired", "age", "task_definition"], rows)


def check_stepfunctions(aws, outputs, args, report, now):
    report.section("Step Functions")
    state_machine_arn = args.state_machine_arn or outputs.get("CalculationStateMachineArn")
    if not state_machine_arn:
        report.issue("WARN", "No CalculationStateMachineArn found; skipping Step Functions.")
        return

    resp = aws.call(
        [
            "stepfunctions", "list-executions",
            "--state-machine-arn", state_machine_arn,
            "--status-filter", "RUNNING",
            "--max-results", str(args.max_executions),
        ],
        "Could not list running Step Functions executions",
    )
    if not resp:
        return

    executions = resp.get("executions", [])
    if not executions:
        report.ok("No running Step Functions executions.")
        return

    rows = []
    max_run_age = timedelta(hours=args.max_job_hours)
    for execution in executions:
        run_age = age(execution.get("startDate"), now)
        rows.append([
            execution.get("name", "-"),
            execution.get("status", "-"),
            fmt_age(run_age),
            execution.get("executionArn", "-"),
        ])
        if run_age and run_age > max_run_age:
            report.issue(
                "CRITICAL",
                f"Step Functions execution {execution.get('name')} has been running for {fmt_age(run_age)}.",
            )

    print_table(["execution", "status", "age", "arn"], rows)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Check CAMRIE AWS resources for long-running or unexpected activity."
    )
    parser.add_argument("--profile", default="nyu", help="AWS profile to use.")
    parser.add_argument("--region", default="us-east-1", help="AWS region to inspect.")
    parser.add_argument("--stack", default="camrie-app-prod", help="SAM/CloudFormation stack name.")
    parser.add_argument("--keyword", default="camrie", help="Tag/name keyword that identifies CAMRIE resources.")
    parser.add_argument("--ecs-cluster", default=None, help="ECS cluster to inspect.")
    parser.add_argument("--state-machine-arn", default=None, help="Override Step Functions state machine ARN.")
    parser.add_argument("--max-job-hours", type=float, default=4.0, help="Warn when CAMRIE jobs/tasks run longer than this.")
    parser.add_argument("--max-queue-minutes", type=float, default=30.0, help="Warn when Batch jobs wait longer than this.")
    parser.add_argument("--max-unknown-hours", type=float, default=12.0, help="Warn when unrelated EC2 instances run longer than this.")
    parser.add_argument("--allow-instance-id", action="append", default=[], help="Ignore a known EC2 instance ID.")
    parser.add_argument("--allow-name", action="append", default=[], help="Ignore EC2 instances whose Name tag contains this text.")
    parser.add_argument("--max-jobs", type=int, default=50, help="Max active Batch jobs to read per status.")
    parser.add_argument("--max-executions", type=int, default=50, help="Max running Step Functions executions to read.")
    parser.add_argument("--show-completed", action="store_true", help="Also show recent completed Batch jobs.")
    parser.add_argument("--completed-limit", type=int, default=5, help="Completed Batch jobs to show per queue/status.")
    parser.add_argument("--fail-on-warn", action="store_true", help="Exit 2 for warnings as well as critical findings.")
    return parser.parse_args()


def main():
    args = parse_args()
    report = Report()
    now = utc_now()

    aws = AwsCli(args.profile, args.region, report)
    outputs, params = get_stack_context(aws, args)
    if not args.ecs_cluster:
        args.ecs_cluster = params.get("ECSClusterName") or f"{args.stack}-cluster"

    print(f"CAMRIE cloud safety check")
    print(f"  profile: {args.profile}")
    print(f"  region:  {args.region}")
    print(f"  stack:   {args.stack}")
    print(f"  time:    {now.isoformat(timespec='seconds')}")

    queues = discover_job_queues(aws, outputs, args)

    check_ec2(aws, args, report, now)
    check_compute_environments(aws, args, report)
    check_batch_jobs(aws, queues, args, report, now)
    check_ecs_tasks(aws, args, report, now)
    check_stepfunctions(aws, outputs, args, report, now)

    report.section("Summary")
    if report.issues:
        for item in report.issues:
            print(f"  {item.level}: {item.message}")
    else:
        report.ok("No suspicious CAMRIE activity found.")

    return report.exit_code(args.fail_on_warn)


if __name__ == "__main__":
    sys.exit(main())
