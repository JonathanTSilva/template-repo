"""The OpenSSF Best Practices answers file and the README badge."""

import json
import re
from pathlib import Path

import pytest

from test_render import render

FILE = ".bestpractices.json"
ALLOWED = {"Met", "Unmet", "N/A", "?"}
KEYS = (Path(__file__).parent / "fixtures/bestpractices_passing_keys.txt").read_text().split()
ATTESTATIONS = ("know_secure_design", "know_common_errors")
KNOWN_STACKS = ("python", "go", "node", "shell")
OTHER_STACKS = ("rust", "c-cpp", "latex", "none")
REPO = "https://github.com/JonathanTSilva/sample-repo"


def load(dst: Path) -> dict:
    return json.loads((dst / FILE).read_text())


def test_fixture_lists_the_67_passing_criteria():
    assert len(KEYS) == 67
    assert len(set(KEYS)) == 67


@pytest.mark.parametrize("stack", KNOWN_STACKS + OTHER_STACKS)
def test_answers_are_complete_and_valid(tmp_path, stack):
    data = load(render(tmp_path, stack=stack))
    for key in KEYS:
        assert data[f"{key}_status"] in ALLOWED, key
        justification = data[f"{key}_justification"]
        assert justification and re.search(r" https://\S+$", justification), key
    # No stray keys: project fields plus status and justification per criterion.
    fields = {"name", "description", "repo_url", "homepage_url", "license"}
    fields |= {"implementation_languages"}
    assert set(data) == fields | {f"{k}_{s}" for k in KEYS for s in ("status", "justification")}


@pytest.mark.parametrize("stack", KNOWN_STACKS + OTHER_STACKS)
def test_attestations_are_left_to_the_owner(tmp_path, stack):
    data = load(render(tmp_path, stack=stack))
    for key in ATTESTATIONS:
        assert data[f"{key}_status"] == "?"
        assert data[f"{key}_justification"].startswith("OWNER ATTESTATION")


def test_project_fields(tmp_path):
    data = load(render(tmp_path, description='Says "hi" & more', has_release=False))
    assert data["name"] == "sample-repo"
    assert data["description"] == 'Says "hi" & more'
    assert data["repo_url"] == data["homepage_url"] == REPO
    assert data["license"] == "MIT"
    assert data["implementation_languages"] == "Python"
    assert data["version_unique_status"] == "?"
    assert data["version_unique_justification"].startswith("release-please is off")


@pytest.mark.parametrize(
    ("license_answer", "spdx"),
    [("mit", "MIT"), ("mit+cc-by-4.0", "MIT AND CC-BY-4.0"), ("cc-by-4.0", "CC-BY-4.0")],
)
def test_license_mapping(tmp_path, license_answer, spdx):
    data = load(render(tmp_path, license=license_answer))
    assert data["license"] == spdx
    assert data["floss_license_status"] == "Met"


@pytest.mark.parametrize(
    ("stack", "test_text", "warnings_text", "static_text"),
    [
        ("python", "uv run pytest", "ruff", "pyright"),
        ("go", "go test", "go vet", "go vet"),
        ("node", "pnpm run test", "eslint", "eslint"),
        ("shell", "bats", "shellcheck", "shellcheck"),
    ],
)
def test_known_stacks_get_stack_wording(tmp_path, stack, test_text, warnings_text, static_text):
    data = load(render(tmp_path, stack=stack))
    assert test_text in data["test_justification"]
    assert warnings_text in data["warnings_justification"]
    assert static_text in data["static_analysis_justification"]
    for key in ("test", "warnings", "static_analysis", "crypto_published"):
        assert data[f"{key}_status"] in {"Met", "N/A"}


@pytest.mark.parametrize("stack", OTHER_STACKS)
def test_other_stacks_leave_stack_rows_unknown(tmp_path, stack):
    data = load(render(tmp_path, stack=stack))
    stack_rows = (
        "build",
        "build_common_tools",
        "build_floss_tools",
        "test",
        "test_invocation",
        "warnings",
        "warnings_fixed",
        "static_analysis",
        "static_analysis_often",
        "documentation_basics",
        "dynamic_analysis_unsafe",
        "dynamic_analysis_enable_assertions",
        "crypto_published",
        "crypto_random",
    )
    for key in stack_rows:
        assert data[f"{key}_status"] == "?", key


def test_build_rows_per_stack(tmp_path):
    expected = {"python": "Met", "go": "Met", "shell": "N/A", "node": "?"}
    for stack, status in expected.items():
        data = load(render(tmp_path / stack, stack=stack))
        assert data["build_status"] == status, stack
    no_skeleton = load(render(tmp_path / "bare", stack="python", skeleton=False))
    assert no_skeleton["build_status"] == "?"


def test_unprovable_rows_are_unknown_everywhere(tmp_path):
    data = load(render(tmp_path))
    for key in (
        "documentation_interface",
        "maintained",
        "test_most",
        "tests_are_added",
        "report_responses",
        "vulnerabilities_fixed_60_days",
        "static_analysis_fixed",
        "dynamic_analysis",
        "dynamic_analysis_fixed",
        "warnings_strict",
    ):
        assert data[f"{key}_status"] == "?", key


@pytest.mark.parametrize(
    "overrides",
    [
        {"visibility": "private", "license": "proprietary"},
        {"status": "inactive"},
        {"status": "concept"},
    ],
)
def test_no_file_for_private_or_inactive(tmp_path, overrides):
    dst = render(tmp_path, **overrides)
    assert not (dst / FILE).exists()
    assert "bestpractices" not in (dst / "AGENTS.md").read_text()


@pytest.mark.parametrize("status", ("wip", "active"))
def test_file_and_agents_line_for_public_wip_and_active(tmp_path, status):
    dst = render(tmp_path, status=status)
    assert (dst / FILE).is_file()
    assert "answers live in `.bestpractices.json`" in (dst / "AGENTS.md").read_text()


def test_file_ends_with_one_newline(tmp_path):
    text = (render(tmp_path) / FILE).read_text()
    assert text.endswith("}\n") and not text.endswith("\n\n")


def test_badge_present_iff_id_is_set(tmp_path):
    off = (render(tmp_path / "off") / "README.md").read_text()
    on = (render(tmp_path / "on", bestpractices_id=12345) / "README.md").read_text()
    assert "bestpractices.dev" not in off
    assert (
        "[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/12345/badge)]"
        "(https://www.bestpractices.dev/projects/12345)"
    ) in on


def test_badge_sits_between_scorecard_and_license(tmp_path):
    readme = (render(tmp_path, bestpractices_id=7) / "README.md").read_text()
    labels = re.findall(r"^\[!\[([^\]]+)\]", readme.split("<!-- badges:end -->")[0], re.M)
    assert labels == [
        "Status",
        "CI",
        "Release",
        "Coverage",
        "Scorecard",
        "OpenSSF Best Practices",
        "License",
        "Codespaces",
    ]


def test_private_repo_never_gets_the_badge(tmp_path):
    dst = render(tmp_path, visibility="private", license="proprietary", bestpractices_id=7)
    assert "bestpractices.dev" not in (dst / "README.md").read_text()
