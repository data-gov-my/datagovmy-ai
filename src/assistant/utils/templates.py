CATALOGUE_ID_TEMPLATE = """### Querying {subcategory} {category} data from the Data Catalogue API

To query specifc data from the Data Catalgoue API, you need to specify the `id` parameter in your request URL.
It's a **required** parameter, here's an example of how to use the endpoint to query {subcategory} {category} data with id {id}:

```http
GET https://api.data.gov.my/data-catalogue?id={id}
```

More information about this dataset:
Dataset page: https://data.gov.my/data-catalogue/{id}
{description} {data_methodology} It is updated on a {update_frequency} basis.
{data_caveat}

To discover all other available datasets, visit the [Data Catalogue page](https://data.gov.my/data-catalogue)."""

CATALOGUE_ID_NOAPI_TEMPLATE = """### {subcategory} {category} dataset

More information about this dataset:
Dataset page: https://data.gov.my/data-catalogue/{id}
{description} {data_methodology} It is updated on a {update_frequency} basis.
{data_caveat}

Note that this data catalogue is not available through OpenAPI as the nature of the data makes it unsuitable for API access. However, you can access the full dataset through the provided download link in the dataset page.

To discover all other available datasets, visit the [Data Catalogue page](https://data.gov.my/data-catalogue)."""
