from dagster import (
    run_failure_sensor,
    run_status_sensor,
    RunFailureSensorContext,
    DagsterRunStatus,
    JobSelector
)

from utils import *

@run_failure_sensor
def notify_on_run_failure(context: RunFailureSensorContext) -> None:
    message = f'Job "{context.dagster_run.job_name}" failed. Error: {context.failure_event.message}'
    send_telegram(message)

@run_status_sensor(
    monitored_jobs=[
        JobSelector(
            location_name="defs",
            job_name="load_job",
        )
    ],
    run_status=DagsterRunStatus.SUCCESS,
)
def notify_on_run_success() -> None:
    send_telegram('Job successful')