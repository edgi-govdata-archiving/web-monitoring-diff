import htmltreediff
from ..utils import get_color_palette, insert_style


def diff(a_text, b_text):
    """
    Wraps the ``htmltreediff`` package with the standard arguments and output
    format used by all diffs in ``web-monitoring-diff``.

    ``htmltreediff`` parses HTML documents into an XML DOM and attempts to diff
    the document *structures*, rather than look at streams of tags & text
    (like ``htmldiffer``) or the readable text content of the HTML (like
    ``web_monitoring_diff.html_render_diff``). Because of this, it can give
    extremely accurate and detailed information for documents that are very
    similar, but its output gets complicated or opaque as the two documents
    diverge in structure. It can also be very slow.

    In practice, we've found that many real-world web pages vary their
    structure enough (over periods as short as a few months) to reduce the
    value of this diff. It's best used for narrowly-defined scenarios like:

    - Comparing versions of a page that are very similar, often at very close
      points in time.

    - Comparing XML structures you can expect to be very similar, like XML API
      responses, RSS documents, etc.

    - Comparing two documents that were generated from the same template with
      differing underlying data. (Assuming the template is fairly rigid, and
      does not leave too much document structure up to the underlying data.)

    ``htmltreediff`` is no longer under active development; we maintain a fork
    with minimal fixes and Python 3 support. It is not available on PyPI, so
    you must install via git::

        $ pip install git+https://github.com/danielballan/htmltreediff@customize

    You can also install all experimental differs with::

        $ pip install -r requirements-experimental.txt

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
diffins {{text-decoration : none; background-color:
    {color_palette['differ_insertion']};}}
diffdel {{text-decoration : none; background-color:
    {color_palette['differ_deletion']};}}
diffins * {{text-decoration : none; background-color:
    {color_palette['differ_insertion']};}}
diffdel * {{text-decoration : none; background-color:
    {color_palette['differ_deletion']};}}
    '''
    d = htmltreediff.diff(a_text, b_text,
                          ins_tag='diffins', del_tag='diffdel',
                          pretty=True)
    # TODO Count number of changes.
    return {'diff': insert_style(d, css)}
