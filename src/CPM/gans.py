# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
gans.py — Hybrid Genetic Algorithm + Neighborhood Search for RCPSP

Implements Goncharov (2025) "A hybrid heuristic algorithm for the
resource-constrained project scheduling problem" (arXiv:2502.18330v2).

Key algorithmic components
--------------------------
- **Resource ranking**: resources ranked by utilisation load; weights guide
  dense-gene selection and GRASP parallel SGS.
- **Dense genes**: time slots with high weighted resource utilisation; the
  activities executing during these slots are preferentially inherited by
  offspring chromosomes.
- **Crossover A**: greedy merge of dense-gene segments from both parents.
- **Crossover B**: segment swap based on the outgoing/incoming networks of
  dense-gene activities in the schedule graph G_S.
- **FBI**: Forward–Backward–Forward improvement (3 SGS calls); applied after
  every crossover and mutation operator.
- **Neighborhood NA**: block reschedule — P activities near a core activity
  are extracted and re-inserted at their earliest feasible positions.
- **Neighborhood NB**: split-list schedule — list is split A1 | block | A2;
  A1 is built with serial SGS, the block with GRASP-parallel SGS, A2 appended.
- **GANS main loop**: GA runs until a stall limit, then NS activates; σ-based
  instance classification adapts the GA/NS effort ratio automatically.

References
----------
Goncharov, E.N. (2025). A hybrid heuristic algorithm for the
resource-constrained project scheduling problem. arXiv:2502.18330v2.

Chromosome representation
-------------------------
A **precedence-feasible activity list** — same index encoding as ``ga.py``::

    order = [i₀, i₁, …, i_{N-1}]

where each element is an index into ``self._activities``.

``Individual = {'order': List[int], 'fitness': float}``  — no DEAP dependency.

Requirements
------------
    numpy, networkx  (already dependencies of pert.py)
"""

from __future__ import annotations

import logging
import math
import random
from collections import deque
from datetime import timedelta
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Priority rules for initial-population seeding (mirrors ga.py / gga.py)
# ---------------------------------------------------------------------------
PRIORITY_RULES: List[str] = [
    'es', 'ef', 'ls', 'lf', 'duration', 'random',
    'mts', 'mtp', 'grpw', 'grd', 'rr', 'avgrr',
    'maxrr', 'minrr', 'irsm', 'wcs', 'acs',
    'mehh_8000_b', 'mehh_3375_b', 'mehh_1000_b', 'mehh_125_b', 'gphh_b',
]

Individual = Dict[str, Any]   # keys: 'order': List[int], 'fitness': float

# Weight sets offered by the paper (Section 8); picked randomly each restart.
_WEIGHT_SETS: List[Tuple[float, ...]] = [
    (1.0, 0.8, 0.6, 0.4),
    (1.0, 0.9, 0.8, 0.7),
    (1.0, 1.0, 1.0, 1.0),
]


class RCPSPHybridGANS:
    """
    Hybrid Genetic Algorithm + Neighbourhood Search for RCPSP.

    Parameters
    ----------
    pert : Pert
        Fully initialised ``Pert`` object with ``generateInfo()`` called.
    pop_size : int
        Population size.  Priority-rule seeds fill up to ``len(PRIORITY_RULES)``
        slots; the rest are random precedence-feasible permutations.
    lambda_max : int
        Total budget of SGS evaluations (stopping criterion).
    ga_stall_limit : int
        Consecutive GA offspring rounds without improvement before activating NS.
        Overridden by σ-based instance classification.
    ns_steps : int
        NS iterations per activation.  Overridden by σ-based classification.
    block_size : int
        Initial ``P`` (number of activities per block) for CreateBlock.
    resource_threshold : float
        ``R`` in DenseActivities: weighted residual below this → dense gene.
    sigma1 : float
        Lower σ threshold for instance classification.
    sigma2 : float
        Upper σ threshold for instance classification.
    parents_size : int
        Maximum size of parent pool ``Γ'``.
    seed : int
        RNG seed.
    verbose : bool
        Print per-generation and NS statistics.
    """

    def __init__(
        self,
        pert,
        pop_size: int = 60,
        lambda_max: int = 50_000,
        ga_stall_limit: int = 50,
        ns_steps: int = 200,
        block_size: int = 6,
        resource_threshold: float = 0.75,
        sigma1: float = 0.2,
        sigma2: float = 0.6,
        parents_size: int = 10,
        seed: int = 42,
        verbose: bool = True,
    ) -> None:
        self.pert = pert
        self.pop_size = max(4, pop_size)
        self.lambda_max = lambda_max
        self.ga_stall_limit = ga_stall_limit
        self.ns_steps = ns_steps
        self.block_size = block_size
        self.resource_threshold = resource_threshold
        self.sigma1 = sigma1
        self.sigma2 = sigma2
        self.parents_size = min(parents_size, pop_size)
        self.verbose = verbose

        random.seed(seed)
        np.random.seed(seed)

        # Fixed activity list (matches forwardDict insertion order)
        self._activities: List[Any] = list(pert.forwardDict.keys())
        self._n: int = len(self._activities)
        self._act_to_idx: Dict[Any, int] = {a: i for i, a in enumerate(self._activities)}

        # Dummy START/END activities excluded from operator targets
        _start = getattr(pert, 'startActivity', None)
        _end   = getattr(pert, 'endActivity',   None)
        self._dummy_acts: FrozenSet[Any] = frozenset(
            a for a in [_start, _end] if a is not None
        )

        # CPM duration (used for σ computation)
        self._cpm_duration: float = pert.getProjectDuration() if hasattr(pert, 'getProjectDuration') else 1.0

        # Build resource info; gracefully degrade if resource pool unavailable
        self._build_resource_info()
        self._randomize_weights()

        # Adaptive block-size state
        self._block_size: int = block_size
        self._empty_block_history: List[bool] = []

        # Topological order for tie-breaking (used in _order_from_schedule)
        self._topo_idx: Dict[Any, int] = self._build_topo_idx()

    # ------------------------------------------------------------------ #
    # Construction helpers                                                  #
    # ------------------------------------------------------------------ #

    def _build_topo_idx(self) -> Dict[Any, int]:
        """Return a topological-order index for each activity."""
        import networkx as nx
        g = getattr(self.pert, 'nxgraph', None)
        if g is not None:
            topo = [a for a in nx.topological_sort(g) if a in self._act_to_idx]
        else:
            in_deg: Dict[Any, int] = {a: 0 for a in self._activities}
            for u, succs in self.pert.forwardDict.items():
                for v in succs:
                    if v in in_deg:
                        in_deg[v] += 1
            q: deque = deque(a for a in self._activities if in_deg[a] == 0)
            topo = []
            while q:
                u = q.popleft(); topo.append(u)
                for v in self.pert.forwardDict.get(u, []):
                    if v in in_deg:
                        in_deg[v] -= 1
                        if in_deg[v] == 0:
                            q.append(v)
        return {a: i for i, a in enumerate(topo)}

    def _build_resource_info(self) -> None:
        """Extract resource IDs, capacities, and per-activity demands."""
        rp = getattr(self.pert, 'resource_pool', None)
        self._skill_ids: List[str] = []
        self._skill_capacity: Dict[str, float] = {}
        self._activity_demand: Dict[Any, Dict[str, float]] = {}

        if rp is not None:
            try:
                self._skill_ids = list(rp.get_all_skills())
                for sk in self._skill_ids:
                    self._skill_capacity[sk] = float(
                        rp.resources[sk].get_max_availability()
                    )
            except Exception:
                self._skill_ids = []

        for a in self._activities:
            req = getattr(a, 'required_resources', []) or []
            demand: Dict[str, float] = {}
            for item in req:
                if isinstance(item, dict):
                    sk = item.get('skill_type') or item.get('resource_type', '')
                    cnt = float(item.get('crew_count', item.get('count', 1)))
                    if sk:
                        demand[sk] = demand.get(sk, 0.0) + cnt
            self._activity_demand[a] = demand

    def _rank_resources(self) -> List[str]:
        """
        Rank resources by utilisation load (most loaded = most scarce).

        load_k = sum_j(r_jk * p_j) / (R_k * T_cpm)
        """
        if not self._skill_ids:
            return []
        loads: Dict[str, float] = {}
        denom = max(self._cpm_duration, 1.0)
        for sk in self._skill_ids:
            cap = self._skill_capacity.get(sk, 1.0)
            total = sum(
                self._activity_demand[a].get(sk, 0.0) * a.returnDuration()
                for a in self._activities
            )
            loads[sk] = total / max(cap * denom, 1e-9)
        return sorted(self._skill_ids, key=lambda s: loads[s], reverse=True)

    def _randomize_weights(self) -> None:
        """Pick a random resource-weight vector from the paper's weight sets."""
        ranked = self._rank_resources()
        wset = random.choice(_WEIGHT_SETS)
        self._resource_weights: Dict[str, float] = {}
        for i, sk in enumerate(ranked):
            self._resource_weights[sk] = wset[i] if i < len(wset) else wset[-1]
        # All unranked skills default to 1.0
        for sk in self._skill_ids:
            if sk not in self._resource_weights:
                self._resource_weights[sk] = 1.0

    # ------------------------------------------------------------------ #
    # Chromosome ↔ activity list                                           #
    # ------------------------------------------------------------------ #

    def _chromosome_to_activities(self, order: List[int]) -> List[Any]:
        return [self._activities[i] for i in order]

    def _rule_to_order(self, rule: str) -> List[int]:
        all_acts = list(self.pert.forwardDict.keys())
        raw = self.pert.priority_calculation(all_acts, priority_rule=rule)
        if raw and isinstance(raw[0], tuple):
            ordered = [a for (a, _, _) in raw]
        else:
            ordered = list(raw)
        return [self._act_to_idx[a] for a in ordered if a in self._act_to_idx]

    def _make_individual(self, order: List[int], fitness: float = math.inf) -> Individual:
        return {'order': list(order), 'fitness': fitness}

    # ------------------------------------------------------------------ #
    # Schedule decoding and timing reads                                   #
    # ------------------------------------------------------------------ #

    def _decode(self, order: List[int]) -> Dict[str, Any]:
        acts = self._chromosome_to_activities(order)
        return self.pert.calculateSerialScheduleWithResources(_ordered=acts)

    def _decode_fitness(self, order: List[int]) -> float:
        return self._decode(order)['scheduled_duration'] - 2

    def _get_schedule_times(self) -> Dict[Any, Tuple[float, float]]:
        """
        Read start/end times (float hours from project start) for all activities.

        Must be called immediately after a SGS decode, before the next call.
        """
        ps = self.pert.startTime
        times: Dict[Any, Tuple[float, float]] = {}
        for a in self._activities:
            st = getattr(a, 'startTime', None)
            et = getattr(a, 'endTime', None)
            if st is not None and ps is not None:
                s_h = (st - ps).total_seconds() / 3600.0
                if et is not None:
                    e_h = (et - ps).total_seconds() / 3600.0
                else:
                    e_h = s_h + a.returnDuration()
                times[a] = (s_h, e_h)
            else:
                times[a] = (0.0, a.returnDuration())
        return times

    def _order_from_schedule(self, times: Dict[Any, Tuple[float, float]]) -> List[int]:
        """Derive a precedence-feasible activity order from start times."""
        sorted_acts = sorted(
            self._activities,
            key=lambda a: (times.get(a, (0.0, 0.0))[0], self._topo_idx.get(a, 0))
        )
        return [self._act_to_idx[a] for a in sorted_acts]

    # ------------------------------------------------------------------ #
    # Forward-Backward Improvement (FBI)                                   #
    # ------------------------------------------------------------------ #

    def _fbi(self, order: List[int]) -> Tuple[float, List[int]]:
        """
        Three-pass forward-backward-forward improvement.

        Returns the best (fitness, order) across the three schedules.
        Each pass counts as one evaluation toward lambda_max.
        """
        # Pass 1: forward
        out1 = self._decode(order)
        f1 = out1['scheduled_duration'] - 2
        t1 = self._get_schedule_times()
        o1 = self._order_from_schedule(t1)

        # Pass 2: backward (reverse order → SGS pushes activities as late as possible)
        out2 = self._decode(list(reversed(o1)))
        f2 = out2['scheduled_duration'] - 2
        t2 = self._get_schedule_times()
        o2 = self._order_from_schedule(t2)

        # Pass 3: forward again from backward-adjusted order
        out3 = self._decode(o2)
        f3 = out3['scheduled_duration'] - 2
        t3 = self._get_schedule_times()
        o3 = self._order_from_schedule(t3)

        candidates = [(f1, o1), (f2, o2), (f3, o3)]
        best_f, best_o = min(candidates, key=lambda x: x[0])
        return best_f, best_o

    def _evaluate_with_fbi(self, order: List[int]) -> Individual:
        fitness, best_order = self._fbi(order)
        return self._make_individual(best_order, fitness)

    def _evaluate_no_fbi(self, order: List[int]) -> Individual:
        fitness = self._decode_fitness(order)
        return self._make_individual(order, fitness)

    # ------------------------------------------------------------------ #
    # Dense genes — DenseActivities(S, R) → Θ                             #
    # ------------------------------------------------------------------ #

    def _weighted_residual(
        self,
        active_acts: List[Any],
    ) -> float:
        """
        Compute v_t = sum_k (R_k - sum_{j∈J(t)} r_jk) * w_k / R_k

        Returns a value in [0, K] where 0 = fully utilised, K = completely idle.
        """
        if not self._skill_ids:
            # Fallback: proportion of activities running (pseudo-utilisation)
            n_total = max(1, len(self._activities))
            return max(0.0, 1.0 - len(active_acts) / n_total)

        v = 0.0
        for sk in self._skill_ids:
            cap = self._skill_capacity.get(sk, 1.0)
            if cap <= 0:
                continue
            used = sum(self._activity_demand[a].get(sk, 0.0) for a in active_acts)
            residual = max(0.0, cap - used)
            w = self._resource_weights.get(sk, 1.0)
            v += residual * w / cap
        return v

    def _dense_activities(
        self, order: List[int], times: Dict[Any, Tuple[float, float]]
    ) -> List[FrozenSet[Any]]:
        """
        Identify dense gene activity sets from a schedule.

        Returns a list of frozensets, each being a non-overlapping dense gene.
        Ordered by v_t (best first).
        """
        # Collect all event times
        events: Set[float] = set()
        for a in self._activities:
            s, e = times.get(a, (0.0, 0.0))
            if e > s:
                events.add(s)
                events.add(e)
        if not events:
            return []

        sorted_events = sorted(events)
        # Build (interval_start, active_set, v_t) for each interval
        dense_candidates: List[Tuple[float, FrozenSet[Any], float]] = []
        for i in range(len(sorted_events) - 1):
            t_mid = (sorted_events[i] + sorted_events[i + 1]) / 2.0
            active = [
                a for a in self._activities
                if times.get(a, (0.0, 0.0))[0] <= t_mid < times.get(a, (0.0, 0.0))[1]
            ]
            if not active:
                continue
            v = self._weighted_residual(active)
            if v < self.resource_threshold:
                dense_candidates.append((sorted_events[i], frozenset(active), v))

        if not dense_candidates:
            return []

        # Overlap resolution: keep non-overlapping dense genes (lowest v_t wins)
        dense_candidates.sort(key=lambda x: x[2])  # sort by v_t ascending
        result: List[FrozenSet[Any]] = []
        covered: Set[Any] = set()
        for _t, gene, _v in dense_candidates:
            new_acts = gene - covered
            if new_acts:
                result.append(frozenset(new_acts))
                covered |= gene
        return result

    # ------------------------------------------------------------------ #
    # Crossover A — dense-gene greedy merge                                #
    # ------------------------------------------------------------------ #

    def _crossover_A(self, p1: Individual, p2: Individual) -> Individual:
        """
        Algorithm: Crossover A (dense gene greedy merge).

        Decode both parents, collect dense genes with their v_t weights,
        greedily add activities from the parent chromosome containing the
        best (lowest v_t) dense gene, skipping already-added activities,
        then fill remainder from the shorter-duration parent.
        """
        out1 = self._decode(p1['order']); t1 = self._get_schedule_times()
        genes1 = self._dense_activities(p1['order'], t1)

        out2 = self._decode(p2['order']); t2 = self._get_schedule_times()
        genes2 = self._dense_activities(p2['order'], t2)

        # Build a merged gene list (activity set, parent index, v_t)
        # Recompute v_t for each gene
        merged: List[Tuple[FrozenSet[Any], int, float]] = []
        for g in genes1:
            active = list(g)
            v = self._weighted_residual(active)
            merged.append((g, 1, v))
        for g in genes2:
            active = list(g)
            v = self._weighted_residual(active)
            merged.append((g, 2, v))
        merged.sort(key=lambda x: x[2])  # best (lowest) first

        acts1 = self._chromosome_to_activities(p1['order'])
        acts2 = self._chromosome_to_activities(p2['order'])

        added: Set[int] = set()
        child_acts: List[Any] = []

        for gene, parent_idx, _v in merged:
            parent_list = acts1 if parent_idx == 1 else acts2
            for a in parent_list:
                idx = self._act_to_idx[a]
                if a in gene and idx not in added:
                    child_acts.append(a)
                    added.add(idx)

        # Fill remainder from shorter-duration parent
        shorter = acts1 if out1.get('scheduled_duration', math.inf) <= out2.get('scheduled_duration', math.inf) else acts2
        for a in shorter:
            idx = self._act_to_idx[a]
            if idx not in added:
                child_acts.append(a)
                added.add(idx)

        # Repair precedence
        child_order = [self._act_to_idx[a] for a in child_acts if a in self._act_to_idx]
        child_order = self._repair(child_order)
        return self._evaluate_with_fbi(child_order)

    # ------------------------------------------------------------------ #
    # Crossover B — dense-gene network swap                                #
    # ------------------------------------------------------------------ #

    def _build_schedule_graph(
        self, times: Dict[Any, Tuple[float, float]], tol: float = 0.5
    ) -> Dict[Any, List[Any]]:
        """
        Build G_S: arcs (i,j) where c_i ≈ s_j (finish-start tight) AND (i,j)∈A.

        Returns {activity: [immediate tight successors]}.
        """
        gs: Dict[Any, List[Any]] = {a: [] for a in self._activities}
        for u, succs in self.pert.forwardDict.items():
            if u not in times:
                continue
            _, cu = times[u]
            for v in succs:
                if v not in times:
                    continue
                sv, _ = times[v]
                if abs(cu - sv) < tol:
                    gs[u].append(v)
        return gs

    def _outgoing_network(
        self, act: Any, gs: Dict[Any, List[Any]]
    ) -> Set[Any]:
        """BFS in G_S from act following outgoing arcs."""
        visited: Set[Any] = set()
        q: deque = deque([act])
        while q:
            node = q.popleft()
            if node in visited:
                continue
            visited.add(node)
            for succ in gs.get(node, []):
                if succ not in visited:
                    q.append(succ)
        return visited

    def _crossover_B(self, p1: Individual, p2: Individual) -> Individual:
        """
        Algorithm: Crossover B (network-based segment swap).

        Selects the best dense gene from each parent, finds the outgoing/incoming
        network in the OTHER parent's schedule graph, locates the segment span
        in that parent's activity list, and swaps it in.
        """
        out1 = self._decode(p1['order']); t1 = self._get_schedule_times()
        genes1 = self._dense_activities(p1['order'], t1)
        gs1 = self._build_schedule_graph(t1)

        out2 = self._decode(p2['order']); t2 = self._get_schedule_times()
        genes2 = self._dense_activities(p2['order'], t2)
        gs2 = self._build_schedule_graph(t2)

        acts1 = self._chromosome_to_activities(p1['order'])
        acts2 = self._chromosome_to_activities(p2['order'])

        if not genes1 or not genes2:
            # Fall back to Crossover A when no dense genes found
            return self._crossover_A(p1, p2)

        # Select best dense gene from each parent
        best_g1 = genes1[0]
        best_g2 = genes2[0]

        # For gene from parent1, find its network in parent2's schedule graph
        network_in_p2: Set[Any] = set()
        for a in best_g1:
            network_in_p2 |= self._outgoing_network(a, gs2)

        # Find span (leftmost..rightmost) in parent2's list
        pos2 = {a: i for i, a in enumerate(acts2)}
        span_positions = [pos2[a] for a in network_in_p2 if a in pos2]
        if not span_positions:
            return self._crossover_A(p1, p2)

        lo2, hi2 = min(span_positions), max(span_positions)
        segment = acts2[lo2: hi2 + 1]

        # Build child: acts1 with segment from acts2 inserted at correct location
        seg_set = {self._act_to_idx[a] for a in segment if a in self._act_to_idx}
        # Find corresponding span in acts1
        pos1 = {a: i for i, a in enumerate(acts1)}
        span1 = [pos1[a] for a in network_in_p2 if a in pos1]
        ins = min(span1) if span1 else len(acts1) // 2

        child_acts = [a for a in acts1 if self._act_to_idx[a] not in seg_set]
        # Insert segment at position (clamped)
        ins = min(ins, len(child_acts))
        for j, a in enumerate(segment):
            if a in self._act_to_idx:
                child_acts.insert(ins + j, a)

        child_order = [self._act_to_idx[a] for a in child_acts if a in self._act_to_idx]
        # Ensure all activities present
        present = set(child_order)
        child_order += [i for i in range(self._n) if i not in present]
        child_order = self._repair(child_order)
        return self._evaluate_with_fbi(child_order)

    # ------------------------------------------------------------------ #
    # Mutation — two-phase swap + insertion                                #
    # ------------------------------------------------------------------ #

    def _mutate(self, ind: Individual) -> Individual:
        """
        Two-phase mutation:
        Phase 1 — swap two random non-dummy activities (if precedence allows).
        Phase 2 — insert a random activity into its feasible window.
        FBI applied once at the end.
        """
        order = list(ind['order'])
        n = len(order)

        non_dummy_pos = [
            p for p in range(n)
            if self._activities[order[p]] not in self._dummy_acts
        ]
        if len(non_dummy_pos) < 2:
            return self._evaluate_with_fbi(order)

        # Phase 1: swap if feasible
        i, j = random.sample(non_dummy_pos, 2)
        if i > j:
            i, j = j, i
        act_i = self._activities[order[i]]
        act_j = self._activities[order[j]]
        backward = self.pert.backwardDict
        if (act_i not in backward.get(act_j, []) and
                act_j not in backward.get(act_i, [])):
            order[i], order[j] = order[j], order[i]

        # Phase 2: insertion into feasible window
        if len(non_dummy_pos) >= 1:
            cur_pos = random.choice(non_dummy_pos)
            act = self._activities[order[cur_pos]]
            pos_of = {order[p]: p for p in range(n)}
            preds = backward.get(act, [])
            succs = self.pert.forwardDict.get(act, [])
            lo = (max(pos_of[self._act_to_idx[pr]] for pr in preds
                      if pr in self._act_to_idx and self._act_to_idx[pr] in pos_of) + 1
                  if preds else 0)
            hi = (min(pos_of[self._act_to_idx[sc]] for sc in succs
                      if sc in self._act_to_idx and self._act_to_idx[sc] in pos_of) - 1
                  if succs else n - 1)
            if lo <= hi:
                new_pos = random.randint(lo, hi)
                if new_pos != cur_pos:
                    gene = order.pop(cur_pos)
                    order.insert(new_pos if new_pos < cur_pos else new_pos - 1, gene)

        return self._evaluate_with_fbi(order)

    # ------------------------------------------------------------------ #
    # Precedence repair                                                    #
    # ------------------------------------------------------------------ #

    def _repair(self, order: List[int]) -> List[int]:
        """Repair precedence feasibility via reorder_by_dependencies."""
        acts = self._chromosome_to_activities(order)
        ranked = [(a, i) for i, a in enumerate(acts)]
        try:
            repaired = self.pert.reorder_by_dependencies(ranked, self.pert.forwardDict)
            return [self._act_to_idx[a] for a, _ in repaired if a in self._act_to_idx]
        except Exception:
            return order

    # ------------------------------------------------------------------ #
    # Initial population                                                   #
    # ------------------------------------------------------------------ #

    def _build_initial_population(self) -> List[Individual]:
        """
        Build initial population from priority rules + random fill.
        No FBI applied (too expensive for initial population).
        """
        pop: List[Individual] = []
        seed_info: Dict[str, float] = {}

        for rule in PRIORITY_RULES:
            if len(pop) >= self.pop_size:
                break
            try:
                self.pert.priorities = None
                order = self._rule_to_order(rule)
                ind = self._evaluate_no_fbi(order)
                pop.append(ind)
                seed_info[rule] = ind['fitness']
            except Exception as exc:
                logger.warning("Skipping rule '%s': %s", rule, exc)

        while len(pop) < self.pop_size:
            try:
                self.pert.priorities = None
                order = self._rule_to_order('random')
                pop.append(self._evaluate_no_fbi(order))
            except Exception:
                break

        n_rand = len(pop) - len(seed_info)
        if self.verbose and seed_info:
            best_s = min(seed_info.values())
            print(f"\nGANS initial population: {len(seed_info)} rule-seeded "
                  f"+ {n_rand} random  |  best seeded = {best_s:.2f} h\n")
            print(f"  {'Rule':<22} {'Fitness (h)':>12}")
            print("  " + "-" * 36)
            for r, f in seed_info.items():
                print(f"  {r:<22} {f:>12.2f}")
            print()
        return pop

    # ------------------------------------------------------------------ #
    # Parent selection — probability-based scan (Section 5 of paper)      #
    # ------------------------------------------------------------------ #

    def _select_parents(
        self, pop: List[Individual], p_select: float = 0.25
    ) -> List[Individual]:
        """
        Scan population in non-decreasing fitness order; add each to Γ'
        with probability p_select until parents_size reached or all scanned.
        Guarantee at least the best individual is included.
        """
        sorted_pop = sorted(pop, key=lambda x: x['fitness'])
        parents: List[Individual] = [sorted_pop[0]]  # always include best
        for ind in sorted_pop[1:]:
            if len(parents) >= self.parents_size:
                break
            if random.random() < p_select:
                parents.append(ind)
        return parents

    # ------------------------------------------------------------------ #
    # Neighbourhood NA — block reschedule                                  #
    # ------------------------------------------------------------------ #

    def _create_block(
        self,
        core_act: Any,
        times: Dict[Any, Tuple[float, float]],
        P: int,
    ) -> List[Any]:
        """
        CreateBlock(j, S, P): collect P activities overlapping or near core_act.
        """
        s_j, e_j = times.get(core_act, (0.0, 0.0))
        p_j = e_j - s_j  # duration in hours
        others = [a for a in self._activities if a is not core_act]
        random.shuffle(others)

        block: List[Any] = [core_act]
        b = 0.0
        max_b = self._cpm_duration

        while len(block) < P and b <= max_b:
            added_this_round = False
            for a in others:
                if a in block:
                    continue
                s_i, e_i = times.get(a, (0.0, 0.0))
                p_i = e_i - s_i
                if s_j - p_i - b <= s_i <= s_j + p_j + b:
                    block.append(a)
                    if len(block) >= P:
                        break
                    added_this_round = True
            if not added_this_round:
                b += max(0.5, p_j / 4.0)

        return block

    def _na_neighbor(self, ind: Individual) -> Optional[Individual]:
        """
        Neighborhood NA: select core activity, extract block, reschedule it.

        The block activities are removed from their current order positions and
        re-inserted at their earliest feasible positions in the remaining list.
        """
        out = self._decode(ind['order'])
        times = self._get_schedule_times()
        order = self._order_from_schedule(times)

        non_dummy = [a for a in self._activities if a not in self._dummy_acts]
        if not non_dummy:
            return None
        core = random.choice(non_dummy)
        block = self._create_block(core, times, self._block_size)

        if not block:
            self._empty_block_history.append(True)
            return None
        self._empty_block_history.append(False)

        block_set = {self._act_to_idx[a] for a in block if a in self._act_to_idx}

        # Residual list (non-block activities in their current order)
        residual = [i for i in order if i not in block_set]

        # Compute EST for each block activity from its predecessors' positions
        # Insert block activities at their EST-derived positions in the residual
        pos_residual = {idx: p for p, idx in enumerate(residual)}
        for a in block:
            if a not in self._act_to_idx:
                continue
            a_idx = self._act_to_idx[a]
            preds = self.pert.backwardDict.get(a, [])
            lo = (max(pos_residual.get(self._act_to_idx[pr], -1)
                      for pr in preds
                      if pr in self._act_to_idx) + 1
                  if preds else 0)
            lo = max(0, lo)
            succs = self.pert.forwardDict.get(a, [])
            hi = (min(pos_residual.get(self._act_to_idx[sc], len(residual))
                      for sc in succs
                      if sc in self._act_to_idx) - 1
                  if succs else len(residual))
            hi = min(max(lo, hi), len(residual))
            ins = random.randint(lo, hi)
            residual.insert(ins, a_idx)
            # update position map
            pos_residual = {idx: p for p, idx in enumerate(residual)}

        child_order = self._repair(residual)
        fitness, best_order = self._fbi(child_order)
        return self._make_individual(best_order, fitness)

    # ------------------------------------------------------------------ #
    # Neighbourhood NB — split-list GRASP parallel SGS                    #
    # ------------------------------------------------------------------ #

    def _grasp_parallel_sgs(
        self,
        block_acts: List[Any],
        used_resources: Dict[str, float],
        n_restarts: int = 5,
    ) -> List[Any]:
        """
        Greedy-randomized selection of one activity from the eligible set,
        maximising weighted resource utilisation.

        Returns a ordering of block_acts chosen by GRASP.
        """
        alpha = 3  # restricted candidate list size
        scheduled: List[Any] = []
        eligible = list(block_acts)

        while eligible:
            # Score each eligible activity by its weighted resource demand
            scores = []
            for a in eligible:
                score = sum(
                    self._resource_weights.get(sk, 1.0)
                    * self._activity_demand[a].get(sk, 0.0)
                    / max(self._skill_capacity.get(sk, 1.0), 1.0)
                    for sk in self._skill_ids
                ) if self._skill_ids else 1.0
                # Penalise activities that would exceed remaining resource capacity
                feasible = True
                for sk in self._skill_ids:
                    cap = self._skill_capacity.get(sk, math.inf)
                    if used_resources.get(sk, 0.0) + self._activity_demand[a].get(sk, 0.0) > cap:
                        feasible = False
                        break
                scores.append((score if feasible else -1.0, self._act_to_idx.get(a, 0), a))

            scores.sort(key=lambda x: x[0], reverse=True)
            rcl = [a for _, _idx, a in scores[:alpha] if scores[0][0] >= 0]
            if not rcl:
                rcl = [eligible[0]]

            chosen = random.choice(rcl)
            scheduled.append(chosen)
            eligible.remove(chosen)
            for sk in self._skill_ids:
                used_resources[sk] = (
                    used_resources.get(sk, 0.0)
                    + self._activity_demand[chosen].get(sk, 0.0)
                )

        return scheduled

    def _nb_neighbor(self, ind: Individual) -> Optional[Individual]:
        """
        Neighborhood NB: split list L = A1 | block | A2.

        A1 scheduled with serial SGS; block with GRASP-parallel SGS; A2 appended.
        """
        order = list(ind['order'])
        n = len(order)
        non_dummy = [self._activities[i] for i in order
                     if self._activities[i] not in self._dummy_acts]
        if len(non_dummy) < 2:
            return None

        core = random.choice(non_dummy)
        core_idx = order.index(self._act_to_idx[core])

        # Decode for timing to build block
        self._decode(order)
        times = self._get_schedule_times()
        block_acts = self._create_block(core, times, self._block_size)

        # Exclude block members from the full order
        block_idx_set = {self._act_to_idx[a] for a in block_acts if a in self._act_to_idx}

        # Check: if any block member is a predecessor of core, skip
        preds_of_core = set(self.pert.backwardDict.get(core, []))
        if preds_of_core & set(block_acts):
            return None

        # Split at core position
        a1_order = [i for i in order[:core_idx] if i not in block_idx_set]
        a2_order = [i for i in order[core_idx + 1:] if i not in block_idx_set]

        # Schedule A1 fragment via serial SGS on a1 activities
        # GRASP the block
        used: Dict[str, float] = {}  # approx resource usage (just for guidance)
        grasp_order = self._grasp_parallel_sgs(block_acts, used, n_restarts=5)

        # Assemble full child order: A1 + GRASP block + A2
        grasp_indices = [self._act_to_idx[a] for a in grasp_order if a in self._act_to_idx]
        child_order = a1_order + grasp_indices + a2_order

        # Ensure every activity present exactly once
        present = set(child_order)
        child_order += [i for i in range(self._n) if i not in present]

        child_order = self._repair(child_order)
        fitness, best_order = self._fbi(child_order)
        return self._make_individual(best_order, fitness)

    # ------------------------------------------------------------------ #
    # Adaptive block-size update                                           #
    # ------------------------------------------------------------------ #

    def _update_block_size(self) -> None:
        """Increase P if most neighbors non-empty; decrease if mostly empty."""
        window = 20
        if len(self._empty_block_history) < window:
            return
        recent = self._empty_block_history[-window:]
        empty_rate = sum(recent) / window
        if empty_rate > 0.5 and self._block_size > 1:
            self._block_size -= 1
        elif empty_rate < 0.2 and self._block_size < max(10, self._n // 4):
            self._block_size += 1
        # Full reset if P dropped to 1
        if self._block_size == 1:
            self._block_size = self.block_size
            self._randomize_weights()

    # ------------------------------------------------------------------ #
    # Instance classification (σ-based)                                   #
    # ------------------------------------------------------------------ #

    def _assign_subset_params(self, best_fitness: float) -> None:
        """
        Classify instance into subset {1, 2, 3} by σ and adapt
        GA stall limit and NS steps accordingly.
        """
        cpm = max(self._cpm_duration, 1.0)
        sigma = (best_fitness - cpm) / cpm
        if sigma < self.sigma1:
            subset, self.ga_stall_limit, self.ns_steps = 1, 80, 50
        elif sigma <= self.sigma2:
            subset, self.ga_stall_limit, self.ns_steps = 2, 50, 150
        else:
            subset, self.ga_stall_limit, self.ns_steps = 3, 20, 300
        if self.verbose:
            print(f"  σ = {sigma:.3f} → subset {subset} | "
                  f"ga_stall={self.ga_stall_limit} | ns_steps={self.ns_steps}")

    # ------------------------------------------------------------------ #
    # Tabu list for NS                                                     #
    # ------------------------------------------------------------------ #

    def _tabu_key(self, ind: Individual) -> int:
        """Sum of start times as tabu signature (Paper Section 6)."""
        self._decode(ind['order'])
        times = self._get_schedule_times()
        return int(sum(s for s, _ in times.values()))

    # ------------------------------------------------------------------ #
    # Main optimisation loop                                               #
    # ------------------------------------------------------------------ #

    def run(self) -> Tuple[Individual, List[Dict]]:
        """
        Execute the GANS algorithm.

        Returns
        -------
        best : Individual
            Best individual found.
        log : list of dict
            Per-generation records with keys:
            ``n_evals``, ``best``, ``event``, ``ga_stall``, ``n_ns_activations``.
        """
        # ── Initialisation ─────────────────────────────────────────────
        pop = self._build_initial_population()
        pop.sort(key=lambda x: x['fitness'])
        n_evals = len(pop)

        best = self._make_individual(pop[0]['order'], pop[0]['fitness'])
        self._assign_subset_params(best['fitness'])

        ga_stall = 0
        n_ns_activations = 0
        p_parent = 0.25
        log: List[Dict] = []

        def _log(event: str = '') -> None:
            log.append({
                'n_evals': n_evals,
                'best': best['fitness'],
                'event': event,
                'ga_stall': ga_stall,
                'n_ns_activations': n_ns_activations,
            })

        _log('init')
        if self.verbose:
            print(f"{'n_evals':>8}  {'best (h)':>10}  {'event':<16}")
            print("-" * 40)
            print(f"{n_evals:>8}  {best['fitness']:>10.2f}  {'init':<16}")

        # ── Main loop ──────────────────────────────────────────────────
        gen = 0
        while n_evals < self.lambda_max:
            gen += 1
            parents = self._select_parents(pop, p_parent)
            if len(parents) < 2:
                parents = sorted(pop, key=lambda x: x['fitness'])[:2]

            # Produce offspring (one crossing per gen, per paper)
            p1 = random.choice(parents)
            p2 = random.choice(parents)
            cx = random.choice([self._crossover_A, self._crossover_B])
            child = cx(p1, p2)
            n_evals += 3  # FBI = 3 SGS calls

            # Apply mutation only if crossing didn't improve
            if child['fitness'] >= min(p1['fitness'], p2['fitness']):
                mutant = self._mutate(child)
                n_evals += 3
                if mutant['fitness'] <= child['fitness']:
                    child = mutant

            # Update best
            improved = child['fitness'] < best['fitness']
            if improved:
                best = self._make_individual(child['order'], child['fitness'])
                ga_stall = 0
            else:
                ga_stall += 1

            # Tournament update: add child, remove worst
            pop.append(child)
            pop.sort(key=lambda x: x['fitness'])
            pop = pop[:self.pop_size]

            # ── NS activation ─────────────────────────────────────────
            if ga_stall >= self.ga_stall_limit and n_evals < self.lambda_max:
                n_ns_activations += 1
                event = f"NS#{n_ns_activations}"

                # Start NS from current best individual
                ns_current = self._make_individual(best['order'], best['fitness'])
                tabu: List[int] = []
                tabu_key_current = self._tabu_key(ns_current)

                for _step in range(self.ns_steps):
                    if n_evals >= self.lambda_max:
                        break
                    ns_type = random.choice(['NA', 'NB'])
                    neighbor = (
                        self._na_neighbor(ns_current)
                        if ns_type == 'NA'
                        else self._nb_neighbor(ns_current)
                    )
                    n_evals += 3  # FBI inside neighbor

                    if neighbor is None:
                        continue

                    tk = self._tabu_key(neighbor)
                    n_evals += 1
                    if tk in tabu:
                        continue  # skip tabu

                    if neighbor['fitness'] < ns_current['fitness']:
                        ns_current = neighbor
                        tabu.append(tabu_key_current)
                        if len(tabu) > 10:
                            tabu.pop(0)
                        tabu_key_current = tk

                    if ns_current['fitness'] < best['fitness']:
                        best = self._make_individual(ns_current['order'], ns_current['fitness'])

                    self._update_block_size()

                # Reinject best into population; restart GA
                pop[0] = self._make_individual(best['order'], best['fitness'])
                ga_stall = 0
                self._randomize_weights()

                if self.verbose:
                    print(f"{n_evals:>8}  {best['fitness']:>10.2f}  {event:<16}")
                _log(event)
                continue

            if self.verbose and (gen % 50 == 0 or improved):
                tag = "*" if improved else ""
                print(f"{n_evals:>8}  {best['fitness']:>10.2f}  {tag:<16}")

            _log()

        logger.info(
            "GANS finished | best = %.2f h | evals = %d | NS activations = %d",
            best['fitness'], n_evals, n_ns_activations,
        )
        return best, log

    # ------------------------------------------------------------------ #
    # Result helpers                                                       #
    # ------------------------------------------------------------------ #

    def get_best_schedule(self, best: Individual) -> Dict:
        """Re-run SGS with the best activity order; leaves pert in best state."""
        acts = self._chromosome_to_activities(best['order'])
        return self.pert.calculateSerialScheduleWithResources(_ordered=acts)

    def get_best_activity_list(self, best: Individual) -> List[str]:
        """Return best scheduling order as a list of task names."""
        return [self._activities[i].returnName() for i in best['order']]

    def get_convergence_summary(self, log: List[Dict]) -> Dict:
        """
        Concise convergence summary.

        Returns dict with keys:
            ``n_evals``, ``best_duration``, ``initial_best``,
            ``improvement``, ``n_ns_activations``, ``final_stall``.
        """
        if not log:
            return {}
        return {
            'n_evals': log[-1]['n_evals'],
            'best_duration': log[-1]['best'],
            'initial_best': log[0]['best'],
            'improvement': log[0]['best'] - log[-1]['best'],
            'n_ns_activations': log[-1]['n_ns_activations'],
            'final_stall': log[-1]['ga_stall'],
        }
