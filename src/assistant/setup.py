from setuptools import find_packages, setup

setup(
    name="ingestion",
    packages=find_packages(exclude=["pipeline_tests"]),
    install_requires=["dagster", "dagster-cloud"],
    extras_require={"dev": ["dagster-webserver", "pytest"]},
)
