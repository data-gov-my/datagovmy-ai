CATALOGUE_ID_TEMPLATE = """### How to Find Available Resources

To access specific data catalogue, you need to specify the `id` parameter in your request URL.
It's a **required** parameter, here's an example of how to use the endpoint to query {catalog_name} data with id {id_name}:

```http
GET https://api.data.gov.my/data-catalogue?id={id_name}
```

To discover the available resources, visit the [Data Catalogue page](https://data.gov.my/data-catalogue).
There is a section at the bottom titled "Sample OpenAPI query," which contains the necessary `id` for each data catalogue.
If a data catalogue is not available through the API, it will be explicitly mentioned. Otherwise, ask me and I will try my best to respond!"""
