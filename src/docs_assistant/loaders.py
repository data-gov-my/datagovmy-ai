from langchain.document_loaders import UnstructuredMarkdownLoader
from langchain.text_splitter import MarkdownHeaderTextSplitter

from langchain import FAISS
from langchain.embeddings import OpenAIEmbeddings

import uuid
import base64
import pandas as pd
from pathlib import Path
from typing import Protocol, List

from utils import *
from config import *


class BaseLoader(Protocol):
    sources: List[str]

    def load(self):
        """Load data"""

    def validate(self):
        """Validate data (needed?)"""

def read_file_from_repo(repo, file_path, token):
    api_url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    headers = {'Authorization': f'token {token}'}
    
    # Send the request
    response = requests.get(api_url, headers=headers)
    response.raise_for_status()

    file_data = response.json()
    
    # Decode the file content from Base64
    content = base64.b64decode(file_data['content']).decode('utf-8')
    
    return content

class MdxLoader(BaseLoader):
    """Implementation of BaseLoader for MDX files."""
    def __init__(self, sources: List[str]):
        self.sources = sources
        # self.additional_param = additional_param
        
    def load(self) -> pd.DataFrame:
        """Load and process text data from file list.

        Returns: 
            dfm: DataFrame of processed text content and metadata with
                 content_embed, uuid, metadata (header, source)
        """
        all_splitted_text = []
        for mdx_file in self.sources:
            # with open(Path(settings.MDX_ROOT_PATH) / mdx_file, 'r') as file:
            #     markdown_input = file.read()
            markdown_input = read_file_from_repo(settings.GITHUB_REPO, mdx_file, settings.GITHUB_TOKEN)

            headers_to_split_on = [
                # we're skipping H1 headers here due to clash with # comments in code blocks
                # ("#", "header"), 
                ("##", "header"),
                ("###", "header"),
            ]

            markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
            md_header_splits = markdown_splitter.split_text(markdown_input)

            # add file info to metadata
            path = Path(mdx_file)
            pages_dir = next(p for p in path.parents if p.name == 'pages')
            relative_path = str(path.relative_to(pages_dir))

            for i in range(len(md_header_splits)):
                md_header_splits[i].metadata.update({'source': relative_path})
            all_splitted_text += md_header_splits

        # post process splitted text 
        df_data = []
        for doc in all_splitted_text:
            print(doc.page_content)
            if 'header' in doc.metadata:
                print('Header:', doc.metadata['header'])
            else:
                # handle H1 headers here
                header = extract_line_without_hash(doc.page_content)
                if header:
                    doc.metadata.update({'header': header})
                    print('New Header:', doc.metadata['header'])
            print('Source:', doc.metadata['source'])  
            print()
            print()    
            df_data.append((doc.page_content, doc.metadata))

        dfm = pd.DataFrame(df_data, columns=['page_content','metadata'])

        # expand metadata dict to columns
        dfm = pd.concat([dfm.drop('metadata', axis=1), dfm.metadata.apply(pd.Series)], axis=1)
        
        # drop specific headers and sources
        to_exclude_headers = [
            'Using the AI Helper',
            'Frequently Asked Questions', # function def for FAQbox
        ]
        to_exclude_source = [
            'ai-helper.en.mdx'
        ]

        dfm = dfm[~dfm.header.isin(to_exclude_headers)]
        dfm = dfm[~dfm.source.isin(to_exclude_source)]

        # remove `.en.mdx` from mdx filenames
        dfm.source = dfm.source.str[:-7]
        # create unique readable id - combo of header and source
        dfm['id'] = dfm.header + ' ' + dfm.source
        # weaviate needs uuid as id
        dfm['uuid'] = [str(uuid.uuid4()) for i in range(len(dfm))]

        # content to embed is combo of header and page_content
        dfm['content_embed'] = dfm.apply(lambda row: row.header + ' ' + row.page_content, axis=1).apply(clean_content)
        
        return dfm

    def validate(self):
        # Implement the validation logic for MdxLoader
        pass


class MetaLoader(BaseLoader):
    """Implementation of BaseLoader for DC Metadata."""
    def __init__(self, sources: List[str], additional_param):
        self.sources = sources
        self.additional_param = additional_param

    def load(self):
        """Load and process text data from file list.
        Returns: 
            dfm: DataFrame of processed text content and metadata
        """
        meta_file = self.sources[0]
        dfmeta = pd.read_csv(meta_file)
        dfmeta['grouping_col'] = dfmeta.apply(lambda row: '_'.join([row.bucket, row.file_name]), axis=1)

        # drop bm translation columns
        columns_to_drop = dfmeta.columns[(dfmeta.columns.str.contains('_bm')) | (dfmeta.columns.str.contains('.bm'))]
        dfmeta.drop(columns_to_drop, axis=1, inplace=True)

        # combine column descriptions into desc string
        grouped = dfmeta.groupby('grouping_col')
        group_desc_text = []
        group_desc_text_clean = []
        for group_name, group_indices in grouped.groups.items():
            print("Group:", group_name)
            group_df = dfmeta.loc[group_indices]
            # create richer dataset description based on title and desc
            group_str = '\n'.join(group_df[group_df.id < 0].apply(lambda row: row.title_en.strip() + ' ' + row.desc_en.strip(), axis=1))
            group_str_clean = ' '.join(group_df[group_df.id < 0].apply(lambda row: row.title_en.strip() + ' ' + row.desc_en.strip(), axis=1))
            print(group_str.strip())
            group_desc_text.append(group_str.strip())
            group_desc_text_clean.append(group_str_clean.strip())    

        # grouped dataset meta
        dfmeta_set = dfmeta[dfmeta.id >= 0].copy()
        dfmeta_set['id_openapi'] = dfmeta_set.grouping_col.str.cat('_' + dfmeta_set.id.astype(str))
        dfmeta_set['col_meta'] = group_desc_text
        dfmeta_set['col_meta_clean'] = group_desc_text_clean

        # convert start end to date range eg. 1955-2023
        dfmeta_set['date_range_int'] = dfmeta_set['catalog_data.catalog_filters.end'] - dfmeta_set['catalog_data.catalog_filters.start']
        dfmeta_set['date_range'] = dfmeta_set.apply(lambda row: str(int(row['catalog_data.catalog_filters.start']))
                        if row.date_range_int == 0 else str(int(row['catalog_data.catalog_filters.start'])) + '-' 
                        + str(int(row['catalog_data.catalog_filters.start'])), axis=1)


        dfmeta['data_sources'] = dfmeta['catalog_data.catalog_filters.data_source'].apply(parse_list_string)

        # remove from meta
        to_drop = [
            'catalog_data.catalog_filters.geographic',
            'catalog_data.catalog_filters.demographic',
            'catalog_data.chart.chart_filters.SLICE_BY',
            'catalog_data.chart.chart_filters.precision',
            'catalog_data.chart.chart_variables.parents',
            'catalog_data.chart.chart_variables.exclude',
            'catalog_data.chart.chart_variables.freeze',
            'catalog_data.catalog_filters.start',
            'catalog_data.catalog_filters.end',
            'catalog_data.catalog_filters.data_source',
            'catalog_data.metadata_lang.en.publication',
            'date_range_int',
            'id',
            'file_name',
            'name'
        ]

        dfmeta_set = dfmeta_set.drop(to_drop, axis=1)

        to_rename = {
            'catalog_data.catalog_filters.frequency': 'update_frequency',
            'catalog_data.metadata_neutral.data_as_of': 'data_as_of',
            'catalog_data.metadata_neutral.last_updated': 'data_last_updated',
            'catalog_data.metadata_neutral.next_update': 'data_next_update',
            'catalog_data.metadata_lang.en.methodology': 'data_methodology',
            'catalog_data.metadata_lang.en.caveat': 'data_caveat',
            'catalog_data.metadata_lang.en.publication': 'publications',
            'catalog_data.chart.chart_type': 'chart_type',
            'bucket': 'dataset_category',
            'title_en': 'title',
            'desc_en': 'description'
        }
        dfmeta_set = dfmeta_set.rename(to_rename,axis=1)

        # format metadata for each row for embeddings
        cols_to_concat = ['dataset_category', 'title', 'description', 'chart_type', 'data_sources', 'col_meta_clean']
        dfmeta_set['content_embed'] = dfmeta_set[cols_to_concat].apply(lambda row: ' '.join(row.values.astype(str)), axis=1)

        return dfmeta_set

    def validate(self):
        # Implement the validation logic for MetaLoader
        pass
