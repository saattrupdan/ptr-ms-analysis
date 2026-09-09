"""The name and mark are stated once; these tests keep that promise.

Every check here compares the package against a place the brand is repeated:
the installer authoring, the PyInstaller spec, and the icon generator. Those
cannot import each other, so the agreement is asserted instead.
"""

import importlib.util
import pathlib
import re
import sys

from sniff import app, brand, viz

REPO = pathlib.Path(__file__).resolve().parent.parent


def _load(name, relpath):
    """Import a packaging module by path; they are not part of the package."""
    path = REPO / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_installers_call_it_the_same_thing():
    msi = _load("make_msi", "packaging/make_msi.py")
    pkg = _load("make_pkg", "packaging/make_pkg.py")
    assert msi.APP_NAME == pkg.APP_NAME == brand.APP_NAME
    # The MSI keys its components from a namespace string; changing it would look
    # like every file being replaced on the next upgrade.
    assert msi.UPGRADE_CODE == "8f0c2f4c-6e1b-5a0d-9e2f-4b7c1a3d6e85"
    assert msi.URL == "https://github.com/saattrupdan/sniff"
    assert pkg.IDENTIFIER == brand.BUNDLE_ID


def test_the_bundle_and_the_package_share_one_identifier():
    spec = (REPO / "packaging" / "sniff-app.spec").read_text(encoding="utf-8")
    assert re.search(r'^BUNDLE_ID = "%s"$' % re.escape(brand.BUNDLE_ID), spec, re.M), (
        "the bundle identifier in the spec and in brand.py are two answers to the same"
        " question, and LaunchServices will believe the second one"
    )
    assert re.search(r'^APP_NAME = "%s"$' % re.escape(brand.APP_NAME), spec, re.M)
    assert 'contents_directory="_internal"' in spec


def test_the_page_mark_and_the_icon_are_the_same_drawing():
    icons = _load("make_icons", "packaging/make_icons.py")
    svg = brand.MARK_SVG
    points = " ".join("%g,%g" % pair for pair in icons.TRACE)

    assert 'points="%s"' % points in svg
    assert 'stroke-width="%g"' % icons.TRACE_WIDTH in svg
    assert re.search(r'rx="([\d.]+)"', svg).group(1) == "%g" % icons.RADIUS

    generated = icons.svg_text()
    artwork = (REPO / "gfx" / "sniff.svg").read_text(encoding="utf-8")
    assert generated == artwork
    assert 'points="%s"' % points in generated


def test_the_brand_contains_no_character_geometry():
    icons = _load("make_icons", "packaging/make_icons.py")
    for name in ("NOSE", "NOSTRIL", "BREATH", "MASCOT_BODY", "MAGNIFIER_LENS"):
        assert not hasattr(icons, name)
    for word in ("nostril", "breath", "mascot", "magnifier", "lab-coat"):
        assert word not in brand.MARK_SVG.lower()


def test_the_icon_uses_the_platform_safe_area():
    icons = _load("make_icons", "packaging/make_icons.py")
    for size in (16, 32, 64, 128, 256):
        buf = icons.render(size)
        opaque = sum(1 for i in range(3, len(buf), 4) if buf[i] > 200)
        assert 0.6 < opaque / (size * size) < 0.7, (
            "the tile is mis-sized at %d px" % size
        )

        edge_points = (
            (size // 2, 0),
            (size - 1, size // 2),
            (size // 2, size - 1),
            (0, size // 2),
        )
        for x, y in edge_points:
            alpha = buf[(y * size + x) * 4 + 3]
            assert alpha == 0, "the icon needs transparent padding at %d px" % size

    for size in (16, 32, 64):
        buf = icons.render(size)
        pixels = (tuple(buf[i : i + 4]) for i in range(0, len(buf), 4))
        assert any(r > 180 and g > 220 and b > 220 and a > 100 for r, g, b, a in pixels)


def test_both_pages_carry_the_mark_and_the_name():
    page = viz.render_html({"file": "x.h5", "peaks": [], "ranges": [], "meta": {}})
    for needle, what in (
        (brand.APP_NAME, "the product name"),
        ('class="brand"', "the mark"),
        ("PTR-MS review", "what the app is for"),
    ):
        assert needle in page, "%s is missing from the review page" % what
    assert "__APP_NAME__" not in page and "__PAGE_TITLE__" not in page
    assert brand.APP_NAME in app._START_HTML
    assert "__MARK__" not in app._START_HTML and "__TAGLINE__" not in app._START_HTML


def test_the_version_is_stated_once():
    """A second version string is a second place to be wrong.

    ``__init__.__version__`` used to be a literal, and it read 0.1.0 across five
    releases while the installers shipped 0.4.0 — the kind of wrong that only shows
    up when someone asks what version they have installed.
    """
    from importlib.metadata import version

    import sniff

    assert sniff.__version__ == version("sniff")
    spec = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([\d.]+[^"\n]*)"$', spec, re.M).group(1)
    assert version("sniff").split("+")[0] == declared


def test_agent_contracts_are_not_still_in_a_retired_skill_file():
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert not (REPO / "SKILL.md").exists()
    assert "Read `SKILL.md`" not in agents
    for contract in (
        "CLI boundary",
        "Review ownership",
        "Peak scope",
        "Blank files",
        "Background diagnostic",
        "Range labels",
        "Concentration communication",
        "Identification limits",
    ):
        assert contract in agents


def test_no_page_still_calls_it_the_old_name():
    page = viz.render_html({"file": "x.h5", "peaks": [], "ranges": [], "meta": {}})
    for body in (page, app._START_HTML):
        assert "PTR-MS Review" not in body, "the old product name is still on a page"
