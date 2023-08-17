from dagster import (
    AssetIn,
    AssetOut,
    MetadataValue,
    Output,
    asset,
    multi_asset,
    get_dagster_logger,
)

import uuid
import requests
import pandas as pd
from pathlib import Path
from typing import List, Tuple
from datetime import datetime, timedelta

from config import *
from loaders import MdxLoader
from vect import WeaviateVectorDB

DOCS_MDX_RECORD_PATH = Path(settings.APP_ROOT_PATH) / "data" / "docs_mdx_record.parquet"

logger = get_dagster_logger()


def parse_github_date(date_str):
    """Convert GitHub API date format to datetime object"""
    return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")


def check_repo_changes(repo, path_to_folder, token, last_check):
    api_url = f"https://api.github.com/repos/{repo}/commits"
    headers = {"Authorization": f"token {token}"}

    # Get the list of commits
    response = requests.get(api_url, headers=headers)
    response.raise_for_status()

    commits = response.json()

    added_files = []
    modified_files = []
    removed_files = []

    for commit in commits:
        commit_date = parse_github_date(commit["commit"]["committer"]["date"])
        # If the commit was after the last check
        if commit_date > last_check:
            # Get the commit details
            commit_url = commit["url"]
            commit_response = requests.get(commit_url, headers=headers)
            commit_response.raise_for_status()
            commit_data = commit_response.json()

            # Check each file in the commit
            for file in commit_data["files"]:
                # If the file is in the specified folder and is an .mdx file
                if file["filename"].startswith(path_to_folder) and file[
                    "filename"
                ].endswith(".mdx"):
                    # Depending on the status of the file, add it to the appropriate list
                    if file["status"] == "added":
                        added_files.append(file["filename"])
                    elif file["status"] == "modified":
                        modified_files.append(file["filename"])
                    elif file["status"] == "removed":
                        removed_files.append(file["filename"])

    return added_files, modified_files, removed_files


@asset
def check_github() -> Tuple:
    """Check git for modified mdx files"""
    last_check_time = datetime.now() - timedelta(days=1)  # Check for the past 1 day
    added_files, modified_files, removed_files = check_repo_changes(
        settings.GITHUB_REPO,
        settings.GITHUB_PATH,
        settings.GITHUB_TOKEN,
        last_check_time,
    )
    logger.info(
        f"Added files: {len(added_files)}, Modified files: {len(modified_files)}, Removed files: {len(removed_files)}"
    )
    return (added_files, modified_files, removed_files)


@multi_asset(
    outs={
        "mdx_recs_to_update": AssetOut(is_required=False),
        "mdx_uuids_to_remove": AssetOut(is_required=False),
    }
)
def handle_github_checking(check_github):
    """Handle filelists from git check"""
    added_files, modified_files, removed_files = check_github

    # get records
    df_docs_rec = pd.read_parquet(DOCS_MDX_RECORD_PATH)

    recs_to_update = []
    uuids_to_remove = []
    if len(added_files) > 0:
        logger.info(f"FILES TO ADD: {len(added_files)} new files")
        mdx_loader_added = MdxLoader(added_files)
        df_mdx_added = mdx_loader_added.load()
        # add_texts
        recs_to_update.append(df_mdx_added)
    if len(modified_files) > 0:
        logger.info(f"FILES MODIFIED: {len(modified_files)} modified files")
        # if file modified, delete all headers and add new
        mdx_loader_modified = MdxLoader(modified_files)
        df_mdx_modified = mdx_loader_modified.load()

        for modified_file in modified_files:
            path = Path(modified_file)
            pages_dir = next(p for p in path.parents if p.name == "pages")
            modified_file_name = str(path.relative_to(pages_dir))[:-7]

            # find uuids to remove
            df_docs_rec_file = df_docs_rec[df_docs_rec.source == modified_file_name]
            uuids_to_remove += df_docs_rec_file.uuid.to_list()

            # records to add
            df_docs_file = df_mdx_modified[df_mdx_modified.source == modified_file_name]
            recs_to_update.append(df_docs_file)
    if len(removed_files) > 0:
        logger.info(f"FILES REMOVED: {len(removed_files)} removed files")
        # no need to load, simply grab uuid for filnames
        uuids_to_remove += df_docs_rec[df_docs_rec.source.isin(removed_files)].uuid
        # remove from record
        df_docs_rec = df_docs_rec[~df_docs_rec.source.isin(removed_files)]

    if len(recs_to_update) > 0:
        # this can be both new and to update
        df_to_update = pd.concat(recs_to_update)
        logger.info(f"Records to update: {len(recs_to_update)} records")

        # reconstruct records from unchanged and changed files
        changed_files = added_files + modified_files
        unchanged_recs = df_docs_rec[~df_docs_rec.source.isin(changed_files)]
        df_docs_rec_new = pd.concat([unchanged_recs, df_to_update])

        logger.info(f"Updating {DOCS_MDX_RECORD_PATH}")
        df_docs_rec_new.to_parquet(DOCS_MDX_RECORD_PATH)

        yield Output(df_to_update, output_name="mdx_recs_to_update")

    if len(uuids_to_remove) > 0:
        logger.info(f"Records to remove: {len(uuids_to_remove)} records")
        yield Output(uuids_to_remove, output_name="mdx_uuids_to_remove")


@asset
def mdx_vect_update(mdx_recs_to_update: pd.DataFrame) -> None:
    """Update mdx to vectorstore [Optional]"""
    mdx_fields = ["header", "source"]
    vect = WeaviateVectorDB(
        meta_fields=mdx_fields,
        instance_url=settings.WEAVIATE_URL,
        index=settings.DOCS_INDEX,
    )
    vect.update(mdx_recs_to_update)


@asset
def mdx_vect_remove(mdx_uuids_to_remove: List):
    """Remove mdx from vectorstore [Optional]"""
    mdx_fields = ["header", "source"]
    vect = WeaviateVectorDB(
        meta_fields=mdx_fields,
        instance_url=settings.WEAVIATE_URL,
        index=settings.DOCS_INDEX,
    )
    vect.remove(mdx_uuids_to_remove)

    # update records parquet file
    df_docs_rec = pd.read_parquet(DOCS_MDX_RECORD_PATH)
    df_docs_rec = df_docs_rec[~df_docs_rec.uuid.isin(mdx_uuids_to_remove)]
    logger.info(f"Updating {DOCS_MDX_RECORD_PATH}")
    df_docs_rec.to_parquet(DOCS_MDX_RECORD_PATH)


# @asset
# def load_dc_meta() -> pd.DataFrame:
#     """Check DC Metadata from S3"""
#     # placeholder
#     return pd.DataFrame()
