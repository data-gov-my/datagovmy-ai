from langchain.text_splitter import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

import uuid
import json
import pandas as pd
from pathlib import Path
from typing import Protocol, List

from utils.helpers import *
from config import *


class BaseLoader(Protocol):
    sources: List[str]

    def load(self):
        """Load data"""

    def validate(self):
        """Validate data (needed?)"""


class MdxLoader(BaseLoader):
    """Implementation of BaseLoader for MDX files."""

    def __init__(self, sources: List[str], mdx_type: str):
        self.sources = sources
        self.mdx_type = mdx_type
        # self.additional_param = additional_param

    def load(self) -> pd.DataFrame:
        """Load and process text data from file list.

        Returns:
            dfm: DataFrame of processed text content and metadata with
                 content_embed, uuid, metadata (header, source)
        """
        raw_chunks_to_remove = ['import Head from "next/head";', r"<Head>.*?</Head>"]

        chunk_size = 1024
        chunk_overlap = 128
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        all_splitted_text = []
        for mdx_file_url in self.sources:
            markdown_input = read_file_from_repo(
                settings.GITHUB_REPO, settings.GITHUB_TOKEN, mdx_file_url
            )

            # clean raw input for certain corner cases
            for remove_pattern in raw_chunks_to_remove:
                markdown_input = re.sub(
                    remove_pattern, "", markdown_input, flags=re.DOTALL
                )

            headers_to_split_on = [
                # we're skipping H1 headers here due to clash with # comments in code blocks
                # ("#", "header"),
                ("##", "header"),
                ("###", "header"),
            ]

            markdown_splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=headers_to_split_on
            )
            md_header_splits = markdown_splitter.split_text(markdown_input)

            # add file info to metadata
            path = Path(mdx_file_url)
            if self.mdx_type == "docs":
                # docs mdx - take path after 'pages' in pages/data-catalogue/overview.en.mdx
                pages_dir = next(p for p in path.parents if p.name == "pages")
                relative_path = str(path.relative_to(pages_dir))
            elif self.mdx_type == "local":
                # local mdx - use filename?
                relative_path = path.name

            for i in range(len(md_header_splits)):
                md_header_splits[i].metadata.update({"source": relative_path})

            # char-level splits to breakdown larger mdx docs
            recursive_md_header_splits = text_splitter.split_documents(md_header_splits)
            all_splitted_text += recursive_md_header_splits

        # split data for dataframe
        df_data = []
        for doc in all_splitted_text:
            if "header" in doc.metadata:
                pass
            else:
                # handle H1 headers here
                header = extract_line_without_hash(doc.page_content)
                if header:
                    doc.metadata.update({"header": header})
            df_data.append((doc.page_content, doc.metadata, doc))

        dfm = pd.DataFrame(df_data, columns=["page_content", "metadata", "doc_object"])

        # expand metadata dict to columns
        dfm = pd.concat(
            [dfm.drop("metadata", axis=1), dfm.metadata.apply(pd.Series)], axis=1
        )
        dfm = dfm.rename(columns={"Header": "header"})

        # exclude headers and sources
        to_exclude_headers = [
            "Using the AI Helper",
            "Frequently Asked Questions",  # function def for FAQbox
        ]
        to_exclude_source = ["ai-helper.en.mdx"]
        to_exclude_header_source = [
            ("Example Request", "static-api/opendosm.en.mdx"),
            ("Request Query & Response Format", "static-api/opendosm.en.mdx"),
            ("How to Find Available Resources", "static-api/opendosm.en.mdx"),
        ]

        dfm = dfm[~dfm.header.isin(to_exclude_headers)]
        dfm = dfm[~dfm.source.isin(to_exclude_source)]
        dfm = dfm[
            dfm.apply(
                lambda row: (row["header"], row["source"])
                not in to_exclude_header_source,
                axis=1,
            )
        ]

        # strip .en.mdx from filename
        dfm.source = dfm.source.str[:-7]
        # content to embed is combo of header and page_content
        dfm["content_embed"] = dfm.apply(
            lambda row: row.header + " " + row.page_content, axis=1
        ).apply(clean_content)
        return dfm

    def validate(self):
        # Implement the validation logic for MdxLoader
        pass


class DashboardMetaLoader(BaseLoader):
    """Implementation of BaseLoader for Dashboard Metadata."""

    def __init__(self, desc_json: str, desc_parquet: str):
        self.desc_json = desc_json
        self.desc_parquet = desc_parquet

    def load(self):
        """Load and process data from two sources:
        - Dashboard i18n file for descriptions
        - Dashboard meta parquet for agency and category

        Returns:
            dfm: DataFrame of processed text content and metadata with
                 content_embed, uuid, metadata (header, source)
        """
        dfmeta_parquet = pd.read_parquet(self.desc_parquet)
        dfmeta_parquet = dfmeta_parquet.rename(columns={"name": "id"})
        # create the source column from routes
        dfmeta_parquet["source"] = dfmeta_parquet["route"]
        # create uuids for weaviate index
        dfmeta_parquet["uuid"] = [str(uuid.uuid4()) for i in range(len(dfmeta_parquet))]

        json_resp = requests.get(self.desc_json)
        dash_json = json.loads(json_resp.text)
        dfmeta_json = pd.DataFrame(dash_json["translation"]["dashboards"]).T
        dfmeta_json.index = dfmeta_json.index.set_names("id")
        dfmeta_json = dfmeta_json.reset_index()

        # combine json and parquet dataframes
        dfmeta = dfmeta_parquet.merge(dfmeta_json, on="id", suffixes=["_", ""])
        dfmeta["content_embed"] = dfmeta[["name", "category", "description"]].apply(
            lambda row: " ".join(row.values.astype(str)), axis=1
        )

        return dfmeta

    def validate(self):
        pass


class DCMetaLoader(BaseLoader):
    """Implementation of BaseLoader for Data Catalogue Metadata."""

    def __init__(self, sources: List[str]):
        self.sources = sources

    def load(self):
        """Load and process text data from metadata parquet file.

        Returns:
            dfm: DataFrame of processed text content and metadata
        """
        meta_file = self.sources[0]
        metafields_file = self.sources[1]
        dfmeta = pd.read_parquet(meta_file)
        dfmeta_fields = pd.read_parquet(metafields_file)
        dfmeta = dfmeta.merge(dfmeta_fields, on="id", suffixes=["", "_fields"])
        # dfmeta = dfmeta[~dfmeta.exclude_openapi]
        # dc_page_id is now id

        # parse column desc to extract datatype and descriptions
        dfmeta[["col_data_type", "col_description"]] = dfmeta.var_description_en.apply(
            parse_desc
        ).apply(pd.Series)

        # group column metadata in different formats
        dfmeta_byfile = dfmeta.groupby("id").first()
        dfmeta_byfile["col_meta"] = dfmeta.groupby("id").apply(
            lambda group_df: "\n".join(
                group_df.apply(
                    lambda row: row.var_title_en.strip()
                    + " "
                    + row.var_description_en.strip(),
                    axis=1,
                )
            )
        )
        dfmeta_byfile["col_meta_clean"] = dfmeta.groupby("id").apply(
            lambda group_df: " ".join(
                group_df.apply(
                    lambda row: row.var_title_en.strip()
                    + " "
                    + row.var_description_en.strip(),
                    axis=1,
                )
            )
        )
        dfmeta_byfile["col_meta_dict"] = dfmeta.groupby("id").apply(
            lambda group_df: group_df[
                ["var_name", "col_data_type", "col_description"]
            ].to_dict(orient="records")
        )

        dfmeta_numcols = [
            "dataset_begin",
            "dataset_end",
        ]
        for numcol in dfmeta_numcols:
            dfmeta_byfile[numcol] = pd.to_numeric(dfmeta_byfile[numcol])

        # convert start end to date range eg. 1955-2023
        dfmeta_byfile["date_range_int"] = (
            dfmeta_byfile["dataset_end"] - dfmeta_byfile["dataset_begin"]
        )
        dfmeta_byfile["date_range"] = dfmeta_byfile.apply(
            lambda row: (
                str(int(row["dataset_begin"]))
                if row.date_range_int == 0
                else str(int(row["dataset_begin"]))
                + "-"
                + str(int(row["dataset_begin"]))
            ),
            axis=1,
        )

        dfmeta_byfile["frequency"] = dfmeta_byfile["frequency"].str.lower()
        dfmeta_byfile.reset_index(inplace=True)

        # remove from meta
        to_drop = [
            "dataset_begin",
            "dataset_end",
            "geography",
            "demography",
            "data_source_primary",
            "date_range_int",
            "var_name",  # machine name, we only need descriptive english - subcategory
            "col_data_type",
            "col_description",
            # "exclude_openapi",
            "var_title_en",  # for columns meta
            "var_description_en",  # for columns meta
        ]

        dfmeta_byfile = dfmeta_byfile.drop(to_drop, axis=1)

        to_rename = {
            "frequency": "update_frequency",
            "methodology_en": "data_methodology",
            "caveat_en": "data_caveat",
            # "catalog_data.chart.chart_type": "chart_type",
            "category_en": "category",
            "subcategory_en": "subcategory",
            "description_en": "description",
        }
        dfmeta_byfile = dfmeta_byfile.rename(to_rename, axis=1)

        # format metadata for embeddings
        cols_to_concat = [
            "subcategory",
            "category",
            "description",
            "data_source",
            "col_meta_clean",
        ]
        dfmeta_byfile["content_embed"] = dfmeta_byfile[cols_to_concat].apply(
            lambda row: " ".join(row.values.astype(str)), axis=1
        )

        # format metadata into header for retriever
        metacols_for_header = [
            "id",
            "subcategory",
            "category",
            "description",
            "data_methodology",
            "update_frequency",
            "data_source",
            "data_caveat",
            "exclude_openapi",
            "id",
        ]
        dfmeta_byfile["header"] = dfmeta_byfile[metacols_for_header].to_dict(
            orient="records"
        )
        dfmeta_byfile["header"] = dfmeta_byfile.apply(
            lambda row: json.dumps(row.header), axis=1
        )

        # use this to identify dc_meta in retriever
        dfmeta_byfile["source"] = "dc_meta"
        return dfmeta_byfile

    def validate(self):
        # Implement the validation logic for MetaLoader
        pass
