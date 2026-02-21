from .. import basic_diffs, html_render_diff, html_links_diff

# Map tokens in the REST API to functions in modules.
# The modules do not have to be part of the web_monitoring_diff package.
DIFF_ROUTES = {
    "length": basic_diffs.compare_length,
    "identical_bytes": basic_diffs.identical_bytes,
    "side_by_side_text": basic_diffs.side_by_side_text,
    "links": html_links_diff.links_diff_html,
    "links_json": html_links_diff.links_diff_json,
    # applying diff-match-patch (dmp) to strings (no tokenization)
    "html_text_dmp": basic_diffs.html_text_diff,
    "html_source_dmp": basic_diffs.html_source_diff,
    "html_token": html_render_diff.html_diff_render,
}

# Optional, experimental diffs.
try:
    from ..experimental import htmltreediff
    DIFF_ROUTES["html_tree"] = htmltreediff.diff
except ModuleNotFoundError:
    ...

try:
    from ..experimental import htmldiffer
    DIFF_ROUTES["html_perma_cc"] = htmldiffer.diff
except ModuleNotFoundError:
    ...
