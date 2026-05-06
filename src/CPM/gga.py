# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
gga.py — Graph Genetic Algorithm (GGA) for Resource-Constrained Project Scheduling

Implements Liu et al. (2025) "A graph-based GA for RCPSP" using an Activity-on-Node
(AON) lag-based chromosome and a micro-GA with frozen subgraph blocks.

References
----------
Liu, Y., Liu, X., & Huang, L. (2025). A graph-based GA for Resource-Constrained
Project Scheduling Problems. Preprint, SSRN 5851447.

Chromosome representation
-------------------------
A flat ``List[float]`` of non-negative lag values, one per directed arc in the
project network (enumerated once from ``pert.forwardDict`` at construction time).

For arc (i → j):  λ(i, j) = S(j) − S(i) − d(i)  (hours, ≥ 0)

where S(·) are activity start times in hours from project start and d(·) is
activity duration in hours.

Conversion pipeline
-------------------
lags → start_times → activity_order → serial_SGS → fitness + actual_starts → corrected_lags

Every genetic operator produces a fully evaluated ``Individual`` by calling
``_improve`` (or ``_evaluate_activity_order``) internally.  The main loop therefore
contains no separate evaluation step.

Micro-GA structure (Algorithm 1)
---------------------------------
Small elite pool (ne, default 5).  Each generation six offspring are created—one
per operator—merged with the elite, and the best ne survive.  A stall counter
triggers a full population restart when no improvement occurs for
``restart_threshold`` consecutive generations.  The all-time best individual
(winner) is re-injected into the restarted pool so it is never lost.

Individual
----------
Plain Python dict ``{'lags': List[float], 'fitness': float}``.  No DEAP dependency.

Requirements
------------
    numpy, networkx  (already dependencies of pert.py)
"""

from __future__ import annotations

import logging
import math
import random
from collections import deque
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

import networkx as nx
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Priority rules for initial-population seeding (mirrors ga.py)
# ---------------------------------------------------------------------------
PRIORITY_RULES: List[str] = [
    'es', 'ef', 'ls', 'lf', 'duration', 'random',
    'mts', 'mtp', 'grpw', 'grd', 'rr', 'avgrr',
    'maxrr', 'minrr', 'irsm', 'wcs', 'acs',
    'mehh_8000_b', 'mehh_3375_b', 'mehh_1000_b', 'mehh_125_b', 'gphh_b',
]

# Type alias for readability
Individual = Dict[str, Any]   # keys: 'lags': List[float], 'fitness': float


class RCPSPGraphGeneticAlgorithm:
    """
    Graph Genetic Algorithm for RCPSP using the AON lag-based chromosome.

    Parameters
    ----------
    pert : Pert
        Fully initialised ``Pert`` object.  ``generateInfo()`` must have been
        called so that ``infoDict`` entries (es, ef, …) are populated.
    ne : int
        Elite pool size.  Forced to ≥ 2 (``random.sample`` requirement).
        Paper default: 5.
    n_gen : int
        Number of GA generations.
    restart_threshold : int
        Consecutive generations without improvement before restarting the
        population.  Paper: 600 for J30, 1200 for J60/J120.
    rho : float
        Fraction of frozen-block nodes to exclude via the strategic exclusion
        heuristic (Algorithm 2).  Default 0.2 (paper: 20 %).
    seed : int
        RNG seed for reproducibility.
    verbose : bool
        Print per-generation statistics to stdout.
    """

    def __init__(
        self,
        pert,
        ne: int = 5,
        n_gen: int = 500,
        restart_threshold: int = 50,
        rho: float = 0.2,
        seed: int = 42,
        verbose: bool = True,
    ) -> None:
        self.pert = pert
        self.ne = max(2, ne)
        self.n_gen = n_gen
        self.restart_threshold = restart_threshold
        self.rho = rho
        self.verbose = verbose

        random.seed(seed)
        np.random.seed(seed)

        # Fixed activity list — order matches forwardDict insertion order
        self._activities: List[Any] = list(pert.forwardDict.keys())
        self._n: int = len(self._activities)
        self._act_to_idx: Dict[Any, int] = {a: i for i, a in enumerate(self._activities)}

        self._build_arc_index()
        self._build_topo_order()

        # Duration cache (float hours) to avoid repeated method calls
        self._durations: Dict[Any, float] = {
            a: a.returnDuration() for a in self._activities
        }

        # Dummy START / END activities — excluded from mutation target selection
        _start = getattr(pert, 'startActivity', None)
        _end   = getattr(pert, 'endActivity',   None)
        self._dummy_acts: FrozenSet[Any] = frozenset(
            a for a in [_start, _end] if a is not None
        )

        # CPM duration upper-bound (used to scale random lags)
        self._cpm_duration: float = (
            max(pert.infoDict[a]['ef'] for a in pert.infoDict)
            if pert.infoDict else 1.0
        )

        # Transitive successor cache used for precedence-safe insertion windows.
        self._trans_successors: Dict[Any, Set[Any]] = {}
        for act in reversed(self._topo):
            direct = {
                succ for succ in self.pert.forwardDict.get(act, [])
                if succ in self._act_to_idx
            }
            trans: Set[Any] = set()
            for succ in direct:
                trans.update(self._trans_successors.get(succ, set()))
            self._trans_successors[act] = direct | trans

    # ---------------------------------------------------------------------- #
    # Construction helpers                                                     #
    # ---------------------------------------------------------------------- #

    def _build_arc_index(self) -> None:
        """Enumerate all arcs from forwardDict; build arc list and reverse lookup."""
        arcs: List[Tuple[int, int]] = []
        arc_to_idx: Dict[Tuple[int, int], int] = {}
        for u, succs in self.pert.forwardDict.items():
            if u not in self._act_to_idx:
                continue
            u_idx = self._act_to_idx[u]
            for v in succs:
                if v not in self._act_to_idx:
                    continue
                v_idx = self._act_to_idx[v]
                key = (u_idx, v_idx)
                if key not in arc_to_idx:
                    arc_to_idx[key] = len(arcs)
                    arcs.append(key)
        self._arcs: List[Tuple[int, int]] = arcs
        self._arc_to_idx: Dict[Tuple[int, int], int] = arc_to_idx

    def _build_topo_order(self) -> None:
        """Compute a topological ordering of all activities."""
        g = getattr(self.pert, 'nxgraph', None)
        if g is not None:
            self._topo: List[Any] = [
                a for a in nx.topological_sort(g) if a in self._act_to_idx
            ]
        else:
            # Kahn's BFS fallback
            in_deg: Dict[Any, int] = {a: 0 for a in self._activities}
            for u, succs in self.pert.forwardDict.items():
                for v in succs:
                    if v in in_deg:
                        in_deg[v] += 1
            queue: deque = deque(a for a in self._activities if in_deg[a] == 0)
            topo: List[Any] = []
            while queue:
                u = queue.popleft()
                topo.append(u)
                for v in self.pert.forwardDict.get(u, []):
                    if v in in_deg:
                        in_deg[v] -= 1
                        if in_deg[v] == 0:
                            queue.append(v)
            self._topo = topo

        # Position lookup for tie-breaking in _start_times_to_activity_order
        self._topo_idx: Dict[Any, int] = {a: i for i, a in enumerate(self._topo)}

    # ---------------------------------------------------------------------- #
    # Conversion pipeline                                                      #
    # ---------------------------------------------------------------------- #

    def _lags_to_start_times(self, lags: List[float]) -> Dict[Any, float]:
        """
        Forward scheduling pass: compute start times (hours) from a lag vector.

        S(j) = max over predecessors i of  S(i) + d(i) + λ(i, j)

        Source activities (no predecessors) start at 0.
        """
        start: Dict[Any, float] = {a: 0.0 for a in self._activities}
        for act in self._topo:
            a_idx = self._act_to_idx[act]
            for pred in self.pert.backwardDict.get(act, []):
                if pred not in self._act_to_idx:
                    continue
                p_idx = self._act_to_idx[pred]
                key = (p_idx, a_idx)
                if key in self._arc_to_idx:
                    lag = lags[self._arc_to_idx[key]]
                    candidate = start[pred] + self._durations[pred] + lag
                    if candidate > start[act]:
                        start[act] = candidate
        return start

    def _start_times_to_activity_order(
        self, start_times: Dict[Any, float]
    ) -> List[Any]:
        """
        Sort activities by start time (ascending).

        Topological index is used as a tie-breaker to guarantee a
        precedence-feasible ordering when two activities share the same
        computed start time.
        """
        return sorted(
            self._activities,
            key=lambda a: (start_times.get(a, 0.0), self._topo_idx.get(a, 0)),
        )

    def _correct_aon_lag(self, actual_starts: Dict[Any, float]) -> List[float]:
        """
        Recompute lag vector from (resource-feasible) start times.

        λ(i, j) = max(0, S(j) − S(i) − d(i))
        """
        lags = [0.0] * len(self._arcs)
        for k, (i_idx, j_idx) in enumerate(self._arcs):
            ai = self._activities[i_idx]
            aj = self._activities[j_idx]
            lag = (
                actual_starts.get(aj, 0.0)
                - actual_starts.get(ai, 0.0)
                - self._durations[ai]
            )
            lags[k] = max(0.0, lag)
        return lags

    def _improve(self, lags: List[float]) -> Tuple[float, List[float]]:
        """
        Decode a lag chromosome, run serial SGS, and correct lags from the
        resource-feasible schedule (IMPROVE / FBI step in the paper).

        Returns
        -------
        (makespan, corrected_lags)
        """
        order = self._start_times_to_activity_order(self._lags_to_start_times(lags))
        out = self.pert.calculateSerialScheduleWithResources(_ordered=order)
        if out.get('n_completed', 0) < out.get('n_activities', len(self._activities)):
            return math.inf, list(lags)
        makespan = out['scheduled_duration'] - 2

        actual_starts = self._get_actual_start_offsets()
        if actual_starts is None:
            return math.inf, list(lags)
        corrected = self._correct_aon_lag(actual_starts)
        return makespan, corrected

    def _evaluate_activity_order(self, order: List[Any]) -> Individual:
        """
        Run serial SGS from a given activity ordering, compute lags from the
        resulting resource-feasible schedule, and return an evaluated Individual.
        """
        out = self.pert.calculateSerialScheduleWithResources(_ordered=order)
        if out.get('n_completed', 0) < out.get('n_activities', len(self._activities)):
            return self._make_individual([0.0] * len(self._arcs), math.inf)
        makespan = out['scheduled_duration'] - 2

        actual_starts = self._get_actual_start_offsets()
        if actual_starts is None:
            return self._make_individual([0.0] * len(self._arcs), math.inf)

        corrected = self._correct_aon_lag(actual_starts)
        return self._make_individual(corrected, makespan)

    def _get_actual_start_offsets(self) -> Optional[Dict[Any, float]]:
        """
        Return actual start offsets in hours after a successful SGS run.

        ``None`` indicates that at least one activity was not scheduled or that
        the project start time is unavailable.
        """
        project_start = self.pert.startTime
        if project_start is None:
            return None

        actual_starts: Dict[Any, float] = {}
        for a in self._activities:
            if a.startTime is None:
                return None
            actual_starts[a] = (
                (a.startTime - project_start).total_seconds() / 3600.0
            )
        return actual_starts

    # ---------------------------------------------------------------------- #
    # Individual factory                                                       #
    # ---------------------------------------------------------------------- #

    def _make_individual(
        self, lags: List[float], fitness: float = math.inf
    ) -> Individual:
        return {'lags': list(lags), 'fitness': fitness}

    def _evaluate(self, ind: Individual) -> Individual:
        """Call _improve; update individual's fitness and lags in place."""
        try:
            fitness, corrected = self._improve(ind['lags'])
            ind['fitness'] = fitness
            ind['lags'] = corrected
        except Exception as exc:
            logger.warning("Evaluation failed: %s", exc)
            ind['fitness'] = math.inf
        return ind

    # ---------------------------------------------------------------------- #
    # Initial population                                                       #
    # ---------------------------------------------------------------------- #

    def _build_initial_population(self) -> List[Individual]:
        """
        Build an elite pool of ``ne`` fully-evaluated individuals.

        Seeding order:
        1. Each priority rule in ``PRIORITY_RULES`` (up to ``ne`` slots):
           derive an activity ordering → serial SGS → corrected lag vector.
        2. Zero-lag individual (all lags = 0, i.e. CPM-earliest ordering).
        3. Random lag vectors until the pool reaches ``ne``.
        """
        pool: List[Individual] = []
        seed_info: Dict[str, float] = {}

        all_acts = list(self.pert.forwardDict.keys())
        for rule in PRIORITY_RULES:
            if len(pool) >= self.ne:
                break
            try:
                self.pert.priorities = None
                raw = self.pert.priority_calculation(
                    list(all_acts), priority_rule=rule
                )
                if raw and isinstance(raw[0], tuple):
                    ordered_acts: List[Any] = [a for (a, _, _) in raw]
                else:
                    ordered_acts = list(raw)

                ind = self._evaluate_activity_order(ordered_acts)
                pool.append(ind)
                seed_info[rule] = ind['fitness']
            except Exception as exc:
                logger.warning(
                    "Skipping rule '%s' during GGA seeding: %s", rule, exc
                )

        # Zero-lag seed (pure CPM earliest-start ordering)
        if len(pool) < self.ne:
            ind = self._evaluate(self._make_individual([0.0] * len(self._arcs)))
            pool.append(ind)

        # Random fill
        while len(pool) < self.ne:
            lags = [
                random.uniform(0.0, self._cpm_duration) for _ in self._arcs
            ]
            ind = self._evaluate(self._make_individual(lags))
            pool.append(ind)

        if self.verbose:
            if seed_info:
                best_s = min(seed_info.values())
                n_rand = len(pool) - len(seed_info)
                print(
                    f"\nGGA initial elite: {len(seed_info)} rule-seeded + "
                    f"{n_rand} fill  |  best seeded = {best_s:.2f} h\n"
                )
                print(f"  {'Rule':<22} {'Fitness (h)':>12}")
                print("  " + "-" * 36)
                for r, f in seed_info.items():
                    print(f"  {r:<22} {f:>12.2f}")
                print()

        return pool

    # ---------------------------------------------------------------------- #
    # Frozen block selection — Algorithm 2                                     #
    # ---------------------------------------------------------------------- #

    def _select_frozen_block(
        self,
        winner: Individual,
        block_type: Optional[str] = None,
    ) -> FrozenSet[Any]:
        """
        Algorithm 2: select a frozen subgraph with strategic exclusion.

        The frozen set is a collection of Activity objects whose incoming lags
        are protected during lag-based crossover (Algorithm 3).

        Parameters
        ----------
        winner : Individual
            Current best individual (used only for context; frozen selection is
            graph-structural and does not depend on lag values).
        block_type : str or None
            ``'forward'`` — predecessor subgraph (front block in the paper).
            ``'backward'`` — successor subgraph (backward block).
            ``None`` — chosen randomly each call.

        Returns
        -------
        frozenset of Activity objects
        """
        non_dummy = [a for a in self._activities if a not in self._dummy_acts]
        if len(non_dummy) < 4:
            return frozenset()

        k = random.randint(1, 3)
        seeds = random.sample(non_dummy, min(k, len(non_dummy)))
        btype = block_type or random.choice(['forward', 'backward'])

        # BFS to collect subgraph nodes
        collected: Set[Any] = set()
        for seed in seeds:
            visited_bfs: Set[Any] = set()
            queue: deque = deque([seed])
            while queue:
                node = queue.popleft()
                if node in visited_bfs:
                    continue
                visited_bfs.add(node)
                collected.add(node)
                if btype == 'forward':
                    # Predecessor subgraph (front block: fixed incoming lags)
                    for pred in self.pert.backwardDict.get(node, []):
                        if pred not in self._dummy_acts and pred not in visited_bfs:
                            queue.append(pred)
                else:
                    # Successor subgraph (backward block)
                    for succ in self.pert.forwardDict.get(node, []):
                        if succ not in self._dummy_acts and succ not in visited_bfs:
                            queue.append(succ)

        if btype == 'backward':
            frontier: Set[Any] = set()
            for node in list(collected):
                for pred in self.pert.backwardDict.get(node, []):
                    if pred not in self._dummy_acts:
                        frontier.add(pred)
            collected.update(frontier)

        if not collected:
            return frozenset()

        # Strategic exclusion (Algorithm 2, probability 0.9)
        if random.random() < 0.9 and len(collected) > 1:
            q = max(1, int(self.rho * len(collected)))
            to_remove: Set[Any] = set()

            # 1. Highly-connected nodes (by nxgraph degree)
            g = getattr(self.pert, 'nxgraph', None)
            if g is not None:
                by_degree = sorted(
                    collected,
                    key=lambda a: g.degree(a) if a in g else 0,
                    reverse=True,
                )
                budget = max(1, q // 3)
                to_remove.update(by_degree[:budget])

            # 2. Long-duration nodes
            if len(to_remove) < q:
                remaining = collected - to_remove
                by_dur = sorted(
                    remaining, key=lambda a: self._durations[a], reverse=True
                )
                budget = max(1, (q - len(to_remove)) // 2)
                to_remove.update(by_dur[:budget])

            # 3. Random fill to reach exclusion quota
            if len(to_remove) < q:
                remaining = list(collected - to_remove)
                random.shuffle(remaining)
                to_remove.update(remaining[: q - len(to_remove)])

            collected -= to_remove

        return frozenset(collected)

    # ---------------------------------------------------------------------- #
    # Genetic operators                                                        #
    # ---------------------------------------------------------------------- #

    def _lag_crossover(
        self,
        p1: Individual,
        p2: Individual,
        frozen: FrozenSet[Any],
    ) -> Individual:
        """
        Algorithm 3: Lag-uniform crossover with frozen block.

        For each activity v:
        - If v is in the frozen set → inherit all incoming lags from p1.
        - Otherwise → inherit v's incoming lag bundle from p1 or p2 with
          equal probability.

        Two children would be symmetric; here we produce one child (the main
        loop calls this operator multiple times with different parent pairs).
        """
        lags1 = p1['lags']
        lags2 = p2['lags']
        child_lags = list(lags1)

        incoming_by_target: Dict[int, List[int]] = {}
        for k, (_, j_idx) in enumerate(self._arcs):
            incoming_by_target.setdefault(j_idx, []).append(k)

        for j_idx, arc_indices in incoming_by_target.items():
            act_j = self._activities[j_idx]
            if act_j in frozen or random.random() >= 0.5:
                continue
            for k in arc_indices:
                child_lags[k] = lags2[k]

        return self._evaluate(self._make_individual(child_lags))

    def _lag_mutation(
        self,
        ind: Individual,
        frozen: FrozenSet[Any],
    ) -> Individual:
        """
        Algorithm 4: Node-centric lag mutation.

        Select a non-frozen, non-dummy activity and shift *all* its incoming
        lags by the same scalar δ (clamped to ≥ 0).  This moves the selected
        activity and its whole successor subgraph forward or backward in time
        while preserving the internal structure of the subgraph.

        δ < 0 (compress, 80 % of calls): shift toward CPM earliest start.
        δ > 0 (expand, 20 % of calls): inject additional slack.
        """
        candidates = [
            a for a in self._activities
            if a not in frozen and a not in self._dummy_acts
        ]
        if not candidates:
            return self._make_individual(list(ind['lags']), ind['fitness'])

        # Retry up to 10 times to find a node with incoming arcs
        for _ in range(10):
            node = random.choice(candidates)
            node_idx = self._act_to_idx[node]
            incoming = [k for k, (_, j) in enumerate(self._arcs) if j == node_idx]
            if incoming:
                break
        else:
            return self._make_individual(list(ind['lags']), ind['fitness'])

        lags = list(ind['lags'])
        min_lag = min(lags[k] for k in incoming)

        if random.random() < 0.8:
            delta = -random.uniform(0.0, min_lag)
        else:
            delta = random.uniform(0.0, max(0.5, self._cpm_duration / 4.0))

        for k in incoming:
            lags[k] = max(0.0, lags[k] + delta)

        return self._evaluate(self._make_individual(lags))

    def _day_form_crossover(
        self,
        winner: Individual,
        challenger: Individual,
        frozen: FrozenSet[Any],
    ) -> Individual:
        """
        Algorithm 5: Day-form two-point crossover with frozen activity protection.

        1. Convert both parents to start-time vectors aligned with _topo.
        2. Build the four two-point segment-exchange variants and choose one.
        3. Restore frozen activities' start times to the winner's values.
        4. Re-derive activity ordering and run _improve (SGS + correct lags).
        """
        st_w = self._lags_to_start_times(winner['lags'])
        st_c = self._lags_to_start_times(challenger['lags'])

        n = len(self._topo)
        if n < 3:
            return self._make_individual(list(winner['lags']), winner['fitness'])

        q1, q2 = sorted(random.sample(range(1, n), 2))

        candidates: List[Dict[Any, float]] = []
        for base_st, donor_st in ((st_w, st_c), (st_c, st_w)):
            inside: Dict[Any, float] = {}
            outside: Dict[Any, float] = {}
            for i, act in enumerate(self._topo):
                in_segment = q1 <= i < q2
                inside[act] = (
                    donor_st.get(act, base_st.get(act, 0.0))
                    if in_segment else base_st.get(act, 0.0)
                )
                outside[act] = (
                    base_st.get(act, 0.0)
                    if in_segment else donor_st.get(act, base_st.get(act, 0.0))
                )
            candidates.extend([inside, outside])

        child_st = random.choice(candidates)
        for act in frozen:
            child_st[act] = st_w.get(act, 0.0)

        child_lags = self._correct_aon_lag(child_st)
        return self._evaluate(self._make_individual(child_lags))

    def _day_form_mutation(self, ind: Individual) -> Individual:
        """
        Algorithm 6: Day-form mutation — shift one activity's start time earlier.

        Picks a random non-dummy activity, reduces its start time by a random
        fraction, then rebuilds the activity ordering and runs _improve.
        """
        candidates = [a for a in self._activities if a not in self._dummy_acts]
        if not candidates:
            return self._make_individual(list(ind['lags']), ind['fitness'])

        act = random.choice(candidates)
        st = self._lags_to_start_times(ind['lags'])
        s_act = st.get(act, 0.0)

        if s_act > 0.0:
            delta = random.uniform(0.0, s_act)
            st[act] = s_act - delta

        child_lags = self._correct_aon_lag(st)
        return self._evaluate(self._make_individual(child_lags))

    def _activity_list_crossover(
        self,
        winner: Individual,
        challenger: Individual,
        frozen: FrozenSet[Any],  # noqa: ARG002  (unused; kept for uniform API)
    ) -> Individual:
        """
        Algorithm 7: One-point activity-list crossover (CORRECTOMIT + FEASIBLESEQUENCE).

        1. Derive activity orderings from both parents' start times.
        2. Choose a segment-boundary cut point (n_segments ∈ {2..6}).
        3. Prefix from winner; remainder filled by scanning challenger and
           appending activities not yet in the child (Hartmann 1998 style).
        4. Repair precedence via ``pert.reorder_by_dependencies``.
        5. Evaluate via SGS.
        """
        order_w = self._start_times_to_activity_order(
            self._lags_to_start_times(winner['lags'])
        )
        order_c = self._start_times_to_activity_order(
            self._lags_to_start_times(challenger['lags'])
        )

        n = len(order_w)
        if n < 2:
            return self._make_individual(list(winner['lags']), winner['fitness'])

        # Segment-based cut point (Algorithm 7 step 1–2)
        n_segs = random.randint(2, min(6, n))
        seg_size = max(1, n // n_segs)
        boundaries = list(range(seg_size, n, seg_size))
        q = random.choice(boundaries) if boundaries else n // 2

        # CORRECTOMIT: one-point order crossover (Hartmann 1998)
        prefix = order_w[:q]
        taken = {self._act_to_idx[a] for a in prefix}
        suffix = [a for a in order_c if self._act_to_idx[a] not in taken]

        # Ensure every activity is present exactly once
        child_order = list(prefix) + list(suffix)
        present = {self._act_to_idx[a] for a in child_order}
        child_order += [a for a in self._activities if self._act_to_idx[a] not in present]

        # FEASIBLESEQUENCE: topological repair
        ranked = [(a, i) for i, a in enumerate(child_order)]
        try:
            repaired = self.pert.reorder_by_dependencies(ranked, self.pert.forwardDict)
            child_order = [a for a, _ in repaired]
        except Exception:
            pass

        return self._evaluate_activity_order(child_order)

    def _activity_list_mutation(
        self,
        ind: Individual,
        frozen: FrozenSet[Any],
    ) -> Individual:
        """
        Algorithm 8: Frozen-aware insertion mutation on activity ordering.

        1. Derive activity order from ind's start times.
        2. With probability 0.8 pick from non-frozen non-dummy activities;
           otherwise pick any non-dummy (small exploratory probability).
        3. Compute feasible insertion window [lo, hi] from predecessor/
           successor positions in the current ordering.
        4. Relocate the chosen activity to a random position in [lo, hi].
        5. Evaluate via SGS.
        """
        order = self._start_times_to_activity_order(
            self._lags_to_start_times(ind['lags'])
        )
        n = len(order)
        if n < 4:
            return self._make_individual(list(ind['lags']), ind['fitness'])

        # Position lookup (by object identity index)
        pos_of: Dict[int, int] = {self._act_to_idx[a]: i for i, a in enumerate(order)}

        non_dummy = [a for a in order if a not in self._dummy_acts]
        non_frozen_non_dummy = [a for a in non_dummy if a not in frozen]

        if not non_dummy:
            return self._make_individual(list(ind['lags']), ind['fitness'])

        if random.random() < 0.8 and non_frozen_non_dummy:
            act = random.choice(non_frozen_non_dummy)
        else:
            act = random.choice(non_dummy)

        act_idx = self._act_to_idx[act]
        cur_pos = pos_of[act_idx]

        # Compute feasible insertion window
        preds = self.pert.backwardDict.get(act, [])
        succs = self._trans_successors.get(act, set())

        lo = (
            max(
                pos_of[self._act_to_idx[p]]
                for p in preds
                if p in self._act_to_idx and self._act_to_idx[p] in pos_of
            ) + 1
            if preds else 0
        )
        hi = (
            min(
                pos_of[self._act_to_idx[s]]
                for s in succs
                if s in self._act_to_idx and self._act_to_idx[s] in pos_of
            ) - 1
            if succs else n - 1
        )

        if lo > hi:
            return self._make_individual(list(ind['lags']), ind['fitness'])

        new_pos = random.randint(lo, hi)
        if new_pos == cur_pos:
            return self._make_individual(list(ind['lags']), ind['fitness'])

        new_order = list(order)
        new_order.pop(cur_pos)
        insert_at = new_pos if new_pos < cur_pos else new_pos - 1
        new_order.insert(insert_at, act)

        return self._evaluate_activity_order(new_order)

    # ---------------------------------------------------------------------- #
    # Main optimisation loop — Algorithm 1                                     #
    # ---------------------------------------------------------------------- #

    def run(self) -> Tuple[Individual, List[Dict]]:
        """
        Execute the micro-GA.

        Workflow
        --------
        1. Build initial elite pool (priority-rule seeds + random fill).
        2. For each generation:
           a. Select frozen block from winner.
           b. Produce six offspring (one per operator).
           c. Merge elite + offspring; keep best ``ne``.
           d. Update winner and stall counter.
           e. Restart if stall ≥ restart_threshold (re-inject winner).

        Returns
        -------
        winner : Individual
            Best individual found across all generations and restarts.
        log : list of dict
            Per-generation records:
            ``gen``, ``best``, ``pool_best``, ``stall``, ``restart``.
        """
        # ── Generation 0 ─────────────────────────────────────────────────────
        elite = self._build_initial_population()
        elite.sort(key=lambda x: x['fitness'])

        winner = self._make_individual(elite[0]['lags'], elite[0]['fitness'])
        best_ever = winner['fitness']
        stall = 0
        log: List[Dict] = [
            {
                'gen': 0,
                'best': best_ever,
                'pool_best': elite[0]['fitness'],
                'stall': stall,
                'restart': False,
            }
        ]
        if self.verbose:
            print(
                f"gen {'0':>5} | best={best_ever:.2f} h | "
                f"pool_best={elite[0]['fitness']:.2f} h | stall={stall}"
            )

        # ── Generational loop ─────────────────────────────────────────────────
        for gen in range(1, self.n_gen + 1):
            frozen = self._select_frozen_block(winner)

            # Two distinct parents for lag crossover
            p1, p2 = random.sample(elite, 2)
            # Random elite individual for mutation operators
            p_rand = random.choice(elite)

            offspring: List[Individual] = [
                self._day_form_crossover(winner, p_rand, frozen),      # Alg 5
                self._lag_crossover(p1, p2, frozen),                   # Alg 3
                self._activity_list_crossover(winner, p_rand, frozen), # Alg 7
                self._lag_mutation(p_rand, frozen),                    # Alg 4
                self._day_form_mutation(p_rand),                       # Alg 6
                self._activity_list_mutation(p_rand, frozen),          # Alg 8
            ]

            # Merge and select elites
            pool = elite + offspring
            pool.sort(key=lambda x: x['fitness'])
            elite = pool[: self.ne]

            gen_best_fitness = elite[0]['fitness']
            restarted = False

            if gen_best_fitness < best_ever:
                best_ever = gen_best_fitness
                winner = self._make_individual(elite[0]['lags'], elite[0]['fitness'])
                stall = 0
            else:
                stall += 1

            # Stall-triggered restart
            if stall >= self.restart_threshold:
                fresh_elite = self._build_initial_population()
                fresh_elite.append(
                    self._make_individual(winner['lags'], winner['fitness'])
                )
                fresh_elite.sort(key=lambda x: x['fitness'])
                elite = fresh_elite[: self.ne]
                if elite[0]['fitness'] < best_ever:
                    best_ever = elite[0]['fitness']
                    winner = self._make_individual(
                        elite[0]['lags'], elite[0]['fitness']
                    )
                gen_best_fitness = elite[0]['fitness']
                stall = 0
                restarted = True

            log.append(
                {
                    'gen': gen,
                    'best': best_ever,
                    'pool_best': gen_best_fitness,
                    'stall': stall,
                    'restart': restarted,
                }
            )
            if self.verbose:
                tag = " [RESTART]" if restarted else ""
                print(
                    f"gen {gen:>5} | best={best_ever:.2f} h | "
                    f"pool_best={gen_best_fitness:.2f} h | stall={stall}{tag}"
                )

        logger.info(
            "GGA finished | best = %.2f h | generations = %d | ne = %d",
            best_ever,
            self.n_gen,
            self.ne,
        )
        return winner, log

    # ---------------------------------------------------------------------- #
    # Result helpers                                                           #
    # ---------------------------------------------------------------------- #

    def get_best_schedule(self, winner: Individual) -> Dict:
        """
        Decode the winner and return the full schedule result dict.

        Re-runs ``calculateSerialScheduleWithResources`` with the winner's
        implied activity ordering so that the ``Pert`` object is left in the
        state of the best schedule.
        """
        order = self._start_times_to_activity_order(
            self._lags_to_start_times(winner['lags'])
        )
        return self.pert.calculateSerialScheduleWithResources(_ordered=order)

    def get_best_activity_list(self, winner: Individual) -> List[str]:
        """
        Return the winner's implied scheduling order as a list of task names.
        """
        order = self._start_times_to_activity_order(
            self._lags_to_start_times(winner['lags'])
        )
        return [a.returnName() for a in order]

    def get_convergence_summary(self, log: List[Dict]) -> Dict:
        """
        Extract a concise convergence summary from the generation log.

        Returns
        -------
        dict with keys:
            ``n_gen``, ``best_duration``, ``initial_best``, ``improvement``,
            ``n_restarts``, ``final_stall``.
        """
        return {
            'n_gen': len(log) - 1,
            'best_duration': log[-1]['best'],
            'initial_best': log[0]['best'],
            'improvement': log[0]['best'] - log[-1]['best'],
            'n_restarts': sum(1 for r in log if r.get('restart', False)),
            'final_stall': log[-1]['stall'],
        }
