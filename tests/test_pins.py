"""Shared-workflow refs are pinned by SHA, and the template stays in step with the repo's own CI."""

import re

import pytest

from test_render import FLAGS, ROOT, render

REF = re.compile(
    r"uses:\s*JonathanTSilva/\.github/\.github/workflows/[^@\n]+?\.yml@(?P<sha>[0-9a-f]{40})\s+#\s*(?P<tag>v\d+\.\d+\.\d+)\s*$",
    re.MULTILINE,
)
ANY_SHARED_REF = re.compile(r"uses:\s*JonathanTSilva/\.github/\.github/workflows/", re.MULTILINE)
TAG_REF = re.compile(r"@v\d+\s*$")
OWN_CI = ROOT / ".github/workflows/ci.yml"
TEMPLATE_CI = ROOT / "template/.github/workflows/ci.yml.jinja"


def pins(path):
    text = path.read_text()
    pinned = {(m["sha"], m["tag"]) for m in REF.finditer(text)}
    assert len(ANY_SHARED_REF.findall(text)) == len(REF.findall(text)), (
        f"{path.name}: a shared-workflow ref is not pinned as @<sha> # vX.Y.Z"
    )
    return pinned


def test_template_pins_match_own_ci():
    own, template = pins(OWN_CI), pins(TEMPLATE_CI)
    assert len(own) == 1, own
    assert len(template) == 1, template
    assert own == template


@pytest.mark.parametrize("stack", ["python", "go", "node", "none"])
def test_rendered_ci_has_no_tag_refs(tmp_path, stack):
    render(tmp_path, stack=stack, has_release=True, **dict.fromkeys(FLAGS, True))
    lines = (tmp_path / ".github/workflows/ci.yml").read_text().splitlines()
    uses = [line for line in lines if "uses:" in line]
    assert uses
    assert not [line for line in uses if TAG_REF.search(line)]
