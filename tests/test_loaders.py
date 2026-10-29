import json

import pandas as pd

from loaders import DCMetaLoader

# Shape of the public catalogue files (DC_META_PARQUET, DC_METAFIELDS_PARQUET)
META = {
    "id": ["fuelprice", "cpi_state"],
    "data_source": ["MOF", "DOSM"],
    "data_source_primary": ["MOF", "DOSM"],
    "category_en": ["Prices", "Prices"],
    "subcategory_en": ["Fuel", "Consumer Prices"],
    "description_en": ["Weekly fuel prices", "Consumer price index by state"],
    "methodology_en": ["Set by MOF", "Survey"],
    "caveat_en": ["", "Base year 2010"],
    "exclude_openapi": [False, False],
    "frequency": ["WEEKLY", "MONTHLY"],
    "geography": ["NATIONAL", "STATE"],
    "demography": ["", ""],
    "dataset_begin": [2017, 2015],
    "dataset_end": [2017, 2024],
}
FIELDS = {
    "id": ["fuelprice", "fuelprice", "cpi_state"],
    "var_name": ["date", "ron95", "index"],
    "var_title_en": ["Date", "RON95", "Index"],
    "var_description_en": [
        "[Date] The week's start date",
        "[Float] Price of RON95 per litre",
        "[Float] CPI value",
    ],
}


def load(tmp_path):
    meta, fields = tmp_path / "meta.parquet", tmp_path / "fields.parquet"
    pd.DataFrame(META).to_parquet(meta)
    pd.DataFrame(FIELDS).to_parquet(fields)
    return DCMetaLoader([str(meta), str(fields)]).load().set_index("id")


def test_one_document_per_dataset_with_its_columns(tmp_path):
    df = load(tmp_path)
    assert sorted(df.index) == ["cpi_state", "fuelprice"]
    assert set(df.source) == {"dc_meta"}
    fuel = df.loc["fuelprice"]
    assert fuel.col_meta_clean == (
        "Date [Date] The week's start date RON95 [Float] Price of RON95 per litre"
    )
    assert [c["var_name"] for c in fuel.col_meta_dict] == ["date", "ron95"]
    assert fuel.col_meta_dict[1]["col_data_type"] == "Float"
    assert "RON95" in fuel.content_embed
    assert json.loads(fuel.header)["update_frequency"] == "weekly"


def test_date_range_uses_begin_and_end(tmp_path):
    df = load(tmp_path)
    assert df.loc["cpi_state"].date_range == "2015-2024"
    assert df.loc["fuelprice"].date_range == "2017"
