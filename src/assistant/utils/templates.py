CATALOGUE_ID_TEMPLATE = """### Querying datasets in the Data Catalogue

To access specific data catalogue, you need to specify the `id` parameter in your request URL.
It's a **required** parameter, here's an example of how to use the endpoint to query {subcategory} {category} data with id {id}:

```http
GET https://api.data.gov.my/data-catalogue?id={id}
```

More information about this dataset:
{description} {data_methodology} It is updated on a {update_frequency} basis. Data source from {data_sources}
{data_caveat}

To discover the available resources, visit the [Data Catalogue page](https://data.gov.my/data-catalogue) and click on a dataset page.
In the dataset page, there will be a section at the bottom titled "Sample OpenAPI query," which contains the necessary `id` for each data catalogue.
If a data catalogue is not available through the API, it will be explicitly mentioned."""
