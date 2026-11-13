"""Render the Copier template for several answer sets and check the output."""

import datetime
import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import copier
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
VENDORED_STANDARD = ROOT / "tests/fixtures/standard.yml"
STANDARD_DIR = Path(os.environ.get("JTS_STANDARD_DIR", ROOT.parent / ".github"))
LIVE_STANDARD = STANDARD_DIR / "standard.yml"
# Without a checkout of the .github repository (CI, a fresh clone), use the vendored copy.
STANDARD = yaml.safe_load(
    (LIVE_STANDARD if LIVE_STANDARD.is_file() else VENDORED_STANDARD).read_text()
)
MARKDOWNLINT = "markdownlint-cli2@0.18.1"
STACKS = ("python", "node", "shell", "go", "rust", "c-cpp", "latex", "none")
FLAGS = ("e2e", "lighthouse", "load", "bench", "reports", "sonar")

BASE = {
    "repo_name": "sample-repo",
    "description": "A sample repository rendered from the template.",
    "kind": "product",
    "stack": "python",
    "visibility": "public",
    "license": "mit",
    "status": "active",
    "has_release": True,
    "year": 2026,
}


def render(dst: Path, **overrides) -> Path:
    copier.run_copy(
        src_path=str(ROOT),
        dst_path=dst,
        data={**BASE, **overrides},
        defaults=True,
        unsafe=True,
        vcs_ref="HEAD",
    )
    return dst


def badge_block(readme: str) -> str:
    start = readme.index("<!-- badges:start -->")
    end = readme.index("<!-- badges:end -->")
    return readme[start:end]


def badge_labels(readme: str) -> list[str]:
    labels: list[str] = []
    for line in badge_lines(readme):
        match = re.match(r"\[!\[([^\]]+)\]", line)
        assert match, line
        labels.append(match.group(1))
    return labels


def badge_lines(readme: str) -> list[str]:
    return [line for line in badge_block(readme).splitlines() if line.startswith("[![")]


def workflow(dst: Path) -> dict:
    return yaml.safe_load((dst / ".github/workflows/ci.yml").read_text())


@pytest.mark.skipif(not LIVE_STANDARD.is_file(), reason="no ../.github checkout to compare with")
def test_vendored_standard_matches_github_repo():
    assert VENDORED_STANDARD.read_text() == LIVE_STANDARD.read_text()


@pytest.mark.parametrize("stack", STACKS)
def test_root_files_do_not_leak_into_renders(tmp_path, stack):
    render(tmp_path, stack=stack)
    for name in ("copier.yml", "uv.lock", "licenses", "template", ".pytest_cache", ".venv"):
        assert not (tmp_path / name).exists(), name
    # Files that exist in both places must be the rendered version, not the root one.
    assert "Copier template" not in (tmp_path / "AGENTS.md").read_text()
    assert "template-repo" not in (tmp_path / ".devcontainer/devcontainer.json").read_text()
    assert "template-repo" not in (tmp_path / "LICENSE").read_text()
    ecosystems = {
        u["package-ecosystem"]
        for u in yaml.safe_load((tmp_path / ".github/dependabot.yml").read_text())["updates"]
    }
    assert "devcontainers" in ecosystems
    assert (tmp_path / ".copier-answers.yml").is_file()
    if stack != "python":
        assert not (tmp_path / "pyproject.toml").exists()


@pytest.mark.parametrize("stack", STACKS)
def test_required_files_present(tmp_path, stack):
    render(tmp_path, stack=stack)
    required = STANDARD["profiles"]["active"]["required_files"]
    missing = [f for f in required if not (tmp_path / f).exists()]
    assert not missing


@pytest.mark.parametrize("stack", STACKS)
@pytest.mark.parametrize("visibility", ("public", "private"))
def test_no_unrendered_markers(tmp_path, stack, visibility):
    render(tmp_path, stack=stack, visibility=visibility)
    for path in tmp_path.rglob("*"):
        if not path.is_file() or ".git" in path.relative_to(tmp_path).parts[:1]:
            continue
        # GitHub Actions expressions legitimately look like `${{ ... }}`.
        text = re.sub(r"\$\{\{.*?\}\}", "", path.read_text())
        for marker in ("{{", "{%", "<REPO>", "TODO", "lorem"):
            assert marker not in text, f"{marker!r} in {path.relative_to(tmp_path)}"


def test_claude_md_imports_agents(tmp_path):
    render(tmp_path)
    assert (tmp_path / "CLAUDE.md").read_text() == "@AGENTS.md\n"


@pytest.mark.parametrize(
    "agent,model", (("architect", "opus"), ("builder", "sonnet"), ("mechanic", "haiku"))
)
def test_agent_models(tmp_path, agent, model):
    render(tmp_path)
    text = (tmp_path / f".claude/agents/{agent}.md").read_text()
    frontmatter = yaml.safe_load(text.split("---")[1])
    assert frontmatter["name"] == agent
    assert frontmatter["model"] == model
    assert frontmatter["description"]


def test_badge_order_public(tmp_path):
    render(tmp_path, visibility="public", has_release=True)
    lines = badge_lines((tmp_path / "README.md").read_text())
    labels = badge_labels((tmp_path / "README.md").read_text())
    assert labels == ["Status", "CI", "Release", "Coverage", "Scorecard", "License", "Codespaces"]
    # Codecov serves its own badge image, so the shields style flag only applies to the others.
    assert all("style=flat-square" in line for line in lines if "codecov.io" not in line)
    assert "https://www.repostatus.org/#active" in lines[0]


def test_badge_order_public_without_release(tmp_path):
    render(tmp_path, visibility="public", has_release=False)
    labels = badge_labels((tmp_path / "README.md").read_text())
    assert labels == ["Status", "CI", "Coverage", "Scorecard", "License", "Codespaces"]


@pytest.mark.parametrize("stack", ("python", "go"))
def test_coverage_badge_and_job_for_public_stacks_with_coverage(tmp_path, stack):
    dst = render(tmp_path, stack=stack)
    readme = (dst / "README.md").read_text()
    assert (
        "[![Coverage](https://codecov.io/gh/JonathanTSilva/sample-repo/graph/badge.svg)]"
        "(https://codecov.io/gh/JonathanTSilva/sample-repo)"
    ) in readme
    jobs = workflow(dst)["jobs"]
    coverage = jobs["coverage"]
    assert coverage["uses"].endswith("workflows/coverage.yml@v1")
    assert coverage["needs"] == stack
    assert coverage["permissions"] == {"actions": "read", "contents": "read", "id-token": "write"}
    assert coverage["with"] == {
        "artifact": "go-coverage" if stack == "go" else "coverage",
        "flags": stack,
    }
    assert "coverage" in jobs["ci"]["needs"]


@pytest.mark.parametrize("stack", ("node", "shell", "rust", "c-cpp", "latex", "none"))
def test_no_coverage_without_a_coverage_artifact(tmp_path, stack):
    dst = render(tmp_path, stack=stack)
    assert "Coverage" not in badge_labels((dst / "README.md").read_text())
    assert "coverage" not in workflow(dst)["jobs"]


@pytest.mark.parametrize("stack", ("python", "go"))
def test_private_repo_has_no_coverage_or_quality(tmp_path, stack):
    dst = render(tmp_path, stack=stack, visibility="private", license="proprietary", sonar=True)
    readme = (dst / "README.md").read_text()
    assert "codecov.io" not in readme and "sonarcloud.io" not in readme
    assert "coverage" not in workflow(dst)["jobs"]


def test_sonar_adds_only_the_quality_badge(tmp_path):
    off = render(tmp_path / "off", stack="rust")
    on = render(tmp_path / "on", stack="rust", sonar=True)
    assert "Quality" not in badge_labels((off / "README.md").read_text())
    labels = badge_labels((on / "README.md").read_text())
    assert labels == ["Status", "CI", "Release", "Quality", "Scorecard", "License", "Codespaces"]
    line = next(
        x for x in badge_lines((on / "README.md").read_text()) if x.startswith("[![Quality")
    )
    assert line == (
        "[![Quality](https://sonarcloud.io/api/project_badges/measure"
        "?project=JonathanTSilva_sample-repo&metric=alert_status)]"
        "(https://sonarcloud.io/summary/new_code?id=JonathanTSilva_sample-repo)"
    )
    # Automatic Analysis: no CI job and no secret.
    assert workflow(on)["jobs"].keys() == workflow(off)["jobs"].keys()
    assert "SONAR_TOKEN" not in (on / ".github/workflows/ci.yml").read_text()


def test_badge_order_with_everything(tmp_path):
    dst = render(tmp_path, stack="go", sonar=True)
    labels = badge_labels((dst / "README.md").read_text())
    assert labels == [
        "Status",
        "CI",
        "Release",
        "Coverage",
        "Quality",
        "Scorecard",
        "License",
        "Codespaces",
    ]


def test_private_repo_static_badges_only(tmp_path):
    render(tmp_path, visibility="private", license="proprietary", has_release=True)
    block = badge_block((tmp_path / "README.md").read_text())
    images = re.findall(r"!\[[^\]]*\]\((https://[^)]+)\)", block)
    assert len(images) == 2
    assert all(url.startswith("https://img.shields.io/badge/") for url in images)
    assert "status" in images[0] and "license" in images[1]
    assert "actions/workflows" not in block  # no live CI badge on a private repo


def test_license_variants(tmp_path):
    mit = render(tmp_path / "mit", license="mit") / "LICENSE"
    text = mit.read_text()
    assert "MIT License" in text and "Jonathan Tobias" in text and "2026" in text

    proprietary = render(tmp_path / "prop", license="proprietary", visibility="private")
    assert "All rights reserved" in (proprietary / "LICENSE").read_text()

    both = (render(tmp_path / "both", license="mit+cc-by-4.0") / "LICENSE").read_text()
    assert "MIT License" in both
    assert "Creative Commons Attribution 4.0 International" in both

    cc = (render(tmp_path / "cc", license="cc-by-4.0") / "LICENSE").read_text()
    assert "MIT License" not in cc
    assert "Creative Commons Attribution 4.0 International" in cc

    agpl = (render(tmp_path / "agpl", license="agpl-3.0") / "LICENSE").read_text()
    assert "GNU AFFERO" in agpl


def test_company_license_is_not_offered(tmp_path):
    with pytest.raises(ValueError):
        render(tmp_path, license="company")


@pytest.mark.parametrize("stack", [s for s in STACKS if s != "none"])
def test_ci_calls_stack_workflow(tmp_path, stack):
    render(tmp_path, stack=stack)
    text = (tmp_path / ".github/workflows/ci.yml").read_text()
    assert f"workflows/{stack}.yml@v1" in text
    ci = workflow(tmp_path)
    jobs = ci["jobs"]
    assert stack in jobs
    assert jobs[stack]["permissions"] == {"contents": "read"}
    assert set(jobs["ci"]["needs"]) == set(jobs) - {"ci"}
    assert jobs["ci"]["if"] == "always()"
    assert jobs["ci"]["name"] == "ci"


def test_ci_without_stack_has_no_stack_job(tmp_path):
    render(tmp_path, stack="none", has_release=False)
    jobs = workflow(tmp_path)["jobs"]
    assert set(jobs) == {"hygiene", "docs", "security", "ci"}
    assert set(jobs["ci"]["needs"]) == {"hygiene", "docs", "security"}


@pytest.mark.parametrize("visibility,expected", (("public", True), ("private", None)))
def test_security_public_input(tmp_path, visibility, expected):
    render(tmp_path, visibility=visibility)
    security = workflow(tmp_path)["jobs"]["security"]
    assert security["uses"].endswith("workflows/security.yml@v1")
    assert security.get("with", {}).get("public") is expected
    assert security["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "actions": "read",
        "id-token": "write",
        "security-events": "write",
    }


def test_ci_triggers_and_permissions(tmp_path):
    render(tmp_path)
    text = (tmp_path / ".github/workflows/ci.yml").read_text()
    assert "pull_request_target" not in text
    ci = yaml.safe_load(text)
    triggers = ci[True] if True in ci else ci["on"]  # PyYAML reads the key `on` as True
    assert triggers["pull_request"]["types"] == ["opened", "edited", "synchronize", "reopened"]
    assert triggers["push"]["branches"] == ["main"]
    assert ci["permissions"] == {}
    jobs = ci["jobs"]
    assert jobs["hygiene"]["permissions"] == {"pull-requests": "read"}
    assert "pull_request" in jobs["hygiene"]["if"]
    assert jobs["docs"]["permissions"] == {"contents": "read"}
    assert jobs["python"]["permissions"] == {"contents": "read"}


def test_release_job_only_with_has_release(tmp_path):
    with_release = workflow(render(tmp_path / "yes", has_release=True))["jobs"]
    release = with_release["release"]
    assert release["uses"].endswith("workflows/release.yml@v1")
    assert release["secrets"] == {"token": "${{ secrets.RELEASE_PLEASE_TOKEN }}"}
    assert release["permissions"] == {
        "contents": "write",
        "issues": "write",
        "pull-requests": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert "push" in release["if"] and "refs/heads/main" in release["if"]
    assert "release" in with_release["ci"]["needs"]

    without = workflow(render(tmp_path / "no", has_release=False))["jobs"]
    assert "release" not in without


@pytest.mark.parametrize(
    "stack,expected",
    (
        ("python", {"uv", "github-actions", "devcontainers"}),
        ("node", {"npm", "github-actions", "devcontainers"}),
        ("shell", {"github-actions", "devcontainers"}),
        ("go", {"gomod", "github-actions", "devcontainers"}),
        ("rust", {"cargo", "github-actions", "devcontainers"}),
        ("c-cpp", {"github-actions", "devcontainers"}),
        ("latex", {"github-actions", "devcontainers"}),
        ("none", {"github-actions", "devcontainers"}),
    ),
)
def test_dependabot_ecosystems(tmp_path, stack, expected):
    render(tmp_path, stack=stack)
    config = yaml.safe_load((tmp_path / ".github/dependabot.yml").read_text())
    assert {u["package-ecosystem"] for u in config["updates"]} == expected
    for update in config["updates"]:
        assert update["schedule"]["interval"] == "weekly"
        assert update["groups"]
        assert update["commit-message"] == {"prefix": "chore(deps)"}


@pytest.mark.parametrize("stack", STACKS)
def test_structured_files_parse(tmp_path, stack):
    render(tmp_path, stack=stack)
    for name in (
        ".pre-commit-config.yaml",
        ".copier-answers.yml",
        ".github/dependabot.yml",
        ".github/workflows/ci.yml",
    ):
        yaml.safe_load((tmp_path / name).read_text())
    json.loads((tmp_path / ".devcontainer/devcontainer.json").read_text())
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    deny = settings["permissions"]["deny"]
    for rule in ("Read(./.env*)", "Bash(git push --force*)", "Bash(git push -f*)"):
        assert rule in deny
    allow = settings["permissions"]["allow"]
    for rule in (
        "Bash(git status)",
        "Bash(git diff)",
        "Bash(git diff --stat)",
        "Bash(git diff --staged)",
        "Bash(git log --oneline -20)",
    ):
        assert rule in allow


@pytest.mark.parametrize("stack", STACKS)
def test_settings_allow_no_wildcard_git(tmp_path, stack):
    render(tmp_path, stack=stack)
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    for entry in settings["permissions"]["allow"]:
        assert not (entry.startswith("Bash(git") and entry.endswith("*)")), entry


# Wildcard entries that predate the go, rust, c-cpp and latex stacks. They are the only
# ones allowed. Every other allow entry must be an exact command, because wildcards let
# an agent pass flags such as `-exec`, `-toolexec`, `-vettool`, `cmake -P` or `ctest -S`.
LEGACY_WILDCARD_ALLOW = {
    "python": {
        "Bash(uv sync*)",
        "Bash(uv run pytest*)",
        "Bash(uv run ruff*)",
        "Bash(uv run pyright*)",
    },
    "node": {
        "Bash(pnpm install*)",
        "Bash(pnpm test*)",
        "Bash(pnpm run test*)",
        "Bash(pnpm lint*)",
        "Bash(pnpm run lint*)",
        "Bash(pnpm typecheck*)",
        "Bash(pnpm run typecheck*)",
    },
    "shell": {"Bash(shellcheck*)", "Bash(shfmt*)", "Bash(bats*)"},
}


@pytest.mark.parametrize("stack", STACKS)
def test_settings_allow_wildcards_only_for_the_legacy_stacks(tmp_path, stack):
    render(tmp_path, stack=stack)
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    wildcards = {e for e in settings["permissions"]["allow"] if e.endswith("*)")}
    assert wildcards == LEGACY_WILDCARD_ALLOW.get(stack, set())


@pytest.mark.parametrize(
    "stack,expected",
    (
        ("go", {"Bash(go test ./...)", "Bash(go vet ./...)", "Bash(gofmt -l .)"}),
        (
            "rust",
            {
                "Bash(cargo test --locked)",
                "Bash(cargo build --locked)",
                "Bash(cargo fmt --all --check)",
                "Bash(cargo clippy --locked --all-targets --all-features -- -D warnings)",
            },
        ),
        (
            "c-cpp",
            {
                "Bash(cmake -S . -B build)",
                "Bash(cmake --build build)",
                "Bash(ctest --test-dir build --output-on-failure)",
            },
        ),
        (
            "latex",
            {"Bash(latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=build main.tex)"},
        ),
    ),
)
def test_new_stack_allow_rules_are_exact_commands(tmp_path, stack, expected):
    render(tmp_path, stack=stack)
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    allow = {e for e in settings["permissions"]["allow"] if not e.startswith("Bash(git")}
    assert allow == expected | {"Bash(pre-commit run --all-files)"}


@pytest.mark.parametrize("stack", STACKS)
def test_settings_allow_no_wildcard_pre_commit(tmp_path, stack):
    # A wildcard lets agents pass --config / -c and run arbitrary hooks.
    render(tmp_path, stack=stack)
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    for entry in settings["permissions"]["allow"]:
        assert not (entry.startswith("Bash(pre-commit") and entry.endswith("*)")), entry


def test_agents_md_sections(tmp_path):
    render(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text()
    for heading in ("Overview", "Setup", "Commands", "Conventions", "Boundaries", "Model routing"):
        assert f"## {heading}" in text
    for agent in ("architect", "builder", "mechanic"):
        assert f"`{agent}`" in text
    assert "https://github.com/JonathanTSilva/.github/blob/main/handbook/" in text


def test_readme_structure_order(tmp_path):
    render(tmp_path)
    text = (tmp_path / "README.md").read_text()
    assert text.startswith("<!-- brand:header:start -->\n")
    positions = [
        text.index(marker)
        for marker in (
            "<!-- brand:header:end -->",
            "<!-- badges:start -->",
            "## What it is",
            "## Quick start",
            "## Usage",
            "## Docs",
            "## Contributing",
            "## License",
            "<!-- brand:footer:start -->",
            "<!-- brand:footer:end -->",
        )
    ]
    assert positions == sorted(positions)
    assert text.endswith("<!-- brand:footer:end -->\n")


def test_readme_brand_blocks_have_placeholder_header_and_shared_footer(tmp_path):
    text = (render(tmp_path) / "README.md").read_text()
    header = text[
        text.index("<!-- brand:header:start -->") : text.index("<!-- brand:header:end -->")
    ]
    assert 'srcset="assets/brand/header-PLACEHOLDER-dark.svg"' in header
    assert 'src="assets/brand/header-PLACEHOLDER-light.svg"' in header
    assert 'alt="sample-repo: A sample repository rendered from the template."' in header
    assert "<picture>" in header and "</picture>" in header
    assert "# sample-repo" not in text
    footer = text[text.index("<!-- brand:footer:start -->") :]
    assert '<p align="right"><a href="https://www.jontobias.com">' in footer
    base = "https://raw.githubusercontent.com/JonathanTSilva/.github/main/assets/brand"
    assert f'srcset="{base}/jontobias-dark.svg"' in footer
    assert f'src="{base}/jontobias-light.svg"' in footer


def test_markdownlint_allows_the_brand_html(tmp_path):
    config = (render(tmp_path) / ".markdownlint-cli2.yaml").read_text()
    assert "MD033" in config
    for element in ("picture", "source", "img", "p", "a"):
        assert element in config


def test_answers_file_records_choices(tmp_path):
    render(tmp_path, stack="node")
    answers = yaml.safe_load((tmp_path / ".copier-answers.yml").read_text())
    assert answers["stack"] == "node" and answers["repo_name"] == "sample-repo"


def test_invalid_repo_name_rejected(tmp_path):
    with pytest.raises(ValueError):
        render(tmp_path, repo_name="Not_Valid")


def test_kind_choices_match_standard():
    questions = yaml.safe_load((ROOT / "copier.yml").read_text())
    assert list(questions["kind"]["choices"]) == [k["id"] for k in STANDARD["kinds"]]
    assert list(questions["stack"]["choices"]) == [
        "python",
        "node",
        "shell",
        "go",
        "rust",
        "c-cpp",
        "latex",
        "none",
    ]
    for flag in FLAGS:
        assert questions[flag]["type"] == "bool" and questions[flag]["default"] is False
    assert "company" not in questions["license"]["choices"]
    assert set(questions["license"]["choices"]) == set(STANDARD["licenses"]) - {"company"}


@pytest.mark.parametrize("stack", STACKS)
@pytest.mark.parametrize("visibility", ("public", "private"))
def test_rendered_workflows_lint(tmp_path, stack, visibility):
    render(tmp_path, stack=stack, visibility=visibility)
    if shutil.which("uvx") is None:
        pytest.skip("uvx not available")
    result = subprocess.run(
        [
            "uvx",
            "--from",
            "actionlint-py",
            "actionlint",
            *sorted(str(p) for p in (tmp_path / ".github/workflows").glob("*.yml")),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("stack", ("python", "go", "none"))
def test_rendered_workflows_with_all_suites_lint(tmp_path, stack):
    render(tmp_path, stack=stack, **dict.fromkeys(FLAGS, True))
    if shutil.which("uvx") is None:
        pytest.skip("uvx not available")
    result = subprocess.run(
        ["uvx", "--from", "actionlint-py", "actionlint", ".github/workflows/ci.yml"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("stack", STACKS)
def test_rendered_markdown_passes_lint_docs(tmp_path, stack):
    # The shared lint-docs workflow runs markdownlint over every .md file of a repo.
    render(tmp_path, stack=stack)
    if shutil.which("npx") is None:
        pytest.skip("npx not available")
    result = subprocess.run(
        ["npx", "--yes", MARKDOWNLINT, "**/*.md", "#node_modules"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 error(s)" in result.stdout + result.stderr


def test_markdownlint_config_mirrors_github_repo(tmp_path):
    render(tmp_path)
    config = yaml.safe_load((tmp_path / ".markdownlint-cli2.yaml").read_text())["config"]
    assert config["MD013"] is False and config["MD060"] is False and config["MD041"] is False
    assert config["MD024"] == {"siblings_only": True}


def test_pre_commit_installs_commit_msg_hook(tmp_path):
    render(tmp_path)
    config = yaml.safe_load((tmp_path / ".pre-commit-config.yaml").read_text())
    assert config["default_install_hook_types"] == ["pre-commit", "commit-msg"]


def test_agents_md_commands_match_stack(tmp_path):
    shell = (render(tmp_path / "shell", stack="shell") / "AGENTS.md").read_text()
    assert "`bats test`" in shell and "bats tests" not in shell
    node = (render(tmp_path / "node", stack="node") / "AGENTS.md").read_text()
    assert "prettier" not in node


def test_ci_keeps_blank_line_before_aggregator_job(tmp_path):
    for has_release in (True, False):
        dst = render(tmp_path / str(has_release), has_release=has_release)
        text = (dst / ".github/workflows/ci.yml").read_text()
        assert "\n\n  # The one check the repository ruleset requires" in text


def test_readme_description_once_and_neutral(tmp_path):
    text = (render(tmp_path) / "README.md").read_text()
    assert text.count("A sample repository rendered from the template.") == 1
    assert "currently marked" not in text
    assert "Usage notes live here" not in text


@pytest.mark.parametrize(
    "stack,example",
    (
        ("python", 'uv run python -c "import sample_repo"'),
        ("node", "pnpm test"),
        ("shell", "bin/sample-repo.sh --help"),
        ("go", "go test ./..."),
        ("rust", "cargo test"),
        ("c-cpp", "ctest --test-dir build --output-on-failure"),
    ),
)
def test_readme_usage_example_per_stack(tmp_path, stack, example):
    text = (render(tmp_path, stack=stack) / "README.md").read_text()
    usage = text[text.index("## Usage") : text.index("## Docs")]
    assert f"```sh\n{example}\n```" in usage


def test_readme_latex_usage_names_the_pdf(tmp_path):
    text = (render(tmp_path, stack="latex") / "README.md").read_text()
    assert "build/main.pdf" in text[text.index("## Usage") : text.index("## Docs")]


def test_readme_without_stack_has_no_usage(tmp_path):
    text = (render(tmp_path, stack="none") / "README.md").read_text()
    assert "## Usage" not in text
    assert text.index("## Quick start") < text.index("## Docs")


def test_year_defaults_to_current_year(tmp_path):
    data = {k: v for k, v in BASE.items() if k != "year"}
    copier.run_copy(
        src_path=str(ROOT), dst_path=tmp_path, data=data, defaults=True, unsafe=True, vcs_ref="HEAD"
    )
    assert (
        f"Copyright (c) {datetime.date.today().year} Jonathan Tobias"
        in (tmp_path / "LICENSE").read_text()
    )


def run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, f"{' '.join(cmd)}\n{result.stdout}\n{result.stderr}"


@pytest.mark.skeleton
def test_python_skeleton_passes_its_ci_commands(tmp_path):
    render(tmp_path, stack="python", repo_name="my-tool")
    assert (tmp_path / "src/my_tool/__init__.py").is_file()
    assert (tmp_path / "tests/test_smoke.py").is_file()
    assert (tmp_path / ".python-version").read_text().strip() == "3.13"
    for cmd in (
        ["uv", "lock"],
        ["uv", "sync", "--frozen"],
        ["uv", "run", "ruff", "check"],
        ["uv", "run", "ruff", "format", "--check"],
        ["uv", "run", "pyright"],
        ["uv", "run", "pytest", "--cov", "--cov-report=xml"],
        ["uv", "run", "pytest"],
    ):
        run(cmd, tmp_path)


@pytest.mark.skeleton
def test_shell_skeleton_passes_its_ci_commands(tmp_path):
    render(tmp_path, stack="shell", repo_name="my-tool")
    script = tmp_path / "bin/my-tool.sh"
    assert script.is_file() and os.access(script, os.X_OK)
    assert "set -euo pipefail" in script.read_text()
    assert (tmp_path / "test/my-tool.bats").is_file()
    # The same pinned tools shell.yml uses.
    run(["uvx", "--from", "shellcheck-py==0.11.0.1", "shellcheck", "bin/my-tool.sh"], tmp_path)
    run(["uvx", "--from", "shfmt-py==4.2.0", "shfmt", "-d", "bin/my-tool.sh"], tmp_path)
    run(["npx", "--yes", "bats@1.13.0", "test/"], tmp_path)
    help_run = subprocess.run([str(script), "--help"], capture_output=True, text=True)
    assert help_run.returncode == 0 and "Usage:" in help_run.stdout


@pytest.mark.skeleton
def test_node_skeleton_passes_its_ci_commands(tmp_path):
    # Takes about 10 seconds locally, so it runs in the normal suite.
    render(tmp_path, stack="node", repo_name="my-tool")
    for name in ("eslint.config.js", "tsconfig.json", "src/index.ts", "tests/index.test.ts"):
        assert (tmp_path / name).is_file(), name
    package = json.loads((tmp_path / "package.json").read_text())
    assert package["type"] == "module"
    assert re.fullmatch(r"pnpm@9\.\d+\.\d+", package["packageManager"])
    assert set(package["scripts"]) >= {"lint", "typecheck", "test"}
    assert package["scripts"]["test"] == "vitest run --coverage"
    pnpm = ["npx", "--yes", package["packageManager"]]
    run([*pnpm, "install", "--lockfile-only"], tmp_path)
    run([*pnpm, "install", "--frozen-lockfile"], tmp_path)
    for script in ("lint", "typecheck", "test"):
        run([*pnpm, "run", script], tmp_path)


def test_python_description_survives_toml_escaping(tmp_path):
    # An emoji outside the BMP must not become a JSON surrogate pair, which TOML rejects.
    description = '🧠 CLI that says "hi" and keeps a \\ backslash'
    render(tmp_path, stack="python", description=description)
    project = tomllib.loads((tmp_path / "pyproject.toml").read_text())["project"]
    assert project["description"] == description


def test_no_skeleton_without_stack(tmp_path):
    render(tmp_path, stack="none")
    for name in ("pyproject.toml", "package.json", "src", "tests", "bin", "test"):
        assert not (tmp_path / name).exists(), name


@pytest.mark.parametrize("stack", ("python", "node", "shell"))
def test_skeleton_files_only_for_their_stack(tmp_path, stack):
    render(tmp_path, stack=stack)
    assert (tmp_path / "pyproject.toml").exists() is (stack == "python")
    assert (tmp_path / "package.json").exists() is (stack == "node")
    assert (tmp_path / "bin").exists() is (stack == "shell")


@pytest.mark.parametrize(
    "stack,files",
    (
        ("python", ("pyproject.toml", "src/sample_repo/__init__.py", "tests/test_smoke.py")),
        ("node", ("package.json", "src/index.ts", "tests/index.test.ts", "tsconfig.json")),
        ("shell", ("bin/sample-repo.sh", "test/sample-repo.bats")),
    ),
)
def test_skeleton_skip_if_exists(tmp_path, stack, files):
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("custom content\n")
    render(tmp_path, stack=stack)
    for name in files:
        assert (tmp_path / name).read_text() == "custom content\n", name


@pytest.mark.parametrize("stack", ("python", "node", "shell", "none"))
def test_skeleton_false_renders_no_skeleton(tmp_path, stack):
    render(tmp_path, stack=stack, skeleton=False)
    for name in (
        "pyproject.toml",
        ".python-version",
        "package.json",
        "eslint.config.js",
        "tsconfig.json",
        "src",
        "tests",
        "bin",
        "test",
    ):
        assert not (tmp_path / name).exists(), name
    # Everything that is not skeleton is still rendered, including the stack's CI.
    for name in (".github/workflows/ci.yml", "AGENTS.md", "README.md", "LICENSE", ".gitignore"):
        assert (tmp_path / name).is_file(), name
    if stack != "none":
        assert stack in (tmp_path / ".github/workflows/ci.yml").read_text()


def test_skeleton_defaults_to_true(tmp_path):
    render(tmp_path, stack="python")
    assert (tmp_path / "pyproject.toml").is_file()
    assert yaml.safe_load((tmp_path / ".copier-answers.yml").read_text())["skeleton"] is True


def test_settings_deny_git_output_and_ext_diff(tmp_path):
    render(tmp_path, stack="python")
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    deny = settings["permissions"]["deny"]
    for entry in (
        "Bash(git diff*--output*)",
        "Bash(git log*--output*)",
        "Bash(git show*--output*)",
        "Bash(git*--ext-diff*)",
        "Bash(git -c*)",
        "Bash(git*--o*)",
        "Bash(git*--ext*)",
        "Bash(git*--no-index*)",
    ):
        assert entry in deny, entry


@pytest.mark.parametrize("stack", [s for s in STACKS if s != "none"])
def test_stack_tooling_is_rendered(tmp_path, stack):
    dst = render(tmp_path, stack=stack)
    agents = (dst / "AGENTS.md").read_text()
    assert "no language stack" not in agents and "no test command" not in agents
    settings = json.loads((dst / ".claude/settings.json").read_text())
    stack_rules = [r for r in settings["permissions"]["allow"] if not r.startswith("Bash(git")]
    assert len(stack_rules) > 1  # the stack's commands, beside pre-commit
    config = (dst / ".pre-commit-config.yaml").read_text()
    if stack in ("go", "rust"):
        assert "repo: local" in config


@pytest.mark.parametrize(
    "stack,files",
    (
        ("go", ["go.mod", "sum.go", "sum_test.go"]),
        ("rust", ["Cargo.toml", "Cargo.lock", "src/lib.rs"]),
        ("c-cpp", ["CMakeLists.txt", "src/sum.cpp", "src/sum.hpp", "tests/sum_test.cpp"]),
        ("latex", ["main.tex"]),
    ),
)
def test_new_stack_skeleton_files(tmp_path, stack, files):
    with_skeleton = render(tmp_path / "yes", stack=stack)
    for name in files:
        assert (with_skeleton / name).is_file(), name
    without = render(tmp_path / "no", stack=stack, skeleton=False)
    for name in files:
        assert not (without / name).exists(), name


@pytest.mark.parametrize("stack", ("go", "rust", "c-cpp", "latex"))
def test_new_stack_skeleton_never_overwrites_existing_files(tmp_path, stack):
    first = render(tmp_path / "a", stack=stack)
    skeleton = [
        p.relative_to(first)
        for p in first.rglob("*")
        if p.is_file()
        and p.name
        in {"go.mod", "sum.go", "sum_test.go", "Cargo.toml", "Cargo.lock", "lib.rs"}
        | {"CMakeLists.txt", "sum.cpp", "sum.hpp", "sum_test.cpp", "main.tex"}
    ]
    assert skeleton
    existing = tmp_path / "b"
    for rel in skeleton:
        (existing / rel).parent.mkdir(parents=True, exist_ok=True)
        (existing / rel).write_text("real code\n")
    render(existing, stack=stack)
    for rel in skeleton:
        assert (existing / rel).read_text() == "real code\n", rel


def test_default_answers_add_no_suite_jobs(tmp_path):
    # An existing repo updating to this version keeps its ci.yml jobs: every new flag is off.
    jobs = workflow(render(tmp_path, stack="node", has_release=False))["jobs"]
    assert set(jobs) == {"hygiene", "docs", "security", "node", "ci"}


@pytest.mark.parametrize("flag", ("e2e", "lighthouse", "load", "bench", "reports"))
def test_suite_flag_adds_its_job(tmp_path, flag):
    jobs = workflow(render(tmp_path, stack="python", has_release=False, **{flag: True}))["jobs"]
    job = jobs[flag]
    assert job["uses"].endswith(f"workflows/{flag}.yml@v1")
    assert job["permissions"] == {"contents": "write" if flag in ("bench", "reports") else "read"}
    assert flag in jobs["ci"]["needs"]
    assert set(jobs["ci"]["needs"]) == set(jobs) - {"ci"}


@pytest.mark.parametrize(
    "stack,tool", (("go", "go"), ("rust", "cargo"), ("python", "pytest"), ("shell", None))
)
def test_bench_job_inputs_match_the_stack(tmp_path, stack, tool):
    bench = workflow(render(tmp_path, stack=stack, bench=True))["jobs"]["bench"]
    assert bench["with"]["command"]
    assert bench["with"]["tool"] == (tool or "customSmallerIsBetter")


def test_go_package_name_is_valid_for_digit_names(tmp_path):
    dst = render(tmp_path, stack="go", repo_name="3d-viewer")
    assert "package pkg3dviewer\n" in (dst / "sum.go").read_text()
    assert "module github.com/JonathanTSilva/3d-viewer\n" in (dst / "go.mod").read_text()


def _have(*tools: str) -> bool:
    return all(shutil.which(t) for t in tools)


@pytest.mark.skeleton
@pytest.mark.skipif(not _have("go", "gofmt"), reason="go toolchain not installed")
def test_go_skeleton_passes_its_ci_commands(tmp_path):
    render(tmp_path, stack="go", repo_name="my-tool")
    out = subprocess.run(["gofmt", "-l", "."], cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout == "", out.stdout
    run(["go", "vet", "./..."], tmp_path)
    run(["go", "test", "-race", "-coverprofile=coverage.out", "./..."], tmp_path)
    assert (tmp_path / "coverage.out").is_file()


@pytest.mark.skeleton
@pytest.mark.skipif(not _have("cargo"), reason="rust toolchain not installed")
def test_rust_skeleton_passes_its_ci_commands(tmp_path):
    render(tmp_path, stack="rust", repo_name="my-tool")
    lock = (tmp_path / "Cargo.lock").read_text()
    for cmd in (
        ["cargo", "fmt", "--all", "--check"],
        ["cargo", "clippy", "--locked", "--all-targets", "--all-features", "--", "-D", "warnings"],
        ["cargo", "test", "--locked", "--all-features"],
    ):
        run(cmd, tmp_path)
    assert (tmp_path / "Cargo.lock").read_text() == lock  # --locked never rewrote it


@pytest.mark.skeleton
@pytest.mark.skipif(not _have("cmake", "ctest", "clang-tidy", "jq"), reason="C++ toolchain missing")
def test_c_cpp_skeleton_passes_its_ci_commands(tmp_path):
    render(tmp_path, stack="c-cpp", repo_name="my-tool")
    run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ],
        tmp_path,
    )
    run(["cmake", "--build", "build", "--parallel"], tmp_path)
    run(["ctest", "--test-dir", "build", "--output-on-failure"], tmp_path)
    # the same file list and checks as the shared c-cpp workflow
    files = subprocess.run(
        ["jq", "-r", ".[].file", "build/compile_commands.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    ).stdout.split()
    sources = sorted({f for f in files if "/build/" not in f})
    assert sources
    run(
        [
            "clang-tidy",
            "-p",
            "build",
            "--warnings-as-errors=*",
            "--checks=clang-analyzer-*,bugprone-*,performance-*",
            *sources,
        ],
        tmp_path,
    )
    if shutil.which("cppcheck"):
        run(
            [
                "cppcheck",
                "--project=build/compile_commands.json",
                "--enable=warning,style,performance,portability",
                "--suppress=missingIncludeSystem",
                "--inline-suppr",
                "--error-exitcode=1",
                "--quiet",
            ],
            tmp_path,
        )


@pytest.mark.skeleton
@pytest.mark.skipif(not _have("latexmk", "pdflatex"), reason="LaTeX toolchain not installed")
def test_latex_skeleton_passes_its_ci_commands(tmp_path):
    render(tmp_path, stack="latex", repo_name="my-tool")
    run(
        [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            "-outdir=build",
            "main.tex",
        ],
        tmp_path,
    )
    assert (tmp_path / "build/main.pdf").is_file()
