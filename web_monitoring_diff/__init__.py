from ._version import get_versions
__version__ = get_versions()['version']
del get_versions

# Import all diff types to the top level.
from .basic_diffs import (
    compare_length,
    identical_bytes,
    side_by_side_text,
    html_text_diff,
    html_source_diff,
    html_tree_diff,
    html_differ
)

from .html_links_diff import links_diff, links_diff_json, links_diff_html
from .html_render_diff import html_diff_render
