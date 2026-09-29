"""Explicit operator recovery, retaining committed pages and audit history."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from research_engine.infrastructure.bootstrap import create_application_services


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or recover interrupted scheduled research searches"
    )
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--list-running", action="store_true")
    operation.add_argument("--recover", metavar="EXECUTION_ID")
    operation.add_argument("--list-runs", action="store_true")
    operation.add_argument("--recover-run", metavar="RUN_ID")
    operation.add_argument("--coverage", metavar="QUERY_ID")
    operation.add_argument("--restart-coverage", nargs=3, metavar=("QUERY_ID", "PROVIDER", "LANE"))
    operation.add_argument("--retry-query", metavar="QUERY_ID")
    operation.add_argument("--resume-run", metavar="RUN_ID")
    operation.add_argument("--list-attempts", action="store_true")
    operation.add_argument("--recover-attempt", metavar="ATTEMPT_ID")
    parser.add_argument("--confirm-worker-stopped", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args(argv)
    if (args.recover or args.recover_run or args.restart_coverage or args.resume_run or args.recover_attempt) and not args.confirm_worker_stopped:
        parser.error("recovery/restart/resume requires --confirm-worker-stopped")

    try:
        services = create_application_services(args.data_dir)
    except Exception as exc:
        print(f"Cannot open research database ({type(exc).__name__})", file=sys.stderr)
        return 1
    try:
        if args.list_running:
            attempts = services.monitoring.list_running()
            for attempt in attempts:
                print(
                    f"{attempt.id}\tquery={attempt.query_id}\tstarted={attempt.started_at.isoformat()}"
                    f"\truns={','.join(attempt.search_run_ids)}"
                )
            if not attempts:
                print("No running scheduled attempts")
        elif args.recover:
            try:
                recovered = services.monitoring.recover_interrupted(
                    args.recover, confirm_worker_stopped=True,
                )
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(
                f"Recovered {recovered.id}: failed; linked provider runs "
                f"{len(recovered.search_run_ids)}"
            )
        elif args.list_runs:
            runs = services.recovery.list_running_runs()
            for run in runs:
                print(f"{run.id}\tquery={run.query_id}\tprovider={run.provider}"
                      f"\tstarted={run.started_at.isoformat()}"
                      f"\tscheduled_execution={run.scheduled_execution_id or 'manual'}")
            if not runs:
                print("No running provider runs")
        elif args.recover_run:
            services.recovery.recover_run(args.recover_run, confirm_worker_stopped=True)
            print(f"Recovered provider run {args.recover_run}; committed pages retained")
        elif args.list_attempts:
            attempts = services.recovery.list_running_attempts()
            for attempt in attempts:
                print(f"{attempt.id}\tquery={attempt.requested_query_id}"
                      f"\tprovider={attempt.requested_provider}\trun={attempt.run_id or 'pre-request'}")
            if not attempts:
                print("No running execution attempts")
        elif args.recover_attempt:
            services.recovery.recover_attempt(args.recover_attempt, confirm_worker_stopped=True)
            print(f"Recovered pre-request attempt {args.recover_attempt}")
        elif args.coverage:
            checkpoints = services.recovery.list_coverage(args.coverage)
            for checkpoint in checkpoints:
                print(f"{checkpoint.id}\tprovider={checkpoint.provider}\tlane={checkpoint.lane}\tcomplete={checkpoint.complete}")
            if not checkpoints:
                print("No active coverage checkpoints")
        elif args.restart_coverage:
            services.recovery.restart_coverage(*args.restart_coverage, confirm_worker_stopped=True)
            print("Coverage archived; next scheduled attempt will start a new campaign")
        elif args.retry_query:
            schedule = services.recovery.queue_retry(args.retry_query)
            print(f"Queued scheduled retry for {schedule.query_id}; requires an active worker")
        elif args.resume_run:
            async def resume():
                searches, providers = services.search_service()
                try:
                    return await searches.retry_run(args.resume_run)
                finally:
                    for provider in providers:
                        await provider.aclose()
            result = asyncio.run(resume())
            print(f"Retry recorded: {result}")
        return 0
    except Exception as exc:
        # Never expose credentials or raw provider error text in CLI diagnostics.
        print(f"Recovery operation failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    finally:
        services.close()


if __name__ == "__main__":
    raise SystemExit(main())
