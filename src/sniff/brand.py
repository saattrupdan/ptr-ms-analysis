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

# 64x64 field: a teal tile, a baseline with one narrow mass-spectrum peak, and
# a warm side-profile nose with a nostril and a small breath detail. Sized by CSS,
# so it carries no width or height of its own.
MARK_SVG = (
    '<svg class="brand" viewBox="0 0 64 64" role="img" aria-label="Sniff">'
    '<rect width="64" height="64" rx="14" fill="#1f6f6b"/>'
    '<polyline points="6,46 20,46 24,46 26,27 28,46 58,46" fill="none"'
    ' stroke="#eafaf6" stroke-width="3.6" stroke-linejoin="round"'
    ' stroke-linecap="round"/>'
    '<polygon points="20.5,23 20.3,21 21,18.8 22.5,16.8 24.5,15.5 27,14.8'
    ' 29.2,15.2 31,16.5 31.6,18 30.7,19.1 28.8,19.5 27.3,20.8'
    ' 25.3,21.8 23,22.5 20.5,23" fill="#ffd9a8"/>'
    '<circle cx="28.9" cy="18.1" r="1.05" fill="#1f6f6b"/>'
    '<polyline points="32.5,14.5 34.8,13 37,13.8 38.5,15.8" fill="none"'
    ' stroke="#ffd9a8" stroke-width="1.8" stroke-linejoin="round"'
    ' stroke-linecap="round"/>'
    '</svg>'
)
