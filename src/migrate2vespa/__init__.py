"""Offline-first Elasticsearch/OpenSearch to Vespa migration tooling."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("migrate2vespa")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
