"""Checks that the public package version stays aligned with its distribution."""

from importlib.metadata import PackageNotFoundError, version

import pytest

import research_engine


def test_module_version_matches_installed_distribution_metadata():
    try:
        installed_version = version("research-scrapper-engine")
    except PackageNotFoundError:
        pytest.skip("distribution metadata is available only after package installation")

    assert research_engine.__version__ == installed_version
