"""Streamlit session-state facade (constructed only by the composition root)."""
from __future__ import annotations

from prismGui.app._streamlit import st
from prismGui.domain.run_config import ModeSelection


# =============================================================================
# Streamlit-backed session state (the app's SessionState implementation)
# =============================================================================

class StreamlitSessionState:
    """``services.SessionState`` over ``st.session_state`` — the ONLY code that knows the
    session-state keys. Constructed inside ``main()`` (touches ``st``)."""

    _BASELINE = "prism_baseline"
    _DRAFT = "prism_draft"
    _SCENARIOS = "prism_scenarios"                 # dict[scenario_id, Scenario], insertion-ordered
    _CURRENT_SCHEDULE = "prism_current_schedule"   # scenario_id, or None == the baseline
    _RUN_CONFIG = "prism_run_config"
    _RESULTS = "prism_results"
    _SELECTED = "prism_selected_id"
    _SOURCE_KEY = "prism_source_key"      # signature of the last file-reloaded source
    _MODE_SELECTIONS = "prism_mode_selections"   # tuple[ModeSelection, ...] merged into RunConfig
    _PLAN_OF_RECORD = "prism_plan_of_record"     # Optional[PlanOfRecord]: adopted-replan chain

    def __init__(self) -> None:
        st.session_state.setdefault(self._BASELINE, None)
        st.session_state.setdefault(self._DRAFT, None)
        st.session_state.setdefault(self._SCENARIOS, {})
        st.session_state.setdefault(self._CURRENT_SCHEDULE, None)
        st.session_state.setdefault(self._RUN_CONFIG, None)
        st.session_state.setdefault(self._RESULTS, {})
        st.session_state.setdefault(self._SELECTED, None)
        st.session_state.setdefault(self._SOURCE_KEY, None)
        st.session_state.setdefault(self._MODE_SELECTIONS, ())
        st.session_state.setdefault(self._PLAN_OF_RECORD, None)

    def get_baseline(self):
        return st.session_state[self._BASELINE]

    def set_baseline(self, plan) -> None:
        st.session_state[self._BASELINE] = plan

    def get_draft(self):
        return st.session_state[self._DRAFT]

    def set_draft(self, draft) -> None:
        st.session_state[self._DRAFT] = draft

    def clear_draft(self) -> None:
        st.session_state[self._DRAFT] = None

    # Streamlit-only: not part of the SessionState Protocol. Tracks which source the
    # session baseline was last seeded from, so the top-of-main file reload overwrites
    # the baseline only when the selection changes — letting a committed edit survive
    # Streamlit's top-to-bottom rerun.
    def get_source_key(self):
        return st.session_state[self._SOURCE_KEY]

    def set_source_key(self, key) -> None:
        st.session_state[self._SOURCE_KEY] = key

    # Streamlit-only: not part of the SessionState Protocol. The run-time execution-mode
    # picks (task_id -> mode_name) merged into the RunConfig at Run — a run-config concern,
    # not a scenario delta (keeping it on RunConfig preserves the run-config hash / freshness).
    def get_mode_selections(self) -> tuple[ModeSelection, ...]:
        return tuple(st.session_state[self._MODE_SELECTIONS])

    def set_mode_selections(self, selections) -> None:
        st.session_state[self._MODE_SELECTIONS] = tuple(selections)

    def get_scenario(self):
        """The scenario the current-schedule pointer names, or None (== the baseline)."""
        current = st.session_state[self._CURRENT_SCHEDULE]
        if current is None:
            return None
        return st.session_state[self._SCENARIOS].get(current)

    def set_scenario(self, scenario) -> None:
        """Shim: None detaches to the baseline (keeps stored scenarios); a scenario is
        stored (add-or-update) and made current."""
        if scenario is None:
            st.session_state[self._CURRENT_SCHEDULE] = None
            return
        st.session_state[self._SCENARIOS][scenario.scenario_id] = scenario
        st.session_state[self._CURRENT_SCHEDULE] = scenario.scenario_id

    def list_scenarios(self) -> tuple:
        return tuple(st.session_state[self._SCENARIOS].values())

    def add_scenario(self, scenario) -> None:
        st.session_state[self._SCENARIOS][scenario.scenario_id] = scenario

    def remove_scenario(self, scenario_id: str) -> None:
        st.session_state[self._SCENARIOS].pop(scenario_id, None)

    def get_current_scenario_id(self):
        return st.session_state[self._CURRENT_SCHEDULE]

    def set_current_scenario_id(self, scenario_id) -> None:
        st.session_state[self._CURRENT_SCHEDULE] = scenario_id

    def get_run_config(self):
        return st.session_state[self._RUN_CONFIG]

    def set_run_config(self, run_config) -> None:
        st.session_state[self._RUN_CONFIG] = run_config

    def list_run_results(self) -> tuple:
        return tuple(st.session_state[self._RESULTS].values())

    def add_run_result(self, result) -> None:
        st.session_state[self._RESULTS][result.run_id] = result

    def get_run_result(self, run_id: str):
        return st.session_state[self._RESULTS].get(run_id)

    def get_selected_result_id(self):
        return st.session_state[self._SELECTED]

    def set_selected_result_id(self, run_id) -> None:
        st.session_state[self._SELECTED] = run_id

    def get_plan_of_record(self):
        return st.session_state[self._PLAN_OF_RECORD]

    def set_plan_of_record(self, plan_of_record) -> None:
        st.session_state[self._PLAN_OF_RECORD] = plan_of_record
