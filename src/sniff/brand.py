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

# 64x64 field: a teal tile and one centred, narrow mass-spectrum peak. Sized by CSS,
# so it carries no width or height of its own.
MARK_SVG = (
    '<svg class="brand" viewBox="0 0 64 64" role="img" aria-label="Sniff">'
    '<rect width="64" height="64" rx="14" fill="#1f6f6b"/>'
    '<polyline points="6,46 24,46 27,46 32,17 37,46 40,46 58,46" fill="none"'
    ' stroke="#eafaf6" stroke-width="5" stroke-linejoin="round"'
    ' stroke-linecap="round"/>'
    '</svg>'
)
