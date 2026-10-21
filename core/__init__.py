"""
SurfGambit engine.

Pure Python, standard library only, and free of Tk imports so it can be used
headlessly (see tests/run.py):

    core.dom     HTML tokenizer, tree builder, DOM node
    core.style   CSS tokenizer, selectors, cascade
    core.layout  box tree and layout algorithms
    core.net     HTTP client and RFC 6265 cookie jar
    core.js      JavaScript subset bound to the DOM
"""

__version__ = "3.0"
