"""The product's name and mark, in one place.

The name appears in three different worlds: the browser page and its tab, the native
window's title bar, and the installers, which additionally carry a bundle identifier
that has to agree with the one in ``packaging/sniff-app.spec``. It used to be spelled
four ways. It is spelled once here.

The mark is the same story with a twist: the artwork itself belongs to
``packaging/make_icons.py``, which also rasterises it into the ``.icns`` and ``.ico``
the installers carry. What lives here is the SVG text of that same geometry, and
``tests/test_brand.py`` fails if the two ever disagree, so the Dock icon and the page
header cannot drift apart.
"""

APP_NAME = "Sniff"
# The name says nothing about what the app is for, so the page and the window carry the
# instrument and the activity next to it.
TAGLINE = "PTR-MS review"
PAGE_TITLE = APP_NAME + " \u2014 " + TAGLINE
# Must equal CFBundleIdentifier in packaging/sniff-app.spec and IDENTIFIER in
# packaging/make_pkg.py: to LaunchServices and to the receipts database, this string is
# the product.
BUNDLE_ID = "dk.samsmart.sniff"

# 64x64 field: a teal tile, a baseline with one narrow mass-spectrum peak, and a
# deliberately small lab-coated mascot looking left towards it through a magnifying
# glass. Sized by CSS, so it carries no width or height of its own.
MARK_SVG = (
    '<svg class="brand" viewBox="0 0 64 64" role="img" aria-label="Sniff">'
    '<rect width="64" height="64" rx="14" fill="#1f6f6b"/>'
    '<polyline points="6,46 20,46 24,46 26,27 28,46 58,46" fill="none"'
    ' stroke="#eafaf6" stroke-width="3.6" stroke-linejoin="round"'
    ' stroke-linecap="round"/>'
    '<polygon points="42,29 51,29 54,34 56,46 38,46 40,34" fill="#f7fbff"/>'
    '<polygon points="44.8,29 48,34 51,29" fill="#9bd9d5"/>'
    '<polyline points="48,34 48,45" fill="none" stroke="#9bd9d5" stroke-width="1"'
    ' stroke-linejoin="round" stroke-linecap="round"/>'
    '<circle cx="49.7" cy="36.5" r="0.72" fill="#1f6f6b"/>'
    '<circle cx="49.7" cy="40" r="0.72" fill="#1f6f6b"/>'
    '<circle cx="47" cy="22" r="5.8" fill="#ffd9a8"/>'
    '<circle cx="43.6" cy="21" r="1.45" fill="#f7fbff"/>'
    '<circle cx="43" cy="21" r="0.62" fill="#1f6f6b"/>'
    '<polyline points="34,35 42,43" fill="none" stroke="#ffd9a8" stroke-width="2.2"'
    ' stroke-linejoin="round" stroke-linecap="round"/>'
    '<circle cx="30" cy="31" r="5.8" fill="#ffd9a8"/>'
    '<circle cx="30" cy="31" r="4.15" fill="#b8e8e4" fill-opacity="0.627451"/>'
    '</svg>'
)
