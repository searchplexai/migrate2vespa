"""Internal source implementation selection."""

from collections.abc import Callable

from .contract import Source

DEFAULT_SOURCE_ID = "elastic-family"


def _elastic_source() -> Source:
    from .elastic import ElasticSource

    return ElasticSource()


SOURCE_REGISTRY: dict[str, Callable[[], Source]] = {
    DEFAULT_SOURCE_ID: _elastic_source,
}


def get_source(source_id: str = DEFAULT_SOURCE_ID) -> Source:
    try:
        factory = SOURCE_REGISTRY[source_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported source implementation: {source_id}") from exc
    return factory()
