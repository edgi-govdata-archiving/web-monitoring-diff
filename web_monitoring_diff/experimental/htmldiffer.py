from htmldiffer.diff import HTMLDiffer
from ..utils import get_color_palette, insert_style


def diff(a_text, b_text):
    """
    Wraps the ``htmldiffer`` package with the standard arguments and output
    format used by all diffs in ``web-monitoring-diff``.

    ``htmldiffer`` is mainly developed as part of Perma CC (https://perma.cc/),
    a web archival service, and the Harvard Library Innovation Lab. At a high
    level, it parses the text and tags of a page into one list and uses
    Python's built-in :class:`difflib.SequenceMatcher` to compare them. This
    contrasts with ``web_monitoring_diff.html_render_diff``, where it is
    primarily the *text* of the page being diffed, with additional content from
    from the surrounding tags added in as appropriate (tags there are still
    kept in order to rebuild the page structure after diffing the text).

    While ``htmldiffer`` is available on PyPI, the public release hasn't been
    updated in quite some time. Its authors recommend installing via git
    instead of PyPI::

        $ pip install git+https://github.com/anastasia/htmldiffer@develop

    You can also install all experimental differs with::

        $ pip install -r requirements-experimental.txt

    NOTE: this differ parses HTML in pure Python and can be very slow when
    using the standard, CPython interpreter. If you plan to use it in a
    production or performance-sensitive environment, consider using PyPy
    or another, more optimized interpreter.

    Parameters
    ----------
    a_text : string
        Source HTML of one document to compare
    b_text : string
        Source HTML of the other document to compare

    Returns
    -------
    dict
    """
    color_palette = get_color_palette()
    css = f'''
.htmldiffer_insert {{text-decoration : none; background-color:
    {color_palette['differ_insertion']};}}
.htmldiffer_delete {{text-decoration : none; background-color:
    {color_palette['differ_deletion']};}}
.htmldiffer_insert * {{text-decoration : none; background-color:
    {color_palette['differ_insertion']};}}
.htmldiffer_delete * {{text-decoration : none; background-color:
    {color_palette['differ_deletion']};}}
    '''

    d = HTMLDiffer(a_text, b_text).combined_diff
    # TODO Count number of changes.
    return {'diff': insert_style(d, css)}
