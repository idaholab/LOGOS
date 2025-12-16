# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

import os
import sys
import sphinx_rtd_theme
from docutils import nodes
from docutils.parsers.rst import roles
sys.path.insert(0, os.path.abspath('../../../'))

project = 'LOGOS'
copyright = 'Copyright 2020, Battelle Energy Alliance, LLC ALL RIGHTS RESERVED'
author = 'Congjian Wang; Diego Mandelli'
release = '1.0.0'

today = ''
today_fmt = '%B %d, %Y'
# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = ['sphinx.ext.intersphinx',
	'sphinx.ext.autodoc',
	'sphinx.ext.doctest',
	'sphinx.ext.todo',
	"sphinx.ext.autodoc.typehints",
	"sphinx.ext.mathjax",
    # "sphinx.ext.autosummary",
	"nbsphinx",  # <- For Jupyter Notebook support
	"sphinx.ext.napoleon",  # <- For Google style docstrings
	"sphinx.ext.imgmath",
	"sphinx.ext.viewcode",
	# 'autoapi.extension',
    'sphinx_copybutton',
    'sphinxcontrib.bibtex',]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

source_suffix = [".rst", ".md"]
# autoapi_dirs = ['../../../src']

# autoapi_ignore = ['*/contrib/*', '*/Testers/*']

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']

# -- NBSphinx options
# Do not execute the notebooks when building the docs
nbsphinx_execute = "never"

autodoc_inherit_docstrings = False

numfig = True
numfig_format = {
    'figure': 'Figure %s',
    'table': 'Table %s',
    'code-block': 'Listing %s',
}


# Optional: automatically number displayed math
math_number_all = True

# Optionally customize the format of equation numbers
math_eqref_format = "Eq. {number}"

bibtex_bibfiles = ['refs.bib']   # path(s) to your .bib file(s)
bibtex_default_style = 'unsrt'   # unsrt, plain, alpha, etc.

latex_engine = "xelatex"
latex_elements = {
    'printindex': r'\def\twocolumn[#1]{#1}\printindex',
    # "extraclassoptions": "landscape" # option to make it landscape to avoid line overflow
}


mathjax3_config = {
    "tex": {
        "tags": "ams",          # or "all"
        "tagSide": "right",     # <— this is what you want
        "tagIndent": "0.8em",
    }
}


# # Define custom field role

# def xml_node_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
#     node = nodes.literal(text, f"<{text}>")  # how to render
#     return [node], []

# def xml_str_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
#     node = nodes.literal(text, f"{text}")  # how to render
#     return [node], []

# def setup(app):
#     roles.register_local_role('xmlNode', xml_node_role)
#     roles.register_local_role('xmlString', xml_str_role)



def _literal_span(text, classes=None, prefix="", suffix=""):
    """Helper to build a literal-like inline node with optional wrappers."""
    shown = f"{prefix}{text}{suffix}"
    return nodes.literal(shown, shown, classes=classes or [])

def todo_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    # (text) in ital red -> we just mark with class; CSS will color/style
    node = nodes.inline(f"({text})", f"({text})", classes=["todo-inline"])
    return [node], []

def xml_attr_required_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    node = _literal_span(text, classes=["xml-attr-required"])
    return [node], []

def xml_attr_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    node = _literal_span(text, classes=["xml-attr"])
    return [node], []

def xml_node_required_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    node = _literal_span(text, classes=["xml-node-required"], prefix="<", suffix=">")
    return [node], []

def xml_node_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    node = _literal_span(text, classes=["xml-node"], prefix="<", suffix=">")
    return [node], []

def xml_string_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    node = _literal_span(text, classes=["xml-string"], prefix="'", suffix="'")
    return [node], []

def xml_desc_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    node = nodes.emphasis(text, text, classes=["xml-desc"])
    return [node], []

def nb_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    # just "Note:" prefix; text can be empty or some label
    label = text or "Note"
    node = nodes.strong(f"{label}:", f"{label}:", classes=["nb-label"])
    return [node], []

def setup(app):
    roles.register_local_role("todo", todo_role)
    roles.register_local_role("xmlAttrRequired", xml_attr_required_role)
    roles.register_local_role("xmlAttr", xml_attr_role)
    roles.register_local_role("xmlNodeRequired", xml_node_required_role)
    roles.register_local_role("xmlNode", xml_node_role)
    roles.register_local_role("xmlString", xml_string_role)
    roles.register_local_role("xmlDesc", xml_desc_role)
    roles.register_local_role("nb", nb_role)
