from dagster import Definitions, ScheduleDefinition, load_assets_from_modules

from . import assets
from .sensors import notify_on_run_failure, notify_on_run_success
from .jobs import load_job

all_assets = load_assets_from_modules([assets])
all_sensors = [notify_on_run_failure, notify_on_run_success]

basic_schedule = ScheduleDefinition(job=load_job, cron_schedule="0 * * * *")

defs = Definitions(
    assets=all_assets, sensors=all_sensors, jobs=[load_job], schedules=[basic_schedule]
)
