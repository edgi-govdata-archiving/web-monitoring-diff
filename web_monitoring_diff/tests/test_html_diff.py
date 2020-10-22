from datetime import datetime
import functools
import os
from pathlib import Path
from pkg_resources import resource_filename
import pytest
from web_monitoring_diff import html_tree_diff, html_differ
from web_monitoring_diff.html_render_diff import html_diff_render


def lookup_pair(fn):
    """Read example data named {fn}.before and {fn}.after"""
    fn1 = 'example_data/{}.before'.format(fn)
    fn2 = 'example_data/{}.after'.format(fn)
    with open(resource_filename('web_monitoring_diff', fn1)) as f:
        before = f.read()
    with open(resource_filename('web_monitoring_diff', fn2)) as f:
        after = f.read()
    return before, after


TEMPLATE = """
BEFORE:
{}
AFTER:
{}
DIFF:
{}
"""

cases = ['change-tag',
         'change-href',
         'change-link-text',
         'ins-in-source',
         'two-paragraphs',
         'add-paragraph',
         'change-word-in-paragraph',
         'change-title',
         'add-list',
         'remove-list',
         'change-list-href',
         'change-list-text',
        ]


OUTPUT_DIR = Path('test_output') / 'diff_output_{}'.format(datetime.now().isoformat())
os.makedirs(OUTPUT_DIR, exist_ok=True)


def export(func):
    @functools.wraps(func)
    def inner(**kwargs):
        d = func(**kwargs)
        filename = func.__name__ + '-' + '-'.join(kwargs.values()) + '.html'
        with open(OUTPUT_DIR / Path(filename), 'w') as file:
            file.write(d)
        return d
    return inner

# For now it's unclear what the 'expected' results should be, so these
# tests will never FAIL (but it can still ERROR). Run pytest with the -s
# flag to see these outputs.

@export
@pytest.mark.parametrize('fn', cases)
def test_contrived_examples_htmltreediff(fn):
    before, after = lookup_pair(fn)
    d = html_tree_diff(before, after)['diff']
    print(TEMPLATE.format(before, after, d))
    return d


@export
@pytest.mark.parametrize('fn', cases)
def test_contrived_examples_html_diff_render(fn):
    before, after = lookup_pair(fn)
    d = html_diff_render(before, after)['combined']
    print(TEMPLATE.format(before, after, d))
    return d


@export
@pytest.mark.parametrize('fn', cases)
def test_contrived_examples_htmldiffer(fn):
    before, after = lookup_pair(fn)
    d = html_differ(before, after)['diff']
    print(TEMPLATE.format(before, after, d))
    return d


### TESTS ON MORE COMPLEX, REAL CASES

staging_version_ids = [
    # @Mr0grog: "The “Survivor Impacts” text is in a `<p>` element between
    # two `<ul>` elements on this page, but in the diff, the `<p>` gets moved
    # _into_ the `<ul>`, so it renders like a list item instead of like the
    # header-ish thing it actually is."
    ('f2d5d701-707a-42e0-8881-653346d01e0a',
     'fc74d750-c651-46b7-bf74-434ad8c62e04'),
    # See issue #99
    ('9d4de183-a186-456c-bffb-55d82989877d',
     '775a8b04-9bac-4d0d-8db0-a8e133c4a964'),
]

# Fetch content as we need it, and cache. This can potentially matter if a
# subset of the tests are run.
version_content_cache = {}
CACHE_DIR = Path(__file__).resolve().parent / Path('fixtures', 'versions')
os.makedirs(CACHE_DIR, exist_ok=True)


def get_staging_content(version_id):
    # Try our in-memory cache, the on-disk cache, and finally the network.
    try:
        return version_content_cache[version_id]
    except KeyError:
        # These data files come from Web Monitoring's live server. If they need
        # to be updated or replaced for some reason, they should be the
        # archived response bodies associated with the given version_id.
        with open(CACHE_DIR / Path(version_id), 'r') as f:
            content = f.read()
        version_content_cache[version_id] = content
        return content


@export
@pytest.mark.parametrize('before_id, after_id', staging_version_ids)
def test_real_examples_htmltreediff(before_id, after_id):
    before, after = map(get_staging_content, (before_id, after_id))
    diff = html_tree_diff(before, after)['diff']
    return diff


@export
@pytest.mark.parametrize('before_id, after_id', staging_version_ids)
def test_real_examples_html_diff_render(before_id, after_id):
    before, after = map(get_staging_content, (before_id, after_id))
    return html_diff_render(before, after)['combined']


@export
@pytest.mark.parametrize('before_id, after_id', staging_version_ids)
def test_real_examples_htmldiffer(before_id, after_id):
    before, after = map(get_staging_content, (before_id, after_id))
    diff = html_differ(before, after)['diff']
    return diff
