"""Shared Streamlit render bits used by more than one page."""
from __future__ import annotations

from prismGui.app._streamlit import st
from prismGui.app.view_data import _issue_rows


def _render_issues(issues, *, empty_msg: str = "No issues.") -> None:
    if not issues:
        st.caption(empty_msg)
        return
    n_err = sum(1 for i in issues if i.severity.value == "error")
    n_warn = sum(1 for i in issues if i.severity.value == "warning")
    st.caption(f"{n_err} error(s), {n_warn} warning(s)")
    st.dataframe(_issue_rows(issues), use_container_width=True, hide_index=True)
