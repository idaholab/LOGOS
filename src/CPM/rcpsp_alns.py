# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
rcpsp_alns.py — Adaptive Large Neighbourhood Search for RCPSP

Implements ALNS for the Resource-Constrained Project Scheduling Problem using
the `alns` library (Wouda & Lan, 2023).  Solution states use the Activity List
representation (precedence-feasible orderings) and the serial SGS decoder from
``pert.calculateSerialScheduleWithResources``, matching the fitness function
used in ``ga.py``.

References
----------
Wouda, N.A., and L. Lan (2023).  ALNS: a Python implementation.
  *Journal of Open Source Software*, 8(81): 5028.

Shaw, P. (1998). Using constraint programming and local search methods to
  solve vehicle routing problems. *CP*, 417–431.

Valls, V., Ballestin, F., and Quintanilla, S. (2005).  Justification and
  RCPSP: A technique that pays.  *European Journal of Operational Research*,
  165(2): 375–386.

Solution Representation
-----------------------
An ``RCPSPState`` wraps a **precedence-feasible activity list** (a list of
``Activity`` objects in scheduling order) plus an ``unscheduled`` list that
holds activities temporarily removed by a destroy operator.  A state is
*complete* when ``unscheduled`` is empty; only complete states expose a valid
``objective()`` value.

Destroy Operators
-----------------
``most_mobile``
    Remove the top-k activities by total float (slack = LF − EF from the
    unconstrained CPM solution).  Highly mobile activities are the most
    interchangeable and their removal creates the widest repair space.

``segment``
    Remove a randomly located contiguous block of k activities from the
    non-dummy part of the ordering.  Disrupts local neighbourhood structure
    while keeping the rest of the sequence intact.

``random``
    Remove k activities chosen uniformly at random from non-dummy activities.
    Provides unbiased exploration when the other operators are overspecialised.

Repair Operators
----------------
``random_insert``
    Re-insert each unscheduled activity at a uniformly random position within
    its feasible window [lo, hi], where lo = 1 + max(position of predecessors
    in current partial ordering) and hi = min(position of successors).
    Activities are processed in topological order so that predecessors are
    inserted before their dependents.

``greedy_insert``
    Same as ``random_insert`` but always chooses position ``lo`` (earliest
    feasible), producing a left-justified, greedy repair.  Tends to reduce
    makespan at the cost of population diversity.

Acceptance Criteria
-------------------
``hill_climbing``
    Only accept candidate solutions that strictly improve the current solution.
    Zero-temperature behaviour; fastest convergence but susceptible to local
    optima.

``record_to_record``
    Accept if the candidate's objective is within a linearly decreasing
    threshold of the best-known solution (Record-to-Record Travel, Dueck 1993).
    Auto-fitted via ``RecordToRecordTravel.autofit``.

``simulated_annealing``
    Accept with probability exp(−Δ/T) where T decreases geometrically from
    ``start_temperature`` to ``end_temperature`` over ``n_iter`` iterations.

Operator Selection
------------------
``SegmentedRouletteWheel`` from the `alns` library partitions iterations into
fixed-length segments (default 100), tracks cumulative reward scores per
operator within each segment, then updates operator weights using a convex
combination with decay factor θ at segment boundaries.  Reward scores follow
the standard ALNS convention: [w_best, w_better, w_accepted, w_rejected].

Requirements
------------
    pip install alns
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from alns import ALNS
    from alns.accept import HillClimbing, SimulatedAnnealing, RecordToRecordTravel
    from alns.select import SegmentedRouletteWheel
    from alns.stop import MaxIterations
except ImportError as _alns_err:
    raise ImportError(
        "alns is required by rcpsp_alns.py.  Install it with:  pip install alns"
    ) from _alns_err

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Priority rules used to seed the initial solution (best of these is chosen).
# ---------------------------------------------------------------------------
SEED_PRIORITY_RULES: List[str] = [
    'es', 'ef', 'ls', 'lf', 'duration', 'random',
    'mts', 'mtp', 'grpw', 'grd', 'rr', 'avgrr',
    'maxrr', 'minrr', 'irsm', 'wcs', 'acs',
    'mehh_8000_b', 'mehh_3375_b', 'mehh_1000_b', 'mehh_125_b', 'gphh_b',
]

# Default ALNS reward weights: [new best, better than current, accepted, rejected]
DEFAULT_WEIGHTS: List[int] = [25, 5, 1, 0]


# =========================================================================== #
# Solution State                                                               #
# =========================================================================== #

class RCPSPState:
    """
    ALNS solution state for the RCPSP.

    A *complete* state holds a precedence-feasible ordering of **all**
    activities (``unscheduled`` is empty).  A *destroyed* state has some
    activities moved to ``unscheduled`` and cannot be evaluated.

    Parameters
    ----------
    pert : Pert
        Fully initialised ``Pert`` object shared across all states.
    ordering : list of Activity
        Precedence-feasible ordering of scheduled activities.
    unscheduled : list of Activity, optional
        Activities not yet in the ordering (empty for complete states).
    """

    def __init__(
        self,
        pert: Any,
        ordering: List[Any],
        unscheduled: Optional[List[Any]] = None,
    ) -> None:
        self._pert = pert
        self.ordering: List[Any] = list(ordering)
        self.unscheduled: List[Any] = list(unscheduled) if unscheduled else []
        self._obj_cache: Optional[float] = None

    # ------------------------------------------------------------------ #

    def objective(self) -> float:
        """
        Evaluate the schedule via Serial SGS and return the project duration.

        Raises ``ValueError`` if the state is incomplete (destroyed).
        Result is cached — subsequent calls are free.
        """
        if self.unscheduled:
            raise ValueError(
                "Cannot evaluate an incomplete (destroyed) state: "
                f"{len(self.unscheduled)} activities still unscheduled."
            )
        if self._obj_cache is None:
            out = self._pert.calculateSerialScheduleWithResources(
                _ordered=self.ordering
            )
            self._obj_cache = float(out['scheduled_duration'] - 2)
        return self._obj_cache

    # ------------------------------------------------------------------ #

    def invalidate_cache(self) -> None:
        """Mark the cached objective as stale (call after modifying ``ordering``)."""
        self._obj_cache = None

    def copy(self) -> 'RCPSPState':
        """Return a lightweight copy suitable for use in destroy/repair operators."""
        s: RCPSPState = object.__new__(RCPSPState)
        s._pert = self._pert          # pylint: disable=protected-access
        s.ordering = list(self.ordering)
        s.unscheduled = list(self.unscheduled)
        s._obj_cache = self._obj_cache  # pylint: disable=protected-access
        return s

    def __repr__(self) -> str:
        obj = f"{self._obj_cache:.2f}" if self._obj_cache is not None else "?"
        return (
            f"RCPSPState(obj={obj}, "
            f"scheduled={len(self.ordering)}, unscheduled={len(self.unscheduled)})"
        )


# =========================================================================== #
# ALNS Optimiser                                                               #
# =========================================================================== #

class RCPSPAdaptiveLNS:
    """
    Adaptive Large Neighbourhood Search optimiser for RCPSP.

    Uses the Activity List representation with the Serial SGS decoder
    (``pert.calculateSerialScheduleWithResources``).  Operator selection is
    handled by ``SegmentedRouletteWheel`` from the `alns` library so the
    algorithm automatically learns which destroy/repair combinations work best.

    Parameters
    ----------
    pert : Pert
        A fully initialised ``Pert`` object with ``generateInfo()`` called.
    n_iter : int
        Total number of ALNS iterations.
    destroy_fraction : float
        Fraction of non-dummy activities removed per destroy step (0, 1).
        E.g. ``0.25`` removes 25 % of activities at each iteration.
    weights : list of int, optional
        Reward scores ``[w_best, w_better, w_accepted, w_rejected]`` for the
        ``SegmentedRouletteWheel``.  Defaults to ``[25, 5, 1, 0]``.
    theta : float
        Segment-decay factor for ``SegmentedRouletteWheel`` (0, 1).
        Higher values retain more historical weight across segments.
    seg_length : int
        Number of iterations per weight-update segment.
    seed : int
        RNG seed for reproducibility.
    accept : str
        Acceptance criterion.  Choices:

        * ``'hill_climbing'``        — only accept improvements (default)
        * ``'record_to_record'``     — accept within a decreasing gap of best
        * ``'simulated_annealing'``  — temperature-based acceptance
    rrt_percent_gap : float
        For ``'record_to_record'``: initial tolerance as a fraction of the
        initial objective (e.g. ``0.02`` = 2 %).  Decreases linearly to 0.
    sa_start_temp : float, optional
        For ``'simulated_annealing'``: start temperature.  Defaults to
        ``0.05 * initial_objective``.
    sa_end_temp : float, optional
        For ``'simulated_annealing'``: end temperature.  Defaults to
        ``0.001 * initial_objective``.
    destroy_ops : list of str, optional
        Subset of destroy operators to use.  Defaults to all three:
        ``['most_mobile', 'segment', 'random']``.
    repair_ops : list of str, optional
        Subset of repair operators to use.  Defaults to both:
        ``['random_insert', 'greedy_insert']``.
    verbose : bool
        Print seeding table, per-run summary, and operator usage stats.
    """

    _DESTROY_METHODS: Dict[str, str] = {
        'most_mobile': '_destroy_most_mobile',
        'segment':     '_destroy_segment',
        'random':      '_destroy_random',
    }
    _REPAIR_METHODS: Dict[str, str] = {
        'random_insert':  '_repair_random_insert',
        'greedy_insert':  '_repair_greedy_insert',
    }

    def __init__(
        self,
        pert: Any,
        n_iter: int = 1000,
        destroy_fraction: float = 0.25,
        weights: Optional[List[int]] = None,
        theta: float = 0.8,
        seg_length: int = 100,
        seed: int = 42,
        accept: str = 'hill_climbing',
        rrt_percent_gap: float = 0.02,
        sa_start_temp: Optional[float] = None,
        sa_end_temp: Optional[float] = None,
        destroy_ops: Optional[List[str]] = None,
        repair_ops: Optional[List[str]] = None,
        verbose: bool = True,
    ) -> None:
        destroy_ops = destroy_ops or list(self._DESTROY_METHODS)
        repair_ops  = repair_ops  or list(self._REPAIR_METHODS)

        for op in destroy_ops:
            if op not in self._DESTROY_METHODS:
                raise ValueError(
                    f"Unknown destroy operator '{op}'. "
                    f"Choose from: {list(self._DESTROY_METHODS)}"
                )
        for op in repair_ops:
            if op not in self._REPAIR_METHODS:
                raise ValueError(
                    f"Unknown repair operator '{op}'. "
                    f"Choose from: {list(self._REPAIR_METHODS)}"
                )
        if accept not in ('hill_climbing', 'record_to_record', 'simulated_annealing'):
            raise ValueError(
                f"Unknown accept criterion '{accept}'. "
                "Choose from: 'hill_climbing', 'record_to_record', "
                "'simulated_annealing'."
            )

        self.pert = pert
        self.n_iter = n_iter
        self.destroy_fraction = destroy_fraction
        self.weights = weights or list(DEFAULT_WEIGHTS)
        self.theta = theta
        self.seg_length = seg_length
        self.accept_name = accept
        self.rrt_percent_gap = rrt_percent_gap
        self.sa_start_temp = sa_start_temp
        self.sa_end_temp = sa_end_temp
        self.destroy_ops = destroy_ops
        self.repair_ops = repair_ops
        self.verbose = verbose

        self._rng = np.random.default_rng(seed)

        # Dummy activity handles
        self._start: Any = pert.startActivity
        self._end: Any = pert.endActivity
        self._dummies = {a for a in (self._start, self._end) if a is not None}

        # Read total float directly from pert.infoDict["slack"] which is
        # already computed by pert.calculateSlack() as lf − ef.
        self._slack: Dict[Any, float] = {
            act: info.get('slack', 0.0)
            for act, info in pert.infoDict.items()
            if info is not None
        }

        # Precompute transitive successors for each activity.
        # Used by _insert_feasible to compute a correct hi bound even when
        # all direct successors of an activity have been removed by destroy.
        all_activities = [a for a in pert.infoDict if pert.infoDict[a] is not None]
        topo_order = self._topo_sort(all_activities)
        self._trans_successors: Dict[Any, set] = {}
        for a in reversed(topo_order):
            direct: set = set(pert.forwardDict.get(a, []))
            trans: set = set()
            for s in direct:
                trans |= self._trans_successors.get(s, set())
            self._trans_successors[a] = direct | trans

    # ====================================================================== #
    # Initial solution helpers                                                 #
    # ====================================================================== #

    def _ordering_from_rule(self, rule: str) -> List[Any]:
        """Return a precedence-feasible Activity list from a named priority rule."""
        all_acts = list(self.pert.forwardDict.keys())
        raw = self.pert.priority_calculation(all_acts, priority_rule=rule)
        if raw and isinstance(raw[0], tuple):
            return [a for (a, _, _) in raw]
        return list(raw)

    def _create_initial_solution(self) -> Tuple['RCPSPState', Dict[str, float]]:
        """
        Evaluate all seed priority rules and return the best as initial state.

        Returns
        -------
        best_state : RCPSPState
        seed_info : dict mapping rule name → duration (h)
        """
        seed_info: Dict[str, float] = {}
        best_state: Optional[RCPSPState] = None
        best_dur = float('inf')

        for rule in SEED_PRIORITY_RULES:
            try:
                self.pert.priorities = None
                ordering = self._ordering_from_rule(rule)
                state = RCPSPState(self.pert, ordering)
                dur = state.objective()
                seed_info[rule] = dur
                if dur < best_dur:
                    best_dur = dur
                    best_state = state.copy()
            except (ValueError, KeyError, RuntimeError) as exc:
                logger.debug("Seed rule '%s' failed: %s", rule, exc)

        if best_state is None:
            ordering = self._ordering_from_rule('random')
            best_state = RCPSPState(self.pert, ordering)

        return best_state, seed_info

    # ====================================================================== #
    # Internal helpers                                                         #
    # ====================================================================== #

    def _non_dummy(self, ordering: List[Any]) -> List[Any]:
        """Return only non-dummy activities from the ordering."""
        return [a for a in ordering if a not in self._dummies]

    def _topo_sort(self, activities: List[Any]) -> List[Any]:
        """
        Return ``activities`` in topological order (predecessors first).

        Uses iterative DFS to avoid recursion-limit issues on large instances.
        Only considers predecessor edges among the supplied activity set.
        """
        act_set = set(activities)
        result: List[Any] = []
        visited: set = set()

        def dfs(a: Any) -> None:
            stack = [(a, False)]
            while stack:
                node, processed = stack.pop()
                if processed:
                    if node not in visited:
                        visited.add(node)
                        result.append(node)
                    continue
                if node in visited or node not in act_set:
                    continue
                stack.append((node, True))
                for pred in self.pert.backwardDict.get(node, []):
                    if pred in act_set and pred not in visited:
                        stack.append((pred, False))

        for a in activities:
            dfs(a)

        return result

    def _insert_feasible(
        self,
        act: Any,
        ordering: List[Any],
        rng: Optional[np.random.Generator],
        greedy: bool,
    ) -> None:
        """
        Insert ``act`` into ``ordering`` (in-place) at a feasible position.

        The feasible window [lo, hi] is:
          lo = 1 + max position of predecessors of ``act`` in ordering
               (0 if no predecessors currently in ordering)
          hi = min position of successors of ``act`` in ordering
               (len(ordering) if no successors currently in ordering)

        Insertion at position ``p`` places ``act`` before the element
        currently at index ``p``.  ``greedy=True`` always inserts at ``lo``;
        otherwise a uniform random position in [lo, hi] is used.
        """
        pos_of: Dict[Any, int] = {a: p for p, a in enumerate(ordering)}

        preds = [pr for pr in self.pert.backwardDict.get(act, []) if pr in pos_of]
        lo = (max(pos_of[pr] for pr in preds) + 1) if preds else 0

        all_succs = self._trans_successors.get(act, set())
        succs = [sc for sc in ordering if sc in all_succs]
        hi = (min(pos_of[sc] for sc in succs)) if succs else len(ordering)

        # Safety clamp — should not trigger on valid instances
        lo = max(0, lo)
        hi = max(lo, min(len(ordering), hi))

        if greedy or rng is None:
            pos = lo
        else:
            pos = int(rng.integers(lo, hi + 1))

        ordering.insert(pos, act)

    # ====================================================================== #
    # Destroy operators                                                        #
    # ====================================================================== #

    def _destroy_most_mobile(
        self, state: 'RCPSPState', _rng: np.random.Generator
    ) -> 'RCPSPState':
        """
        Remove the k most mobile (highest total float) non-dummy activities.

        Total float is taken from the unconstrained CPM solution stored in
        ``pert.infoDict`` and precomputed at construction time, so this
        operator costs O(n log n) per call.  The RNG argument is accepted to
        match the ALNS operator protocol but is not used (selection is
        deterministic given the precomputed slack values).
        """
        destroyed = state.copy()
        non_dummy = self._non_dummy(destroyed.ordering)
        if not non_dummy:
            return destroyed

        k = max(1, int(len(non_dummy) * self.destroy_fraction))
        to_remove = sorted(
            non_dummy, key=lambda a: self._slack.get(a, 0.0), reverse=True
        )[:k]
        remove_set = set(to_remove)

        destroyed.ordering = [a for a in destroyed.ordering if a not in remove_set]
        destroyed.unscheduled = to_remove
        destroyed.invalidate_cache()
        return destroyed

    def _destroy_segment(
        self, state: 'RCPSPState', rng: np.random.Generator
    ) -> 'RCPSPState':
        """
        Remove a random contiguous segment of k non-dummy activities.

        A starting index within the non-dummy positions is drawn uniformly;
        the following k positions are removed together, preserving contiguity
        in the original ordering.
        """
        destroyed = state.copy()
        non_dummy_positions = [
            p for p, a in enumerate(destroyed.ordering) if a not in self._dummies
        ]
        if not non_dummy_positions:
            return destroyed

        n_nd = len(non_dummy_positions)
        k = max(1, int(n_nd * self.destroy_fraction))
        k = min(k, n_nd)

        start_idx = int(rng.integers(0, n_nd - k + 1))
        remove_positions = set(non_dummy_positions[start_idx: start_idx + k])

        to_remove = [destroyed.ordering[p] for p in sorted(remove_positions)]
        destroyed.ordering = [
            a for p, a in enumerate(destroyed.ordering) if p not in remove_positions
        ]
        destroyed.unscheduled = to_remove
        destroyed.invalidate_cache()
        return destroyed

    def _destroy_random(
        self, state: 'RCPSPState', rng: np.random.Generator
    ) -> 'RCPSPState':
        """
        Remove k randomly chosen non-dummy activities (uniform, without replacement).

        Provides unbiased diversification when the other operators have
        converged to a sub-region of the search space.
        """
        destroyed = state.copy()
        non_dummy = self._non_dummy(destroyed.ordering)
        if not non_dummy:
            return destroyed

        k = max(1, int(len(non_dummy) * self.destroy_fraction))
        k = min(k, len(non_dummy))
        indices = rng.choice(len(non_dummy), size=k, replace=False)
        remove_set = {non_dummy[i] for i in indices}

        destroyed.ordering = [a for a in destroyed.ordering if a not in remove_set]
        destroyed.unscheduled = [a for a in non_dummy if a in remove_set]
        destroyed.invalidate_cache()
        return destroyed

    # ====================================================================== #
    # Repair operators                                                         #
    # ====================================================================== #

    def _repair_random_insert(
        self, state: 'RCPSPState', rng: np.random.Generator
    ) -> 'RCPSPState':
        """
        Re-insert unscheduled activities at uniformly random feasible positions.

        Activities are processed in topological order (predecessors first) so
        that each insertion finds its correct feasible window.
        """
        repaired = state.copy()
        to_insert = self._topo_sort(repaired.unscheduled)
        repaired.unscheduled = []

        for act in to_insert:
            self._insert_feasible(act, repaired.ordering, rng, greedy=False)

        repaired.invalidate_cache()
        return repaired

    def _repair_greedy_insert(
        self, state: 'RCPSPState', rng: np.random.Generator
    ) -> 'RCPSPState':
        """
        Re-insert unscheduled activities at the earliest feasible position (lo).

        Produces a left-justified (greedy) insertion that tends to reduce
        makespan at the cost of diversity.
        """
        repaired = state.copy()
        to_insert = self._topo_sort(repaired.unscheduled)
        repaired.unscheduled = []

        for act in to_insert:
            self._insert_feasible(act, repaired.ordering, rng, greedy=True)

        repaired.invalidate_cache()
        return repaired

    # ====================================================================== #
    # Main optimisation loop                                                   #
    # ====================================================================== #

    def run(self) -> Tuple['RCPSPState', Dict[str, Any]]:
        """
        Execute the ALNS algorithm.

        Workflow
        --------
        1. Build an initial solution by evaluating all seed priority rules and
           selecting the best ordering.
        2. Configure the ``ALNS`` engine with the chosen destroy and repair
           operators, ``SegmentedRouletteWheel`` selection, the configured
           acceptance criterion, and ``MaxIterations`` stopping rule.
        3. Run ``alns.iterate`` and return the best state found.

        Returns
        -------
        best_state : RCPSPState
            Best complete solution found.
        log : dict
            Summary with keys:
            ``initial_duration``, ``best_duration``, ``improvement``,
            ``destroy_counts``, ``repair_counts``.
        """
        # ── Initial solution ────────────────────────────────────────────────
        init_state, seed_info = self._create_initial_solution()
        init_dur = init_state.objective()

        if self.verbose:
            if seed_info:
                print(
                    f"\nInitial seeding ({len(seed_info)} rules)  |  "
                    f"best = {min(seed_info.values()):.2f} h\n"
                )
                print(f"  {'Rule':<22} {'Duration (h)':>14}")
                print("  " + "-" * 38)
                for rule, dur in seed_info.items():
                    print(f"  {rule:<22} {dur:>14.2f}")
            print(
                f"\nRunning ALNS  "
                f"destroy={self.destroy_ops}  "
                f"repair={self.repair_ops}  "
                f"accept={self.accept_name!r}  "
                f"n_iter={self.n_iter}\n"
                f"  Initial objective : {init_dur:.2f} h"
            )

        # ── ALNS engine setup ───────────────────────────────────────────────
        alns_engine = ALNS(self._rng)

        for op_name in self.destroy_ops:
            alns_engine.add_destroy_operator(
                getattr(self, self._DESTROY_METHODS[op_name]), name=op_name
            )
        for op_name in self.repair_ops:
            alns_engine.add_repair_operator(
                getattr(self, self._REPAIR_METHODS[op_name]), name=op_name
            )

        select = SegmentedRouletteWheel(
            self.weights, self.theta, self.seg_length,
            len(self.destroy_ops), len(self.repair_ops),
        )

        # ── Acceptance criterion ────────────────────────────────────────────
        if self.accept_name == 'hill_climbing':
            accept = HillClimbing()

        elif self.accept_name == 'record_to_record':
            # autofit(init_obj, start_gap, end_gap, num_iters) — linear decay
            accept = RecordToRecordTravel.autofit(
                init_dur, self.rrt_percent_gap, 0.0, self.n_iter
            )

        elif self.accept_name == 'simulated_annealing':
            # autofit(init_obj, worse, accept_prob, num_iters) — exponential cooling
            # Accept solutions up to 5% worse with probability 0.5 initially.
            accept = SimulatedAnnealing.autofit(init_dur, 0.05, 0.5, self.n_iter)

        else:
            accept = HillClimbing()

        stop = MaxIterations(self.n_iter)

        # ── Run ─────────────────────────────────────────────────────────────
        result = alns_engine.iterate(init_state, select, accept, stop)
        best_state: RCPSPState = result.best_state
        best_dur = best_state.objective()
        stats = result.statistics

        if self.verbose:
            print(f"  Best found        : {best_dur:.2f} h")
            print(f"  Improvement       : {init_dur - best_dur:.2f} h\n")
            print("  Destroy operator usage  [best / better / accepted / rejected]:")
            for op, counts in stats.destroy_operator_counts.items():
                print(f"    {op:<20} {counts}")
            print("  Repair operator usage   [best / better / accepted / rejected]:")
            for op, counts in stats.repair_operator_counts.items():
                print(f"    {op:<20} {counts}")
            print()

        logger.info(
            "ALNS finished | best = %.2f h | init = %.2f h | iterations = %d",
            best_dur, init_dur, self.n_iter,
        )

        log: Dict[str, Any] = {
            'initial_duration': init_dur,
            'best_duration':    best_dur,
            'improvement':      init_dur - best_dur,
            'destroy_counts':   dict(stats.destroy_operator_counts),
            'repair_counts':    dict(stats.repair_operator_counts),
        }
        return best_state, log

    # ====================================================================== #
    # Result helpers                                                           #
    # ====================================================================== #

    def get_best_schedule(self, best_state: 'RCPSPState') -> Dict[str, Any]:
        """
        Decode the best state and return the full schedule result dict.

        Calls ``calculateSerialScheduleWithResources`` with the best activity
        ordering so the ``Pert`` object is left in the state of the best
        found schedule (Gantt chart, constrained chain, etc. are accessible).

        Parameters
        ----------
        best_state : RCPSPState
            As returned by ``run()``.

        Returns
        -------
        dict
            Same keys as ``calculateSerialScheduleWithResources``.
        """
        return self.pert.calculateSerialScheduleWithResources(
            _ordered=best_state.ordering
        )

    def get_best_activity_list(self, best_state: 'RCPSPState') -> List[str]:
        """
        Return the best ordering as a human-readable list of activity names.

        Parameters
        ----------
        best_state : RCPSPState
            As returned by ``run()``.

        Returns
        -------
        list of str
        """
        return [a.returnName() for a in best_state.ordering]
