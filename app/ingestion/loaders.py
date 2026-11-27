"""
EXTRACT step: document loaders.

Each loader turns a source (file, API, ...) into a list of raw dicts. They all share one
interface, so the pipeline doesn't care where data comes from — the same idea as
LangChain / LlamaIndex "document loaders".

DISCUSSION NOTE:
  Adding a new source (e.g. a supplier's REST API, a Kafka topic, an S3 bucket) means
  writing one new class with a `load()` method. Nothing downstream changes.
"""
from __future__ import annotations

import csv
import json
from abc import ABC, abstractmethod # Abstract base class and abstract method decorator for loaders.
from pathlib import Path
from typing import Any

# Base class for all loaders. Defines the interface that all loaders must implement.
# This class cannot be instantiated directly. Subclasses must implement the `load` method.
# Example usage:
# loader = JsonCatalogLoader(Path("catalog.json"))
# products = loader.load()
# ABC: Abstract Base Class for loaders.
# ABC is a helper class used to define abstract base classes.
# abstractmethod: Decorator indicating that a method must be implemented by subclasses.
# A class inheriting from ABC cannot be instantiated unless all its abstract methods are implemented. 
class BaseLoader(ABC):
    @abstractmethod   # Must be implemented by all subclasses to load product records.
    def load(self) -> list[dict[str, Any]]:
        ...

# Base class for all catalog loaders. Defines the interface that all loaders must implement.
# Parameters:
# - path: The path to the JSON file.
# Returns:
# - A list of product records retrieved from the JSON file.
class JsonCatalogLoader(BaseLoader):
    """Loads a JSON array of product records."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"{self.path} must contain a JSON array")
        return data

# Loads products from a CSV file.
# Parameters:
# - path: The path to the CSV file.
# Returns:
# - A list of product records retrieved from the CSV file.
class CsvCatalogLoader(BaseLoader):
    """Loads a CSV export (e.g. from a PIM or a spreadsheet). List fields use '|' separators."""

    LIST_FIELDS = ("features", "reviews", "images")   # images: "a.png|b.png|c.png"

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        rows = []
        with self.path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                for field in self.LIST_FIELDS:
                    if row.get(field):
                        row[field] = [x.strip() for x in row[field].split("|")]
                rows.append(row)
        return rows

# Loads products from an HTTP REST API.
# Parameters:
# - url: The base URL of the REST API.
# - headers: Optional HTTP headers to include in requests.
# - max_pages: Maximum number of pages to fetch.
# Returns:
# - A list of product records retrieved from the API.
class HttpCatalogLoader(BaseLoader):
    """Loads products from a paginated REST API (Data Collection & APIs).

    Expects responses like {"items": [...], "next": "<url or null>"}.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None, max_pages: int = 100):
        self.url, self.headers, self.max_pages = url, headers or {}, max_pages

    def load(self) -> list[dict[str, Any]]:
        import requests  # imported lazily: only needed when this loader is used

        items, url, pages = [], self.url, 0
        while url and pages < self.max_pages:
            resp = requests.get(url, headers=self.headers, timeout=30)
            resp.raise_for_status()
            body = resp.json()
            items.extend(body.get("items", []))
            url, pages = body.get("next"), pages + 1
        return items

# Factory function to choose the appropriate loader based on the source string.
# Parameters:
# - path_or_url: The path to a local file or a URL.
# Returns:
# - An instance of the appropriate loader subclass (JsonCatalogLoader, CsvCatalogLoader, or HttpCatalogLoader).

def loader_for(path_or_url: str | Path) -> BaseLoader:
    """Factory: choose a loader from the source string."""
    s = str(path_or_url)
    if s.startswith(("http://", "https://")):
        return HttpCatalogLoader(s)
    if s.endswith(".csv"):
        return CsvCatalogLoader(Path(s))
    return JsonCatalogLoader(Path(s))
    