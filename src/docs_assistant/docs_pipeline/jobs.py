from dagster import load_assets_from_modules, define_asset_job

from . import assets

all_assets = load_assets_from_modules([assets])

load_job = define_asset_job(name="load_docs_job", selection=all_assets)
