"""
Experimental External Diffs
---------------------------

The functions in ``web_monitoring_diff.experimental`` wrap diff algorithms
available from other repositories that we consider relatively experimental or
unproven. They may be new and still have lots of edge cases, may not be
publicly available via PyPI or another package server, or may have any number
of other issues.

They are not installed by default, so calling them may raise an exception. To
install them, use pip::

    $ pip install -r requirements-experimental.txt

Experimental modules are typically named by the package they wrap, and can be
called with a function named ``diff``. For example:

>>> from web_monitoring_diff.experimental import htmldiffer
>>> htmldiffer.diff("<some>html</some>", "<some>other html</some>")
"""
