# datagovmy-ai

Backend for datagovmy AI/ML services:
- Open API Documentation Assistant
- MyDataGPT Assistant (coming soon)

## Setup Dev Workspace

1. Install [pyenv](https://github.com/pyenv/pyenv#automatic-installer) and then use it to install the Python version in `.python-version`.
2. Create virtual environment in root directory with `python -m venv env`
3. Activate virtual environment.
4. Install pip-tools first with `python -m pip install pip-tools`.
5. Run `make init` to install all dependencies for this project.
6. Create your own `.env` file from `.env.example`.
7. Navigate to the application folder to run (eg. `src/assistant`) and run `uvicorn app:app --reload`

## Setup Dev Dependencies

This project has the following dependencies:
- [Redis](https://redis.io/docs/getting-started/installation/)
- [Weaviate](https://weaviate.io/developers/weaviate/installation)

Click on the link of the respective projects to find out how to set them up for your environment.

## Setup Retrieval Pipeline

The documentation assistant project indexes `.mdx` files from the [datagovmy-front](https://github.com/data-gov-my/datagovmy-front) repository.
