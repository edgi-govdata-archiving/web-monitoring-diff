"""
This HTML-diffing implementation is based on LXML’s `html.diff` module. It is
meant to create HTML documents that can be viewed in a browser to highlight
and visualize portions of the page that changed.

We’ve tweaked the implementation of LXML’s diff algorithm significantly, to the
point where this is nearly a fork. It may properly become one in the future.

For now, you can mentally divide this module into two sections:

1. A higher-level routine that wraps the underlying diff implementation and
   formats responses. (The entry point for this is html_diff_render.)
2. A heavily modified version of LXML’s `html.diff`. It still leverages and
   depends on some parts of the LXML module, but that could change. (The entry
   point for this is _htmldiff)
"""
from bs4 import BeautifulSoup, Comment
from collections import Counter, namedtuple
from enum import Enum
from functools import lru_cache
import copy
import difflib
from .utils import get_color_palette
import html
import html5_parser
import logging
import re
from .content_type import raise_if_not_diffable_html
from .basic_diffs import compute_dmp_diff

# Imports only used in forked tokenization code; may be ripe for removal:
from lxml import etree
from html import escape as html_escape


logger = logging.getLogger(__name__)

# This *really* means don't cross the boundaries of these elements with
# insertion/deletion elements. Instead, break the insertions/deletions in two.
# TODO: custom elements are iffy here. Maybe include them? (any tag with a `-`
# in the name)
block_level_tags = set([
    'address',
    'article',
    'aside',
    'blockquote',
    'caption',
    'center',  # historic
    'dd',
    'details',
    'dialog',
    'dir',  # historic
    'div',
    'dl',
    'dt',
    'fieldset',
    'figcaption',
    'figure',
    'frameset',  # historic
    'footer',
    'form',
    'h1',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'header',
    'hgroup',
    'hr',
    'isindex',  # historic
    'li',
    'main',
    'menu',
    'nav',
    'noframes',  # historic
    'ol',
    'p',
    'pre',
    'section',
    'summary',
    'table',
    'ul',

    # Not "block" exactly, but don't cross its boundary
    'colgroup',
    'tbody',
    'thead',
    'tfoot',
    'tr',
    'td',
    'th',
    'noscript',
    'canvas',

    # These are "transparent", which means they *can* be a block
    'a',
    'del',
    'ins',
    'slot',
])

# "void" in the HTML sense -- these tags do not having a closing tag
void_tags = (
    'area',
    'base',
    'basefont',
    'br',
    'col',
    'embed',
    'img',
    'input',
    'link',
    'meta',
    'param',
    'source',
    'track',
    'wbr'
)

# Tags that do not allow content or whose content can be ignored
empty_tags = (*void_tags, 'iframe')

# Should be treated as a single unit for diffing purposes -- their content is not HTML
# TODO: fork the tokenization part of lxml.html.diff and use this list!
undiffable_content_tags = set([
    'datalist',  # Still HTML content, but we can’t really diff inside
    'math',
    'option',
    'rp',
    'script',
    'select',  # Still HTML content, but we can’t really diff inside
    'style',
    'svg',
    'template',
    'textarea'
])

# Elements that are not allowed to have our change elements as direct children
# TODO: add a cleanup step that respects this situation
no_change_children_tags = set([
    'colgroup',
    'dl',
    'hgroup',
    'menu',
    'ol',
    'optgroup',
    'picture',
    'select',
    'table',
    'tbody',
    'thead',
    'tfoot',
    'tr',
    'ul',
])

# TODO: do we need special treatment for `<picture>`? Kind of like `<img>`

# Active elements are those that don't render, but affect other elements on the
# page. When viewing a combined diff, these elements need to be "deactivated"
# so their old and new versions don't compete.
ACTIVE_ELEMENTS = ('script', 'style')

# This diff is fundamentally a word-by-word diff, which attempts to re-assemble
# the tags that were present before or after a word after diffing the text.
# To help ensure a sense of structure is still involved in the diff, we look
# words preceded by these tags and add several special, matchable tokens in
# front of the word so that the actual diff algorithm sees some "sameness."
#
# One would *think* including `<h#>` tags here would make sense, but it turns
# out we've seen a variety of real-world situations where tags flip from inline
# markup to headings or headings nested by themselves (!) in other structural
# markup, making them cause frequent problems if included here.
SEPARATABLE_TAGS = set(['blockquote', 'section', 'article', 'header', 'footer',
                        'pre', 'ul', 'ol', 'li', 'table', 'p'])
# SEPARATABLE_TAGS = block_level_tags

# A simplistic, empty HTML document to use in place of totally empty content
EMPTY_HTML = '''<html>
    <head></head>
    <body>
        <p style="text-align: center;">[No Content]</p>
    </body>
</html>'''

# Maximum number of spacer tokens to add to a token stream for a document.
# Adding too many can cause SequenceMatcher to choke.
MAX_SPACERS = 2500


class WaybackUrlComparator:
    """
    Compares Wayback Machine links from multiple timeframes as if they are the
    same. For example, these two URLs would be equivalent:
    - http://web.archive.org/web/20190525141538/https://www.noaa.gov/
    - http://web.archive.org/web/20181231224558/https://www.noaa.gov/
    """
    matcher = re.compile(r'web/\d{14}(im_|js_|cs_)?/(https?://)?(www.)?')

    def compare(self, url_a, url_b):
        match_a = self.matcher.search(url_a)
        match_b = self.matcher.search(url_b)
        if match_a and match_b:
            url_a = url_a[match_a.end():]
            url_b = url_b[match_b.end():]
            return url_a == url_b
        return url_a == url_b


class WaybackUkUrlComparator(WaybackUrlComparator):
    """
    Compares UK Web Archive (webarchive.org.uk) links from multiple timeframes
    as if they are the same. For example, these two URLs would be equivalent:
    - https://www.webarchive.org.uk/wayback/en/archive/20190525141538/https://www.example.gov/
    - https://www.webarchive.org.uk/wayback/en/archive/20181231224558/https://www.example.gov/
    """
    matcher = re.compile(r'https://www\.webarchive\.org\.uk/wayback/en/archive/\d{14}(mp_|im_)?/(https?://)?(www.)?')


class ServletSessionUrlComparator:
    """
    Ignores Java Servlet session IDs in URLs when comparing. (Servlets may
    store session IDs in the URL instead of cookies.) For example, these two
    URLs would be equivalent:
    - https://www.ncdc.noaa.gov/homr/api;jsessionid=A2DECB66D2648BFED11FC721FC3043A1
    - https://www.ncdc.noaa.gov/homr/api;jsessionid=B3EFDC88E3759CGFE22GD832GD4154B2

    Because they both refer to `https://www.ncdc.noaa.gov/homr/api`.
    """
    matcher = re.compile(r';jsessionid=[^;]+')

    def compare(self, url_a, url_b):
        match_a = self.matcher.sub('', url_a, count=1)
        match_b = self.matcher.sub('', url_b, count=1)
        return match_a == match_b


class CompoundComparator:
    """
    Compares URLs using multiple comparators. If any of the comparators claim
    a URL is equivalent, the final result is that the URLs are equal.

    Parameters
    ----------
    comparators : list of Comparator
    """
    def __init__(self, comparators):
        self.comparators = comparators

    def compare(self, url_a, url_b):
        for comparator in self.comparators:
            if comparator.compare(url_a, url_b):
                return True

        return False


class UrlRules:
    """
    The UrlComparisonRule class represents the the mapping between the
    various Comparator classes and the keywords used to match them. This
    mapping is done inside the RULES dictionary.
    """
    RULES = {'jsessionid': ServletSessionUrlComparator,
             'wayback': WaybackUrlComparator,
             'wayback_uk': WaybackUkUrlComparator}

    @classmethod
    def compare_array(cls, url_list_a, url_list_b, comparator):
        if len(url_list_a) == 0 == len(url_list_b):
            return True

        for url_a in url_list_a:
            for url_b in url_list_b:
                if comparator:
                    if comparator.compare(url_a, url_b):
                        return True
                elif url_a == url_b:
                    return True
        return False

    @classmethod
    def get_comparator(cls, mode):
        if not mode:
            return None

        try:
            comparators = [cls.RULES[name.strip()]()
                           for name in mode.split(',')]
            return CompoundComparator(comparators)
        except KeyError:
            raise KeyError(f'{mode} is an invalid strict URL rule.')


def html_diff_render(a_text, b_text, a_headers=None, b_headers=None,
                     include='combined', content_type_options='normal',
                     url_rules='jsessionid'):
    """
    HTML Diff for rendering. This is focused on visually highlighting portions
    of a page’s text that have been changed. It does not do much to show how
    node types or attributes have been modified (save for link or image URLs).

    The overall page returned primarily represents the structure of the "new"
    or "B" version. However, it contains some useful metadata in the `<head>`:

    1. A `<template id="wm-diff-old-head">` contains the contents of the "old"
       or "A" version’s `<head>`.
    2. A `<style id="wm-diff-style">` contains styling diff-specific styling.
    3. A `<meta name="wm-diff-title" content="[diff]">` contains a renderable
       HTML diff of the page’s `<title>`. For example:

       `The <del>old</del><ins>new</ins> title`

    NOTE: you may want to be careful with rendering this response as-is;
    inline `<script>` and `<style>` elements may be included twice if they had
    changes, which could have undesirable runtime effects.

    Parameters
    ----------
    a_text : string
        Source HTML of one document to compare
    b_text : string
        Source HTML of the other document to compare
    a_headers : dict
        Any HTTP headers associated with the `a` document
    b_headers : dict
        Any HTTP headers associated with the `b` document
    include : string
        Which comparisons to include in output. Options are:

        - `combined` returns an HTML document with insertions and deletions
          together.

        - `insertions` returns an HTML document with only the unchanged text
          and text inserted in the `b` document.

        - `deletions` returns an HTML document with only the unchanged text and
          text that was deleted from the `a` document.

        - `all` returns all of the above documents. You might use this for
          efficiency -- the most expensive part of the diff is only performed
          once and reused for all three return types.

    content_type_options : string
        Change how content type detection is handled. It doesn’t make a lot of
        sense to apply an HTML-focused diffing algorithm to, say, a JPEG image,
        so this function uses a combination of headers and content sniffing to
        determine whether a document is not HTML (it’s lenient; if it's not
        pretty clear that it's not HTML, it’ll try and diff). Options are:

        - `normal` uses the `Content-Type` header and then falls back to
          sniffing to determine content type.
        - `nocheck` ignores the `Content-Type` header but still sniffs.
        - `nosniff` uses the `Content-Type` header but does not sniff.
        - `ignore` doesn’t do any checking at all.

    url_rules : string
        Use specialized rules for comparing URLs in links, images, etc.
        Possible values are:

        - `jsessionid` ignores Java Servlet session IDs in URLs.
        - `wayback` considers two Wayback Machine links as equivalent if they have
          the same original URL, regardless of each of their timestamps.
        - `wayback_uk` like `wayback`, but for the UK Web Archive (webarchive.org.uk)

        You can also combine multiple comparison rules with a comma,
        e.g. `jsessionid,wayback`. Use None or an empty string for exact
        comparisons. (Default: `jsessionid`)

    Examples
    --------
    >>> text1 = '<!DOCTYPE html><html><head></head><body><p>Paragraph</p></body></html>'
    ... text2 = '<!DOCTYPE html><html><head></head><body><h1>Header</h1></body></html>'
    ... test_diff_render = html_diff_render(text1,text2)
    """
    raise_if_not_diffable_html(
        a_text,
        b_text,
        a_headers,
        b_headers,
        content_type_options)

    comparator = UrlRules.get_comparator(url_rules)

    soup_old = html5_parser.parse(a_text.strip() or EMPTY_HTML,
                                  treebuilder='soup', return_root=False)
    soup_new = html5_parser.parse(b_text.strip() or EMPTY_HTML,
                                  treebuilder='soup', return_root=False)

    # Remove comment nodes since they generally don't affect display.
    # NOTE: This could affect display if the removed are conditional comments,
    # but it's unclear how we'd meaningfully visualize those anyway.
    [element.extract() for element in
     soup_old.find_all(string=lambda text:isinstance(text, Comment))]
    [element.extract() for element in
     soup_new.find_all(string=lambda text:isinstance(text, Comment))]

    soup_old = _cleanup_document_structure(soup_old)
    soup_new = _cleanup_document_structure(soup_new)

    results, diff_bodies = diff_elements(soup_old.body, soup_new.body, comparator, include)

    for diff_type, diff_body in diff_bodies.items():
        soup = None
        if diff_type == 'deletions':
            soup = copy.copy(soup_old)
        elif diff_type == 'insertions':
            soup = copy.copy(soup_new)
        else:
            soup = copy.copy(soup_new)
            title_meta = soup.new_tag(
                'meta',
                content=_diff_title(soup_old, soup_new))
            title_meta.attrs['name'] = 'wm-diff-title'
            soup.head.append(title_meta)

            old_head = soup.new_tag('template', id='wm-diff-old-head')
            if soup_old.head:
                for node in soup_old.head.contents.copy():
                    old_head.append(copy.copy(node))
            soup.head.append(old_head)

        change_styles = soup.new_tag(
            "style",
            type="text/css",
            id='wm-diff-style')
        change_styles.string = get_diff_styles()
        soup.head.append(change_styles)

        soup.body.replace_with(diff_body)
        runtime_scripts = soup.new_tag('script', id='wm-diff-script')
        runtime_scripts.string = UPDATE_CONTRAST_SCRIPT
        soup.body.append(runtime_scripts)
        if diff_type == 'combined':
            _deactivate_deleted_active_elements(soup)
        # Convert to a string instead of prettifying. `prettify()` will always
        # add extra space around non-inline elements, even if `formatter` is
        # "minimal" or None. This is a problem because the page may use pre-
        # formatted text or use CSS to make block elements display as inline,
        # etc. There are lots of situations where the spacing really matters,
        # so we want to make sure not to alter it.
        results[diff_type] = str(soup)

    return results


def _cleanup_document_structure(soup):
    """Ensure a BeautifulSoup document has a <head> and <body>"""
    if not soup.head:
        head = soup.new_tag('head')
        soup.html.insert(0, head)
    if not soup.body:
        body = soup.new_tag('body')
        soup.html.append(body)
    return soup


def _deactivate_deleted_active_elements(soup):
    for element in soup.find_all(ACTIVE_ELEMENTS):
        if element.find_parent('del'):
            wrapper = soup.new_tag('template')
            wrapper['class'] = 'wm-diff-deleted-inert'
            element.wrap(wrapper)

    return soup


def get_title(soup):
    "Get the title of a Beautiful Soup document."
    return soup.title and soup.title.string or ''


def _html_for_dmp_operation(operation):
    "Convert a diff-match-patch operation to an HTML string."
    html_value = html.escape(operation[1])
    if operation[0] == -1:
        return f'<del class="wm-diff">{html_value}</del>'
    elif operation[0] == 1:
        return f'<ins class="wm-diff">{html_value}</ins>'
    else:
        return html_value


def _diff_title(old, new):
    """
    Create an HTML diff (i.e. a string with `<ins>` and `<del>` tags) of the
    title of two Beautiful Soup documents.
    """
    diff = compute_dmp_diff(get_title(old), get_title(new))
    return ''.join(map(_html_for_dmp_operation, diff))


def diff_elements(old, new, comparator, include='all'):
    if not old:
        old = BeautifulSoup().new_tag('div')
    if not new:
        new = BeautifulSoup().new_tag('div')

    def fill_element(element, diff):
        result_element = copy.copy(element)
        result_element.clear()
        # At this point, `diff` is an HTML *string*, so we need to parse it
        # before we can safely insert it into a soup. (We used to insert it as
        # a string and do some funny tricks, but this led to other issues.)
        # TODO: _htmldiff() should return a tree of soup tags rather than a
        # list of strings, so we don't need to re-parse here.
        parsed_diff = html5_parser.parse(
            f'<!doctype html>\n<html><body>{diff}</body></html>',
            treebuilder='soup', return_root=False)
        # `.contents/.children` are *live*, so cache their output into a new
        # list before moving them to the new container -- otherwise we'll miss
        # some because the contents change in the middle of moving.
        result_element.extend(list(parsed_diff.body.children))
        return result_element

    results = {}
    metadata, raw_diffs = _htmldiff(_diffable_fragment(old),
                                    _diffable_fragment(new),
                                    comparator,
                                    include)

    for diff_type, diff in raw_diffs.items():
        element = diff_type == 'deletions' and old or new
        results[diff_type] = fill_element(element, diff)

    return metadata, results


def _diffable_fragment(element):
    """
    Convert a beautiful soup element into an HTML fragment string with just the
    element's *contents* that is ready for diffing.
    """
    # FIXME: we have to remove <ins> and <del> tags because *we* use them to
    # indicate changes that we find. We probably shouldn't do that:
    # https://github.com/edgi-govdata-archiving/web-monitoring-processing/issues/69#issuecomment-321424897
    for edit_tag in element.find_all(_is_ins_or_del):
        edit_tag.unwrap()
    # Create a fragment string of just the element's contents
    return ''.join(map(str, element.children))


def _is_ins_or_del(tag):
    return tag.name == 'ins' or tag.name == 'del'


# FIXME: this should take two BeautifulSoup elements to diff (since we've
# already parsed and generated those), not two HTML fragment strings that have
# to get parsed again.
def _htmldiff(old, new, comparator, include='all'):
    """
    A slightly customized version of htmldiff that uses different tokens.
    """
    old_tokens = tokenize(old, comparator)
    new_tokens = tokenize(new, comparator)
    # old_tokens = [_customize_token(token) for token in old_tokens]
    # new_tokens = [_customize_token(token) for token in new_tokens]
    old_tokens = _limit_spacers(_customize_tokens(old_tokens), MAX_SPACERS)
    new_tokens = _limit_spacers(_customize_tokens(new_tokens), MAX_SPACERS)
    # result = htmldiff_tokens(old_tokens, new_tokens)
    # result = diff_tokens(old_tokens, new_tokens) #, include='delete')
    logger.debug('CUSTOMIZED!')

    # HACK: The whole "spacer" token thing above in this code triggers the
    # `autojunk` mechanism in SequenceMatcher, so we need to explicitly turn
    # that off. That's probably not great, but I don't have a better approach.
    matcher = InsensitiveSequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    # matcher = SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    opcodes = matcher.get_opcodes()

    metadata = _count_changes(opcodes)
    diffs = {}

    def render_diff(diff_type):
        diff = assemble_diff(old_tokens, new_tokens, opcodes, diff_type)
        # return fixup_ins_del_tags(''.join(diff).strip())
        result = ''.join(diff).strip().replace('</li> ', '</li>')
        return result

    if include == 'all' or include == 'combined':
        diffs['combined'] = render_diff('combined')
    if include == 'all' or include == 'insertions':
        diffs['insertions'] = render_diff('insertions')
    if include == 'all' or include == 'deletions':
        diffs['deletions'] = render_diff('deletions')

    return metadata, diffs


# FIXME: this is utterly ridiculous -- the crazy spacer token solution we came
# up with can add so much extra stuff to some kinds of pages that
# SequenceMatcher chokes on it. This strips out excess spacers. We should
# really re-examine the whole spacer token concept now that we control the
# tokenization phase, though.
def _limit_spacers(tokens, max_spacers):
    limited_tokens = []
    for token in tokens:
        if isinstance(token, SpacerToken):
            if max_spacers <= 0:
                continue
            max_spacers -= 1
        limited_tokens.append(token)

    return limited_tokens


def _count_changes(opcodes):
    counts = Counter(map(lambda operation: operation[0], opcodes))
    return {
        'change_count': counts['insert'] + counts['delete'] + 2 * counts['replace'],
        'deletions_count': counts['delete'] + counts['replace'],
        'insertions_count': counts['insert'] + counts['replace'],
    }


# --------------------- lxml.html.diff Tokenization --------------------------
# The following tokenization-related code is more-or-less copied from
# lxml.html.diff. We plan to change it significantly.

def expand_tokens(tokens, equal=False):
    """Given a list of tokens, return a generator of the chunks of
    text for the data in the tokens.
    """
    for token in tokens:
        for pre in token.pre_tags:
            yield pre
        if not equal or not token.hide_when_equal:
            if token.trailing_whitespace:
                yield token.html() + token.trailing_whitespace
            else:
                yield token.html()
        for post in token.post_tags:
            yield post


class DiffToken(str):
    """ Represents a diffable token, generally a word that is displayed to
    the user.  Opening tags are attached to this token when they are
    adjacent (pre_tags) and closing tags that follow the word
    (post_tags).  Some exceptions occur when there are empty tags
    adjacent to a word, so there may be close tags in pre_tags, or
    open tags in post_tags.

    We also keep track of whether the word was originally followed by
    whitespace, even though we do not want to treat the word as
    equivalent to a similar word that does not have a trailing
    space."""

    # When this is true, the token will be eliminated from the
    # displayed diff if no change has occurred:
    hide_when_equal = False

    def __new__(cls, text, pre_tags=None, post_tags=None, trailing_whitespace=""):
        obj = str.__new__(cls, text)

        if pre_tags is not None:
            obj.pre_tags = pre_tags
        else:
            obj.pre_tags = []

        if post_tags is not None:
            obj.post_tags = post_tags
        else:
            obj.post_tags = []

        obj.trailing_whitespace = trailing_whitespace

        return obj

    def __repr__(self):
        return 'DiffToken(%s, %r, %r, %r)' % (str.__repr__(self), self.pre_tags,
                                              self.post_tags, self.trailing_whitespace)

    def html(self):
        return str(self)


class tag_token(DiffToken):

    """ Represents a token that is actually a tag.  Currently this is just
    the <img> tag, which takes up visible space just like a word but
    is only represented in a document by a tag.  """

    def __new__(cls, tag, data, html_repr, comparator, pre_tags=None,
                post_tags=None, trailing_whitespace=""):
        obj = DiffToken.__new__(cls, "%s: %s" % (type, data),
                            pre_tags=pre_tags,
                            post_tags=post_tags,
                            trailing_whitespace=trailing_whitespace)
        obj.tag = tag
        obj.data = data
        obj.html_repr = html_repr
        obj.comparator = comparator
        return obj

    def __repr__(self):
        return 'tag_token(%s, %s, html_repr=%s, post_tags=%r, pre_tags=%r, trailing_whitespace=%r)' % (
            self.tag,
            self.data,
            self.html_repr,
            self.pre_tags,
            self.post_tags,
            self.trailing_whitespace)

    def html(self):
        return self.html_repr


class href_token(DiffToken):
    """ Represents the href in an anchor tag.  Unlike other words, we only
    show the href when it changes.  """

    hide_when_equal = True

    def __new__(cls, href, comparator, pre_tags=None,
                post_tags=None, trailing_whitespace=""):
        obj = DiffToken.__new__(cls, text=href,
                                pre_tags=pre_tags,
                                post_tags=post_tags,
                                trailing_whitespace=trailing_whitespace)
        obj.comparator = comparator
        return obj

    def __eq__(self, other):
        # This equality check aims to apply specific rules to the contents of
        # the href element solving false positive cases
        if not isinstance(other, href_token):
            return False
        if self.comparator:
            return self.comparator.compare(str(self), str(other))
        return super().__eq__(other)

    def __hash__(self):
        return super().__hash__()

    def html(self):
        return ' Link: %s' % self


class UndiffableContentToken(DiffToken):
    pass


# FIXME: this should be adapted to work off a BeautifulSoup element instead of
# an etree/lxml element, since we already have that and could avoid re-parsing
# the whole document a second time.
def tokenize(html, comparator, include_hrefs=True):
    """
    Parse the given HTML and returns token objects (words with attached tags).

    This parses only the content of a page; anything in the head is
    ignored, and the <head> and <body> elements are themselves
    optional.  The content is then parsed by lxml, which ensures the
    validity of the resulting parsed document (though lxml may make
    incorrect guesses when the markup is particular bad).

    <ins> and <del> tags are also eliminated from the document, as
    that gets confusing.

    If include_hrefs is true, then the href attribute of <a> tags is
    included as a special kind of diffable token."""
    if etree.iselement(html):
        body_el = html
    else:
        body_el = parse_html(html)
    # Then we split the document into text chunks for each tag, word, and end tag:
    chunks = flatten_el(body_el, skip_tag=True, include_hrefs=include_hrefs)
    # Finally re-joining them into token objects:
    return fixup_chunks(chunks, comparator)

def parse_html(html):
    """
    Parses an HTML fragment, returning an lxml element.  Note that the HTML
    will be wrapped in a <div> tag that was not in the original document.
    """
    return html5_parser.parse(html, treebuilder='lxml')


def split_trailing_whitespace(word):
    """
    This function takes a word, such as 'test\n\n' and returns ('test','\n\n')
    """
    stripped_length = len(word.rstrip())
    return word[0:stripped_length], word[stripped_length:]

class TokenType(Enum):
    undiffable = 0
    start_tag = 1
    end_tag = 2
    word = 3
    href = 4
    img = 5


def fixup_chunks(chunks, comparator):
    """
    This function takes a list of chunks and produces a list of tokens.
    """
    tag_accum = []
    cur_word = None
    result = []
    for chunk in chunks:
        current_token = chunk[0]
        if current_token == TokenType.img:
            src = chunk[1]
            tag, trailing_whitespace = split_trailing_whitespace(chunk[2])
            cur_word = ImgTagToken('img', data=src, html_repr=tag,
                                   comparator=comparator, pre_tags=tag_accum,
                                   trailing_whitespace=trailing_whitespace)
            tag_accum = []
            result.append(cur_word)

        elif current_token == TokenType.href:
            href = chunk[1]
            cur_word = href_token(href, comparator=comparator, pre_tags=tag_accum, trailing_whitespace=" ")
            tag_accum = []
            result.append(cur_word)

        elif current_token == TokenType.undiffable:
            cur_word = UndiffableContentToken(chunk[1], pre_tags=tag_accum)
            tag_accum = []
            result.append(cur_word)

        elif current_token == TokenType.word:
            chunk, trailing_whitespace = split_trailing_whitespace(chunk[1])
            cur_word = DiffToken(chunk, pre_tags=tag_accum, trailing_whitespace=trailing_whitespace)
            tag_accum = []
            result.append(cur_word)

        elif current_token == TokenType.start_tag:
            tag_accum.append(chunk[1])

        elif current_token == TokenType.end_tag:
            if tag_accum:
                tag_accum.append(chunk[1])
            else:
                assert cur_word, (
                    "Weird state, cur_word=%r, result=%r, chunks=%r of %r"
                    % (cur_word, result, chunk, chunks))
                cur_word.post_tags.append(chunk[1])
        else:
            assert(0)

    if not result:
        return [DiffToken('', pre_tags=tag_accum)]
    else:
        result[-1].post_tags.extend(tag_accum)

    return result


def flatten_el(el, include_hrefs, skip_tag=False):
    """ Takes an lxml element el, and generates all the text chunks for
    that tag.  Each start tag is a chunk, each word is a chunk, and each
    end tag is a chunk.

    If skip_tag is true, then the outermost container tag is
    not returned (just its contents)."""
    if not skip_tag:
        if el.tag == 'img':
            src_array = []
            # The `data-src` attribute is very commonly used for JS to lazy-
            # load images, so allow it in lieu of `src`.
            el_src = el.get('src') or el.get('data-src')
            if el_src is not None:
                src_array.append(el_src)

            # Same as above with `data-srcset` here.
            srcset = el.get('srcset') or el.get('data-srcset')
            if srcset is not None:
                for src in srcset.split(','):
                    src_array.append(src.split(' ', maxsplit=1)[0])

            yield (TokenType.img, src_array, start_tag(el))

        elif el.tag in undiffable_content_tags:
            element_source = etree.tostring(el, encoding=str, method='html')
            yield (TokenType.undiffable, element_source)
            return
        else:
            yield (TokenType.start_tag, start_tag(el))
    if el.tag in void_tags and not el.text and not len(el) and not el.tail:
        return
    start_words = split_words(el.text)
    for word in start_words:
        yield (TokenType.word, html_escape(word))
    for child in el:
        for item in flatten_el(child, include_hrefs=include_hrefs):
            yield item
    if el.tag == 'a' and el.get('href') and include_hrefs:
        yield (TokenType.href, el.get('href'))
    if not skip_tag:
        yield (TokenType.end_tag, end_tag(el))
        end_words = split_words(el.tail)
        for word in end_words:
            yield (TokenType.word, html_escape(word))

split_words_re = re.compile(r'\S+(?:\s+|$)', re.U)

def split_words(text):
    """ Splits some text into words. Includes trailing whitespace
    on each word when appropriate.  """
    if not text or not text.strip():
        return []

    words = split_words_re.findall(text)
    return words

start_whitespace_re = re.compile(r'^[ \t\n\r]')

def start_tag(el):
    """
    The text representation of the start tag for a tag.
    """
    return '<%s%s>' % (
        el.tag, ''.join([' %s="%s"' % (name, html_escape(value, True))
                         for name, value in el.attrib.items()]))

def end_tag(el):
    """ The text representation of an end tag for a tag.  Includes
    trailing whitespace when appropriate.  """
    if el.tail and start_whitespace_re.search(el.tail):
        extra = ' '
    else:
        extra = ''
    return '</%s>%s' % (el.tag, extra)


# ------------------ END lxml.html.diff Tokenization ------------------------


class MinimalHrefToken(href_token):
    """
    A diffable token representing the URL of an <a> element. This allows the
    URL of a link to be diffed. However, we don't actually want to *render*
    the URL in the output (it's quite noisy in practice).

    Future revisions may change this for more complex, useful output.
    """
    def html(self):
        # FIXME: we really do need some kind of sentinel here, even if we
        # only use it to track that there was a potential URL change. If the
        # URL diff does not coalesce with the link text, this becomes an empty
        # `<ins>/<del>` element and is probably not user-visible.
        # On the flip side, these can often get spuriously rendered because of
        # the same coalescing.
        #
        # Maybe: render a special tag, e.g. `<span class="wm-diff-url">`, then,
        # when cleaning up the diff, find instances of that tag, examine the
        # parent `<a>` element, and do something special if the link really
        # did change. Otherwise, delete it.
        #
        # Also: any such sentinel element MUST be one the lxml diffr thinks is
        # "empty" (i.e. self-closing). Otherwise, it may spuriously move a
        # subsequent change back through the document *into* the sentinel when
        # attempting to clean and "re-balance" the DOM tree. So, it must be one
        # of: [param, img, area, br, basefont, input, base, meta, link, col]
        return ''


# Explicitly designed to render repeatable crap so you can force-create
# unchanged areas in the diff, but not render that crap to the final result.
class SpacerToken(DiffToken):
    # def __new__(cls, text, pre_tags=None, post_tags=None, trailing_whitespace=""):
    #     obj = str.__new__(cls, text)

    #     if pre_tags is not None:
    #         obj.pre_tags = pre_tags
    #     else:
    #         obj.pre_tags = []

    #     if post_tags is not None:
    #         obj.post_tags = post_tags
    #     else:
    #         obj.post_tags = []

    #     obj.trailing_whitespace = trailing_whitespace
    #     return obj

    def html(self):
        return ''


# I had some weird concern that I needed to make this token a single word with
# no spaces, but now that I know this differ more deeply, this is pointless.
class ImgTagToken(tag_token):

    def __new__(cls, tag, data, html_repr, comparator, pre_tags=None,
                post_tags=None, trailing_whitespace=""):
        obj = DiffToken.__new__(cls, "\n\nImg:%s\n\n" % str(data),
                                pre_tags=pre_tags,
                                post_tags=post_tags,
                                trailing_whitespace=trailing_whitespace)
        obj.tag = tag
        obj.data = data
        obj.html_repr = html_repr
        obj.comparator = comparator
        return obj

    def __eq__(self, other):
        if isinstance(other, ImgTagToken):
            return UrlRules.compare_array(self.data, other.data, self.comparator)
        return False

    def __hash__(self):
        return super().__hash__()


def _customize_tokens(tokens):
    SPACER_STRING = '\nSPACER'

    # Balance out pre- and post-tags so that a token of text is surrounded by
    # the opening and closing tags of the element it's in. For example:
    #
    #    <p><a>Hello!</a></p><div>…there.</div>
    #
    # Currently parses as:
    #    [('Hello!', pre=['<p>','<a>'], post=[]),
    #     ('…there.', pre=['</a>','</p>','<div>'], post=['</div>'])]
    #    (Note the '</div>' post tag is only present at the end of the doc)
    #
    # But this attempts make it more like:
    #
    #    [('Hello!', pre=['<p>','<a>'], post=['</a>','</p>']),
    #     ('…there.', pre=[<div>'], post=['</div>'])]
    #
    # TODO: when we get around to also forking the parse/tokenize part of this
    # diff, do this as part of the original tokenization instead.
    for token_index, token in enumerate(tokens):
        # logger.debug(f'Handling token {token_index}: {token}')
        if token_index == 0:
            continue
        previous = tokens[token_index - 1]
        previous_post_complete = False
        for post_index, tag in enumerate(previous.post_tags):
            if not tag.startswith('</'):
                # TODO: should we attempt to fill pure-structure tags here with
                # spacers? e.g. should we take the "<p><em></em></p>" here and
                # wrap a spacer token in it instead of moving to "next-text's"
                # pre_tags? "text</p><p><em></em></p><p>next-text"
                token.pre_tags = previous.post_tags[post_index:] + token.pre_tags
                previous.post_tags = previous.post_tags[:post_index]
                previous_post_complete = True
                break

        if not previous_post_complete:
            for pre_index, tag in enumerate(token.pre_tags):
                if not tag.startswith('</'):
                    if pre_index > 0:
                        previous.post_tags.extend(token.pre_tags[:pre_index])
                        token.pre_tags = token.pre_tags[pre_index:]
                    break
            else:
                previous.post_tags.extend(token.pre_tags)
                token.pre_tags = []


        # logger.debug(f'  Result...\n    pre: {token.pre_tags}\n    token: "{token}"\n    post: {token.post_tags}')

    result = []
    # for token in tokens:
    for token_index, token in enumerate(tokens):
        # if str(token).lower().startswith('impacts'):
        # if str(token).lower().startswith('although'):
        #     logger.debug(f'SPECIAL TAG!\n  pre: {token.pre_tags}\n  token: "{token}"\n  post: {token.post_tags}')

        # hahaha, this is crazy. But anyway, insert "spacers" that have
        # identical text the diff algorithm can latch onto as an island of
        # unchangedness. We do this anywhere a SEPARATABLE_TAG is opened.
        # Basically, this lets us create a sort of "wall" between changes,
        # ensuring a continuous insertion or deletion can't spread across
        # list items, major page sections, etc.
        # See farther down in this same method for a repeat of this with
        # `post_tags`
        try_splitting = len(token.pre_tags) > 0
        split_start = 0
        while try_splitting:
            for tag_index, tag in enumerate(token.pre_tags[split_start:]):
                split_here = False
                for name in SEPARATABLE_TAGS:
                    if tag.startswith(f'<{name}'):
                        split_here = True
                        break
                if split_here:
                    # new_token = SpacerToken(SPACER_STRING, pre_tags=token.pre_tags[0:tag_index + 1])
                    # token.pre_tags = token.pre_tags[tag_index + 1:]

                    new_token = SpacerToken(SPACER_STRING, pre_tags=token.pre_tags[0:tag_index + split_start])
                    token.pre_tags = token.pre_tags[tag_index + split_start:]

                    # tokens.insert(token_index + 1, token)
                    # token = new_token
                    result.append(new_token)
                    result.append(SpacerToken(SPACER_STRING))
                    result.append(SpacerToken(SPACER_STRING))
                    try_splitting = len(token.pre_tags) > 1
                    split_start = 1
                    break
                else:
                    try_splitting = False


        # This is a CRITICAL scenario, but should probably be generalized and
        # a bit better understood. The case is empty elements that are fully
        # nested inside something, so you have a structure like:
        #
        #   <div><span><a></a></span></div><div>Text!</div>
        #
        # All the tags preceeding `Text!` get set as pre_tags for `Text!` and,
        # later, when stuff gets rebalanced, `Text!` gets moved down inside the
        # <div> that completely precedes it.
        for index, tag in enumerate(token.pre_tags):
            if tag.startswith('<a') and len(token.pre_tags) > index + 1:
                next_tag = token.pre_tags[index + 1]
                if next_tag and next_tag.startswith('</a'):
                    result.append(SpacerToken('~EMPTY~', pre_tags=token.pre_tags[0:index], post_tags=token.pre_tags[index:]))
                    token.pre_tags = []

        # if _has_separation_tags(token.pre_tags):
        #     # result.append(SpacerToken(SPACER_STRING, token.pre_tags))
        #     # token.pre_tags = []
        #     result.append(SpacerToken(SPACER_STRING))
        #     result.append(SpacerToken(SPACER_STRING))

        customized = _customize_token(token)
        result.append(customized)

        if str(customized) == "Posts" and str(tokens[token_index - 1]) == 'Other' and str(tokens[token_index - 2]) == 'and': # and str(tokens[token_index - 3]) == 'posts':
            logger.debug(f'SPECIAL TAG!\n  pre: {token.pre_tags}\n  token: "{token}"\n  post: {token.post_tags}')
            next_token = tokens[token_index + 1]
            logger.debug(f'SPECIAL TAG!\n  pre: {next_token.pre_tags}\n  token: "{next_token}"\n  post: {next_token.post_tags}')
            for tag_index, tag in enumerate(customized.post_tags):
                if tag.startswith('</ul>'):
                    new_token = SpacerToken(SPACER_STRING)
                    result.append(new_token)
                    new_token = SpacerToken(SPACER_STRING, pre_tags=customized.post_tags[tag_index:])
                    result.append(new_token)
                    customized.post_tags = customized.post_tags[:tag_index]

        # if isinstance(customized, ImgTagToken):
        #     result.append(SpacerToken(SPACER_STRING))
        #     result.append(SpacerToken(SPACER_STRING))
        #     result.append(SpacerToken(SPACER_STRING))
        #     logger.debug(f'IMAGE TOKEN:')
        #     logger.debug(f'  pre: {customized.pre_tags}\n  token: "{customized}"\n  post: {customized.post_tags}')

        # if len(customized.post_tags) > 0:
        #     result.append(SpacerToken('', post_tags=customized.post_tags))
        #     customized.post_tags = []

        # if (_has_separation_tags(customized.post_tags)):
        #     result.append(SpacerToken(SPACER_STRING, pre_tags=customized.post_tags))
        #     # result.append(SpacerToken(SPACER_STRING, post_tags=customized.post_tags, trailing_whitespace=customized.trailing_whitespace))
        #     customized.post_tags = []
        #     # customized.trailing_whitespace = ''
        for tag_index, tag in enumerate(customized.post_tags):
            split_here = False
            for name in SEPARATABLE_TAGS:
                if tag.startswith(f'<{name}'):
                    split_here = True
                    break
            if split_here:
                # new_token = SpacerToken(SPACER_STRING, pre_tags=customized.post_tags[tag_index + 1:])
                # customized.post_tags = customized.post_tags[0:tag_index + 1]

                # new_token = SpacerToken(SPACER_STRING, pre_tags=customized.post_tags[tag_index:])
                # customized.post_tags = customized.post_tags[0:tag_index]

                new_token = SpacerToken(SPACER_STRING, post_tags=customized.post_tags[tag_index:])
                customized.post_tags = customized.post_tags[0:tag_index]

                # tokens.insert(token_index + 1, token)
                # token = new_token
                result.append(new_token)
                result.append(SpacerToken(SPACER_STRING))
                result.append(SpacerToken(SPACER_STRING))
                break

    return result


# One would *think* including `<h#>` tags here would make sense, but it turns
# out we've seen a variety of real-world situations where tags flip from inline
# markup to headings or headings nested by themselves (!) in other structural
# markup, making them cause frequent problems if included here.
SEPARATABLE_TAGS = set(['blockquote', 'section', 'article', 'header',
                        'footer', 'pre', 'ul', 'ol', 'li', 'table', 'p'])
HEADING_TAGS = set(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
def _has_separation_tags(tag_list):
    for index, tag in enumerate(tag_list):
        for name in SEPARATABLE_TAGS:
            if tag.startswith(f'<{name}') or tag.startswith(f'</{name}'):
                logger.debug(f'Separating on: {name}')
                return True
        if 'id=' in tag:
            return True
    return False

def _has_heading_tags(tag_list):
    for index, tag in enumerate(tag_list):
        for name in HEADING_TAGS:
            if tag.startswith(f'<{name}') or tag.startswith(f'</{name}'):
                return True


# Seemed so nice and clean! But should probably be merged into
# `_customize_tokens()` now. Or otherwise it needs to be able to produce more
# than one token to replace the given token in the stream.
def _customize_token(token):
    """
    Replace existing diffing tokens with customized ones for better output.
    """
    if isinstance(token, href_token):
        return MinimalHrefToken(
            str(token),
            comparator=token.comparator,
            pre_tags=token.pre_tags,
            post_tags=token.post_tags,
            trailing_whitespace=token.trailing_whitespace)
        # return token
    else:
        return token


# TODO: merge and reconcile this with `merge_change_groups()`, which is 90%
# the same thing; it outputs the change elements as nested lists of tokens.
def merge_changes(change_chunks, doc, tag_type='ins'):
    """
    Merge tokens that were changed into a list of tokens (that represents the
    whole document) and wrap them with a tag.

    This will break the changed sections into multiple elements as needed to
    ensure that changes don't cross the boundaries of some elements, like
    `<header>` or `<p>`. For example, you'd get three `<ins>` elements here:

        <ins>Some</ins><p><ins>inserted</ins></p><ins>text</ins>

    Parameters
    ----------
    change_chunks : list of token
        The changes to merge.
    doc : list of token
        The "document" to merge `change_chunks` into.
    tag_type : str
        The type of HTML tag to wrap the changes with.
    """
    # NOTE: this serves a similar purpose to LXML's html.diff.merge_insert
    # function, though this is much more complicated. LXML's version takes a
    # simpler approach to placing tags, then later runs the whole thing through
    # an XML parser, manipulates the tree, and re-serializes. We don't do that
    # here because it turns out to exacerbate some errors in the placement of
    # insert and delete tags. Think of it like splinting a broken bone without
    # setting it first.
    #
    # Here, we actually attempt to keep track of the stack of elements and
    # proactively put tags in the right place and break them up so the
    # resulting token stream represents valid markup. Happily, that also means
    # we don't also have to do the expensive parse-then-serialize step later!
    depth = 0
    current_content = None
    for chunk in change_chunks:
        inline_tag = False
        inline_tag_name = None

        if chunk == '':
            continue

        # FIXME: explicitly handle elements that can't have our markers as
        # direct children.
        if chunk[0] == '<':
            # NOTE: split first by `>` because we could have undiffable items
            # with content here, i.e. more than one tag.
            name = chunk.split('>', 1)[0].split(None, 1)[0].strip('<>/')
            # Also treat `a` tags as block in this context, because they *can*
            # contain block elements, like `h1`, etc.
            is_block = name in block_level_tags or name == 'a'

            if chunk[1] == '/':
                if depth > 0:
                    if is_block:
                        for nested_tag in current_content:
                            doc.append(f'</{nested_tag}>')
                        doc.append(f'</{tag_type}>')
                        current_content = None
                        depth -= 1
                        doc.append(chunk)
                    else:
                        if name in current_content:
                            index = current_content.index(name)
                            current_content = current_content[index + 1:]
                            doc.append(chunk)
                        else:
                            # only a malformed document should hit this case
                            # where tags aren't properly nested ¯\_(ツ)_/¯
                            for nested_tag in current_content:
                                doc.append(f'</{nested_tag}>')

                            doc.append(f'</{tag_type}>')
                            doc.append(chunk)
                            doc.append(f'<{tag_type} class="wm-diff">')

                            # other side of the malformed document case from above
                            current_content.reverse()
                            for nested_tag in current_content:
                                doc.append(f'<{nested_tag}>')
                            current_content.reverse()
                else:
                    doc.append(chunk)
                # There is no case for a closing tag where aren't doen with the chunk
                continue
            else:
                if is_block:
                    if depth > 0:
                        for nested_tag in current_content:
                            doc.append(f'</{nested_tag}>')
                        doc.append(f'</{tag_type}>')
                        current_content = None
                        depth -= 1
                    doc.append(chunk)
                    continue
                else:
                    inline_tag = True
                    inline_tag_name = name

        if depth == 0:
            doc.append(f'<{tag_type} class="wm-diff">')
            depth += 1
            current_content = []

        doc.append(chunk)
        # Note the undiffable_content_tags check here. We assume tokens for
        # those tags represent a whole element, not just a start or end tag,
        # so we don't consider them "open" as part of `current_content`.
        if (inline_tag and
                (inline_tag_name not in undiffable_content_tags) and
                (inline_tag_name not in empty_tags)):
            # FIXME: track the original start tag for when we need to break
            # these elements around boundaries.
            current_content.insert(0, inline_tag_name)

    if depth > 0:
        for nested_tag in current_content:
            doc.append(f'</{nested_tag}>')

        doc.append(f'</{tag_type}>')

        current_content.reverse()
        for nested_tag in current_content:
            doc.append(f'<{nested_tag}>')


def assemble_diff(html1_tokens, html2_tokens, commands, include='combined'):
    """
    Assembles a renderable HTML string from a set of old and new tokens and a
    list of operations to perform agains them.
    """
    include_insert = include == 'combined' or include == 'insertions'
    include_delete = include == 'combined' or include == 'deletions'

    # Generating a combined diff view is a relatively complicated affair. We
    # keep track of all the consecutive insertions and deletions in buffers
    # until we find a portion of the document that is unchanged, at which point
    # we reconcile the DOM structures of the changes before inserting the
    # unchanged parts.
    result = []
    insert_buffer = []
    delete_buffer = []

    for command, i1, i2, j1, j2 in commands:
        if command == 'equal':
            # When encountering an unchanged series of tokens, we first expand
            # them to include the HTML elements that are attached to the
            # tokenized text. Then we find the changed HTML tags before and
            # after the unchanged text and add them to the previous buffer of
            # changes and the next buffer of changes, respectively. This
            # ensures that the reconciliation routine that handles differences
            # in DOM structure is used on them, while portions that are exactly
            # the same are simply inserted as-is.
            #
            # TODO: this splitting approach could probably be handled better if
            # it was part of or better integrated with expanding the tokens, so
            # we could just look at the first token's `pre_tags` and the last
            # token's `post_tags` instead of having to reverse engineer them.
            equal_buffer_delete = []
            equal_buffer_insert = []
            equal_buffer_delete_next = []
            equal_buffer_insert_next = []
            if include_insert and include_delete:
                merge_change_groups(
                    expand_tokens(html1_tokens[i1:i2], equal=True),
                    equal_buffer_delete,
                    tag_type=None)
                merge_change_groups(
                    expand_tokens(html2_tokens[j1:j2], equal=True),
                    equal_buffer_insert,
                    tag_type=None)

                first_delete_group = -1
                first_insert_group = -1
                for token_index, token in enumerate(equal_buffer_delete):
                    if isinstance(token, list):
                        first_delete_group = token_index
                        break
                for token_index, token in enumerate(equal_buffer_insert):
                    if isinstance(token, list):
                        first_insert_group = token_index
                        break
                # In theory we should always find both, but sanity check anyway
                if first_delete_group > -1 and first_insert_group > -1:
                    max_index = min(first_delete_group, first_insert_group)
                    unequal_reverse_index = max_index
                    for reverse_index in range(max_index):
                        delete_token = equal_buffer_delete[first_delete_group - 1 - reverse_index]
                        insert_token = equal_buffer_insert[first_insert_group - 1 - reverse_index]
                        if delete_token != insert_token:
                            unequal_reverse_index = reverse_index
                            break
                    delete_buffer.extend(equal_buffer_delete[:first_delete_group - unequal_reverse_index])
                    equal_buffer_delete = equal_buffer_delete[first_delete_group - unequal_reverse_index:]
                    insert_buffer.extend(equal_buffer_insert[:first_insert_group - unequal_reverse_index])
                    equal_buffer_insert = equal_buffer_insert[first_insert_group - unequal_reverse_index:]

                last_delete_group = -1
                last_insert_group = -1
                # FIXME: totally inefficient; should go backward
                for token_index, token in enumerate(equal_buffer_delete):
                    if isinstance(token, list):
                        last_delete_group = token_index
                for token_index, token in enumerate(equal_buffer_insert):
                    if isinstance(token, list):
                        last_insert_group = token_index

                # In theory we should always find both, but sanity check anyway
                if last_delete_group > -1 and last_insert_group > -1:
                    max_range = min(len(equal_buffer_delete) - last_delete_group, len(equal_buffer_insert) - last_insert_group)
                    unequal_index = max(1, max_range)
                    for index in range(1, max_range):
                        delete_token = equal_buffer_delete[last_delete_group + index]
                        insert_token = equal_buffer_insert[last_insert_group + index]
                        if delete_token != insert_token:
                            unequal_index = index
                            break
                    equal_buffer_delete_next = equal_buffer_delete[last_delete_group + unequal_index:]
                    equal_buffer_delete = equal_buffer_delete[:last_delete_group + unequal_index]
                    equal_buffer_insert_next = equal_buffer_insert[last_insert_group + unequal_index:]
                    equal_buffer_insert = equal_buffer_insert[:last_insert_group + unequal_index]

            if insert_buffer or delete_buffer:
                reconcile_change_groups(insert_buffer, delete_buffer, result)

            if include_insert and include_delete:
                result.extend(flatten_groups(equal_buffer_insert))
                delete_buffer.extend(equal_buffer_delete_next)
                insert_buffer.extend(equal_buffer_insert_next)
            elif include_insert:
                result.extend(expand_tokens(html2_tokens[j1:j2], equal=True))
            else:
                result.extend(expand_tokens(html1_tokens[i1:i2], equal=True))
            continue
        if (command == 'insert' or command == 'replace') and include_insert:
            ins_tokens = expand_tokens(html2_tokens[j1:j2])
            if include_delete:
                merge_change_groups(ins_tokens, insert_buffer, 'ins')
            else:
                merge_changes(ins_tokens, result, 'ins')
        if (command == 'delete' or command == 'replace') and include_delete:
            del_tokens = expand_tokens(html1_tokens[i1:i2])
            if include_insert:
                merge_change_groups(del_tokens, delete_buffer, 'del')
            else:
                merge_changes(del_tokens, result, 'del')

    reconcile_change_groups(insert_buffer, delete_buffer, result)
    return result


# TODO: merge and reconcile this with `merge_changes()`, which is 90% the same
# thing; it just outputs a flat instead of nested list.
def merge_change_groups(change_chunks, doc, tag_type=None):
    """
    Group tokens from a flat list of tokens into continuous mark-up-able
    groups. This mainly means being sensitive to HTML elements that we don't
    want tags representing changes to intersect with.

    For example, a change element shouldn't cross the boundaries of a `<p>`
    element. Instead, it should end before the `<p>`, restart again inside the
    `<p>`, and end and restart again before and after the `</p>` closing tag:

        <ins>Some</ins><p><ins>inserted</ins></p><ins>text</ins>

    While the input to this method is a flat list of tokens, the output is a
    mixed list of tokens (e.g. the `<p>`) and lists of tokens, which represent
    runs of tokens that can be fully wrapped in an element to represent changes
    (like `<ins>`). Note also that the output is not returned, but appended to
    the second argument (a document, or list of tokens, to output to). So the
    corresponding data structure to the above would be:

        Input:  ['Some','<p>','inserted','</p>','text']
        Output: [['<ins>','Some','</ins>'],
                 '<p>',
                 ['<ins>','inserted','</ins>'],
                 '</p>',
                 ['<ins>','text','</ins>']]

    The last argument, `tag_type` is the name of the tag to wrap the changes
    in, e.g. `ins`. If set to `None`, changes will still be grouped, but groups
    will not include wrapping tags.

    Parameters
    ----------
    change_chunks : list of token
        The changes to merge.
    doc : list of token
        The "document" to merge `change_chunks` into.
    tag_type : str
        The type of HTML tag to wrap the changes with.
    """
    depth = 0
    current_content = None
    group = doc
    for chunk in change_chunks:
        inline_tag = False
        inline_tag_name = None

        if chunk == '' or chunk == ' ':
            continue

        # FIXME: explicitly handle elements that can't have our markers as
        # direct children.
        if chunk[0] == '<':
            # NOTE: split first by `>` because we could have undiffable items
            # with content here, i.e. more than one tag.
            name = chunk.split('>', 1)[0].split(None, 1)[0].strip('<>/')
            # Also treat `a` tags as block in this context, because they *can*
            # contain block elements, like `h1`, etc.
            is_block = name in block_level_tags or name == 'a'

            if chunk[1] == '/':
                if depth > 0:
                    if is_block:
                        for nested_tag in current_content:
                            group.append(f'</{nested_tag}>')
                        if tag_type:
                            group.append(f'</{tag_type}>')
                        current_content = None
                        depth -= 1
                        group = doc
                        group.append(chunk)
                    else:
                        if name in current_content:
                            index = current_content.index(name)
                            current_content = current_content[index + 1:]
                            group.append(chunk)
                        else:
                            # only a malformed document should hit this case
                            # where tags aren't properly nested ¯\_(ツ)_/¯
                            for nested_tag in current_content:
                                group.append(f'</{nested_tag}>')

                            if tag_type:
                                group.append(f'</{tag_type}>')
                            # <start> not sure if we should break the group
                            # group = doc
                            # <end> not sure if we should break the group
                            group.append(chunk)
                            # <start> not sure if we should break the group
                            # group = []
                            # doc.append(group)
                            # <end> not sure if we should break the group
                            if tag_type:
                                group.append(f'<{tag_type} class="wm-diff">')

                            # other side of the malformed document case from above
                            current_content.reverse()
                            for nested_tag in current_content:
                                group.append(f'<{nested_tag}>')
                            current_content.reverse()
                else:
                    group.append(chunk)
                # There is no case for a closing tag where aren't doen with the chunk
                continue
            else:
                if is_block:
                    if depth > 0:
                        for nested_tag in current_content:
                            group.append(f'</{nested_tag}>')
                        if tag_type:
                            group.append(f'</{tag_type}>')
                        current_content = None
                        depth -= 1
                        group = doc
                    group.append(chunk)
                    continue
                else:
                    inline_tag = True
                    inline_tag_name = name

        if depth == 0:
            group = []
            doc.append(group)
            if tag_type:
                group.append(f'<{tag_type} class="wm-diff">')
            depth += 1
            current_content = []

        group.append(chunk)
        # Note the undiffable_content_tags check here. We assume tokens for
        # those tags represent a whole element, not just a start or end tag,
        # so we don't consider them "open" as part of `current_content`.
        if (inline_tag and
                (inline_tag_name not in undiffable_content_tags) and
                (inline_tag_name not in empty_tags)):
            # FIXME: track the original start tag for when we need to break
            # these elements around boundaries.
            current_content.insert(0, inline_tag_name)

    if depth > 0:
        for nested_tag in current_content:
            group.append(f'</{nested_tag}>')

        if tag_type:
            group.append(f'</{tag_type}>')
        group = doc

        current_content.reverse()
        for nested_tag in current_content:
            group.append(f'<{nested_tag}>')


TagInfo = namedtuple('TagInfo', ('name', 'open', 'source'))

@lru_cache(maxsize=1024)
def tag_info(tag_text):
    """
    Read text like `<span>` and return a `TagInfo` named tuple or None if not
    a tag.
    """
    if not tag_text.startswith('<'):
        return None
    name = tag_text.split()[0].strip('<>/')
    return TagInfo(name, not tag_text.startswith('</'), tag_text)

# TODO: rewrite this in a way that doesn't mutate the input?
def reconcile_change_groups(insert_groups, delete_groups, document):
    """
    Attempt to reconcile a list of grouped tokens (see merge_change_groups())
    that were deleted with another list of those that were inserted in roughly
    the same place, resulting in a single, merged list of tokens that displays
    the two adjacent to each other in a correctly structured DOM.

    The merged list of tokens is appended to `document`, then each list of
    tokens is cleared.

    The basic idea here is that, where the two lists of tokens diverge, we
    create speculative branches. When the tokens added to a branch result in a
    complete tree, we add it to the resulting document. Incomplete insertion
    branches are added as-is at the end (because the overall combined document
    follows the structure of the new version of the HTML, not the old), while
    incomplete deletion branches are either synthetically completed by closing
    all their open tags (if they contain meaningful changes) or are simply
    thrown out (if they do not contain meaningful changes).
    """
    logger.debug('------------------ RECONCILING ----------------------')
    logger.debug(f'  INSERT:\n  {insert_groups}\n')
    logger.debug(f'  DELETE:\n  {delete_groups}\n')
    start_index = len(document)
    insert_index = 0
    delete_index = 0
    insert_count = len(insert_groups)
    delete_count = len(delete_groups)

    insert_tag_stack = []
    delete_tag_stack = []
    delete_tag_unstack = []
    insert_buffer = []
    delete_buffer = []
    buffer = document

    while True:
        insertion = insert_index < insert_count and insert_groups[insert_index] or None
        deletion = delete_index < delete_count and delete_groups[delete_index] or None
        if not insertion and not deletion:
            break

        # Strip here because tags can have whitespace attached to the end :\
        equal_items = insertion == deletion or (
            isinstance(insertion, str) and
            isinstance(deletion, str) and
            insertion.strip() == deletion.strip())

        if equal_items and buffer is document:
            document.append(insertion)
            insert_index += 1
            delete_index += 1
        elif isinstance(deletion, list):
            buffer.extend(deletion)
            delete_index += 1
        elif isinstance(insertion, list):
            buffer.extend(insertion)
            insert_index += 1
        elif deletion:
            tag = tag_info(deletion)
            if tag:
                if tag.open:
                    if delete_tag_unstack:
                        delete_buffer.append(deletion)
                        delete_index += 1
                        active_tag = delete_tag_unstack.pop()
                        if tag.name != active_tag.name:
                            delete_tag_unstack.append(active_tag)
                            delete_tag_unstack.append(tag)
                        if not delete_tag_unstack:
                            logger.debug(f'INSERTING DELETE UNSTACK BUFFER: {delete_buffer}')
                            document.extend(delete_buffer)
                            delete_buffer.clear()
                            buffer = document
                    else:
                        buffer = delete_buffer
                        delete_tag_stack.append(tag)
                        delete_buffer.append(deletion)
                        delete_index += 1
                else:
                    if delete_tag_stack:
                        active_tag = delete_tag_stack.pop()
                        while delete_tag_stack and active_tag.name != tag.name:
                            active_tag = delete_tag_stack.pop()
                        if active_tag.name != tag.name:
                            logger.warning(f'Close tag with no corresponding open tag found in deletions ({tag})')
                            break
                        delete_buffer.append(deletion)
                        delete_index += 1
                        if not delete_tag_stack:
                            logger.debug(f'INSERTING DELETE BUFFER: {delete_buffer}')
                            document.extend(delete_buffer)
                            delete_buffer.clear()
                            buffer = document
                    else:
                        # Speculatively go the opposite direction
                        buffer = delete_buffer
                        if delete_tag_unstack:
                            active_tag = delete_tag_unstack.pop()
                            if not active_tag.open:
                                delete_tag_unstack.append(active_tag)
                                delete_tag_unstack.append(tag)
                                delete_buffer.append(deletion)
                                delete_index += 1
                            elif active_tag.open and active_tag.name == tag.name:
                                delete_buffer.append(deletion)
                                delete_index += 1
                            else:
                                logger.warning('Close tag with no corresponding open tag found in unstacking deletions')
                                break
                        else:
                            delete_tag_unstack.append(tag)
                            delete_buffer.append(deletion)
                            delete_index += 1
                            # pass
            else:
                # NOTE: not sure we can ever reach this case (unwrapped change
                # that is not an element).
                buffer.append(deletion)
                delete_index += 1

        elif insertion:
            # if we have a hanging delete buffer (with content, not just HTML
            # DOM structure), clean it up and insert it before moving on.
            # FIXME: this should not look explicitly for `<del>`
            if '<del class="wm-diff">' in delete_buffer:
                for tag in delete_tag_stack:
                    delete_buffer.append(f'</{tag[0]}>')
                document.extend(delete_buffer)
                delete_tag_stack.clear()
                delete_buffer.clear()

            tag = tag_info(insertion)
            if tag:
                if tag.open:
                    buffer = insert_buffer
                    insert_tag_stack.append(tag)
                    insert_buffer.append(insertion)
                    insert_index += 1
                else:
                    if insert_tag_stack:
                        active_tag = insert_tag_stack.pop()
                        while insert_tag_stack and active_tag.name != tag.name:
                            active_tag = insert_tag_stack.pop()
                        if active_tag.name != tag.name:
                            logger.warning('Close tag with no corresponding open tag found in insertions')
                            break
                        insert_buffer.append(insertion)
                        insert_index += 1
                        if not insert_tag_stack:
                            document.extend(insert_buffer)
                            insert_buffer.clear()
                            buffer = document
                    else:
                        # Insertions control the overall structure of the
                        # document. Don't move up a level until we are out of
                        # deletions at this level.
                        if not deletion:
                            document.append(insertion)
                            insert_index += 1

            else:
                # NOTE: not sure we can ever reach this case (unwrapped change
                # that is not an element).
                buffer.append(insertion)
                insert_index += 1

    # Add any hanging buffer of deletes that never got completed, but only if
    # it has salient changes in it.
    # FIXME: this should not look explicitly for `<del>`
    if '<del class="wm-diff">' in delete_buffer:
        for tag in delete_tag_stack:
            delete_buffer.append(f'</{tag[0]}>')
        document.extend(delete_buffer)

    document.extend(insert_buffer)

    insert_groups.clear()
    delete_groups.clear()

    result = document[start_index:]
    logger.debug(f'  RESULT:\n  {result}\n')

    return document


def flatten_groups(groups, include_non_groups=True):
    flat = []
    for item in groups:
        if isinstance(item, list):
            flat.extend(item)
        elif include_non_groups:
            flat.append(item)

    return flat


class InsensitiveSequenceMatcher(difflib.SequenceMatcher):
    """
    Acts like SequenceMatcher, but tries not to find very small equal
    blocks amidst large spans of changes
    """

    threshold = 2

    def get_matching_blocks(self):
        size = min(len(self.a), len(self.b))
        threshold = min(self.threshold, size / 4)
        actual = difflib.SequenceMatcher.get_matching_blocks(self)
        return [item for item in actual
                if item[2] > threshold
                or not item[2]]


def get_diff_styles():
    colors = get_color_palette()
    # Unset local `<ins>`/`<del>` styling on the page that might clash with
    # our diff elements. Note that `all: unset` has browser bugs that are
    # problematic (e.g. https://bugs.webkit.org/show_bug.cgi?id=158782) so
    # we need to use a list of specific properties we're concerned about
    # instead. (It can also cause the contents of `<style>` and `<script>`
    # tags to be rendered on the page, which is also bad.)
    return f'''
        ins.wm-diff, del.wm-diff {{
            display: unset;
            visibility: unset;
            opacity: 1;
            clip: auto;
            text-decoration: unset;
            color: inherit;
        }}
        ins.wm-diff, ins.wm-diff > * {{
            background-color: {colors['differ_insertion']} !important;
        }}
        del.wm-diff, del.wm-diff > * {{
            background-color: {colors['differ_deletion']} !important;
        }}
        script {{display: none !important;}}'''


UPDATE_CONTRAST_SCRIPT = """
    (function () {
        // Update the text color of change elements to ensure a readable level
        // of contrast with the background color
        function parseColor (colorString) {
            const components = colorString.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)/);
            return {
                red: Number(components[1]),
                green: Number(components[2]),
                blue: Number(components[3]),
                alpha: components[4] == null ? 1 : Number(components[4])
            };
        }

        function normalizeChannel (integerValue) {
            const scaled = integerValue / 255;
            if (scaled < 0.03928) {
                return scaled / 12.92;
            }
            return Math.pow((scaled + 0.055) / 1.055, 2.4);
        }

        // See https://www.w3.org/TR/WCAG/#relativeluminancedef
        function srgbRelativeLuminance (color) {
            return 0.2126 * normalizeChannel(color.red)
                + 0.7152 * normalizeChannel(color.green)
                + 0.0722 * normalizeChannel(color.blue);
        }

        // See https://www.w3.org/TR/WCAG/#contrast-ratiodef
        function contrastRatio (a, b) {
            const luminanceA = srgbRelativeLuminance(a) + 0.05;
            const luminanceB = srgbRelativeLuminance(b) + 0.05;
            return Math.max(luminanceA, luminanceB) / Math.min(luminanceA, luminanceB);
        }

        document.querySelectorAll('ins,del').forEach(element => {
            const color = parseColor(getComputedStyle(element).color);
            const background = parseColor(getComputedStyle(element).backgroundColor);
            if (contrastRatio(color, background) < 4) {
                element.style.color = '#000';
            }
        });
    })();
"""
