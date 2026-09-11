"""Shared guarded Streamlit import for the app (view) layer.

Every ``app`` view module imports ``st`` from here so the Streamlit guard lives in exactly one
place. Headless (Streamlit absent) ``st`` is ``None`` and ``_HAS_STREAMLIT`` is ``False``; since
all ``st.*`` use is inside function bodies, importing any view module headless stays safe.
"""
from __future__ import annotations

try:  # guarded: the module must import even where Streamlit is not installed.
    import streamlit as st
    _HAS_STREAMLIT = True
except ModuleNotFoundError:  # pragma: no cover - exercised only in a Streamlit-less env
    st = None  # type: ignore[assignment]
    _HAS_STREAMLIT = False
