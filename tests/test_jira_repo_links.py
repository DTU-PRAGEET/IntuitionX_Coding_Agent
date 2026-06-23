"""Tests for Jira GitHub repo link extraction."""

from __future__ import annotations

from src.core.github_links import (
    extract_repo_links_from_text,
    extract_target_branch,
    github_slugs_from_text,
)

SCRUM_5_DESCRIPTION = """
h2. Security Finding

A potential SQL Injection vulnerability has been identified in the user search functionality.

----

h2. Repository Link:

[https://github.com/DTU-PRAGEET/Testing-Security-demo#|https://github.com/DTU-PRAGEET/Testing-Security-demo#|smart-link]

Branch: master

----

h2. Component

User Search
"""


def test_github_slugs_from_jira_smart_link():
    slugs = github_slugs_from_text(SCRUM_5_DESCRIPTION)
    assert slugs == ["DTU-PRAGEET/Testing-Security-demo"]


def test_extract_repo_links_does_not_split_description():
    links = extract_repo_links_from_text(SCRUM_5_DESCRIPTION)
    assert links == ["DTU-PRAGEET/Testing-Security-demo"]
    assert all("\n" not in link for link in links)
    assert all("Security Finding" not in link for link in links)


def test_extract_target_branch_master():
    assert extract_target_branch(SCRUM_5_DESCRIPTION) == "master"
