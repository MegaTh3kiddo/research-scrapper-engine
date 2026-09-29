"""Reusable core for medical literature intelligence."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("research-scrapper-engine")
except PackageNotFoundError:
    __version__ = "0+unknown"
