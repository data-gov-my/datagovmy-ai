# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2023-09-15

### Added

- Added Data Catalogue metadata into the vector index to support dataset searching and `catalogue_id` discovery.
- Added a custom document retriever that handles possible dataset queries by injecting `catalogue_id` into a API guide template.
- Added this changelog file!

### Fixed

- Improve prompt to decline dataset requests that doesn't exist to hopefully reduce hallucinations of `catalogue_id`s that don't exist

## [1.0.0] - 2019-09-13

### Added
First public launch and release!
