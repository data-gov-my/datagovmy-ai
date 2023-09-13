# datagovmy-ai

Backend repository and playground for experimental datagovmy AI/ML services:
- 👨‍💻 Open API Documentation Assistant ([See in action](https://developer.data.gov.my/))
- 📈 MyDataGPT Assistant (Coming Soon)

## Setup Dev Workspace 🚀

1. Install [pyenv](https://github.com/pyenv/pyenv#automatic-installer) and then use it to install the Python version in `.python-version`.
2. Create virtual environment in root directory with `python -m venv env`
3. Activate virtual environment.
4. Install pip-tools first with `python -m pip install pip-tools`.
5. Run `make init` to install all dependencies for this project.
6. Create your own `.env` file from `.env.example`.
7. Navigate to the application folder to run (eg. `src/assistant`) and run `uvicorn app:app --reload`
8. Interact with the chat endpoint is at `/chat`

## Setup Dev Dependencies 🛠️

This project has the following dependencies:
- [Redis](https://redis.io/docs/getting-started/installation/)
- [Weaviate](https://weaviate.io/developers/weaviate/installation)

Click on the link of the respective projects to find out how to set them up for your environment.

## Features 🔥

### Open API Documentation Assistant 👨‍💻

Built to assist developers in getting started with using the data.gov.my open API. The docs assistant is a Retrieval Augmented Generation (RAG) application powered by OpenAI's `gpt-3.5-turbo` model. Its data pipeline indexes `.mdx` files from the [API documentation](https://developer.data.gov.my/) in the [datagovmy-front](https://github.com/data-gov-my/datagovmy-front) repository and stores embeddings in a Weaviate vectorstore for retrieval.

### MyDataGPT Assistant (Coming Soon!) 📈

This assistant is part of a bigger effort to build a one-stop data assistant for the nation's open data designed to eventually answer data queries and show insights on all data released on data.gov.my. Similar to the docs assistant, it is also an RAG application that leverages a Weaviate vector index loaded with metadata. This is currently under development. Stay tuned!

### Known limitations:
- Bahasa Malaysia (BM) language support - in our testing with BM queries, GPT-3.5 tends to lean towards responses that sound more like Bahasa Indonesia despite our best efforts in prompting. YMMV, but more work to be done here!
- Full understanding of the Data Catalogue API fields (coming soon)

## Usage

This is an experimental product that utilizes the OpenAI API. It is provided for testing and educational purposes only. The government and its representatives make no warranties or guarantees regarding the accuracy, completeness, or suitability of the information provided by this product.

## Contributing

Thank you for your willingness to contribute to this free and open source project by the Malaysian public sector! When contributing, consider first discussing your desired change with the core team via GitHub issues or discussions!

## License

data.gov.my is licensed under [MIT](./LICENSE.md)

Copyright © 2023 Government of Malaysia
