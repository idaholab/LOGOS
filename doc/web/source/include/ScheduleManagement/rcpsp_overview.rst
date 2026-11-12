.. _sec-sm-rcpsp:

The Resource-Constrained Project Scheduling Problem
---------------------------------------------------

Problem statement
~~~~~~~~~~~~~~~~~

The resource-constrained project scheduling problem (RCPSP) asks for start
times for the activities of a project so that the project finishes as early as
possible. Precedence relations between activities and limited renewable
resources must both be respected :cite:`Blazewicz1983,KolischHartmann1999`.

A project consists of a set :math:`J = \{0, 1, \dots, n, n+1\}` of activities.
Activities :math:`0` and :math:`n+1` are dummy activities with zero duration.
They mark the start and the end of the project. Each real activity
:math:`j` has a duration :math:`d_j`, and once it starts it runs without
interruption. The precedence relations form a directed acyclic graph (the
*activity-on-node* network). Activity :math:`j` cannot start before all of its
immediate predecessors :math:`P_j` have finished. A set :math:`K` of renewable
resources is available. Resource :math:`k` provides :math:`a_k` units in every
period, and activity :math:`j` uses :math:`r_{jk}` units of resource :math:`k`
in every period in which it is in process.

A schedule is a vector of start times :math:`(s_0, s_1, \dots, s_{n+1})` with
finish times :math:`f_j = s_j + d_j`. Let :math:`\mathcal{A}(t)` be the set of
activities in process at time :math:`t`. A schedule is *feasible* if

.. math::

   \begin{aligned}
   & s_j \ge f_i, && \forall j \in J,\; i \in P_j, \\
   & \sum_{j \in \mathcal{A}(t)} r_{jk} \le a_k, && \forall k \in K,\; \forall t, \\
   & s_0 = 0 .
   \end{aligned}

The objective is to minimize the makespan :math:`f_{n+1}`. A time-indexed
mixed-integer formulation of this problem, solved exactly with Pyomo, is given
in :ref:`sec-RCPSP`.

Notation
~~~~~~~~

The following symbols are used throughout this section.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Symbol
     - Meaning
   * - :math:`j`, :math:`n`
     - an activity; the number of real activities
   * - :math:`d_j`
     - duration of activity :math:`j` (hours)
   * - :math:`P_j`, :math:`S_j`
     - immediate predecessors / immediate successors of :math:`j`
   * - :math:`\bar P_j`, :math:`\bar S_j`
     - all (transitive) predecessors / successors of :math:`j`
   * - :math:`ES_j, EF_j, LS_j, LF_j`
     - early start, early finish, late start, and late finish from the CPM
       analysis (see :ref:`sec-pert-class`)
   * - :math:`TF_j = LS_j - ES_j`
     - total float (slack) of :math:`j`
   * - :math:`K`
     - set of renewable resources. In LOGOS this includes crew skills,
       equipment items, and location zones
   * - :math:`r_{jk}`
     - demand of activity :math:`j` for resource :math:`k`
   * - :math:`a_k`
     - maximum availability (capacity) of resource :math:`k`

Complexity and solution approaches
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The RCPSP generalizes the job-shop scheduling problem and is strongly NP-hard
:cite:`Blazewicz1983`. Exact methods, such as the mixed-integer program in
:ref:`sec-RCPSP` or branch-and-bound, prove optimality for projects with up to
a few dozen activities. Outage schedules with hundreds or thousands of
activities and side constraints are beyond their reach in practical run times.
Practical tools therefore rely on heuristics
:cite:`KolischHartmann1999,HartmannKolisch2000,KolischHartmann2006`. These
fall into two families:

- **Priority-rule-based heuristics.** A *schedule generation scheme* (SGS)
  builds a feasible schedule step by step, and a *priority rule* decides which
  activity to schedule next :cite:`Kolisch1996SGS,Kolisch1996`. The *serial*
  SGS takes the activities one at a time in priority order and starts each at
  its earliest precedence- and resource-feasible time. The *parallel* SGS
  moves forward in time. At each decision point it starts as many eligible
  activities as the remaining resources allow, in priority order. One pass
  takes milliseconds to seconds, even for large networks. The priority rules
  available in LOGOS are described in :ref:`sec-priority-rules`.
- **Metaheuristics.** Genetic algorithms, large neighborhood search, and
  related methods search over the representations that an SGS decodes, such
  as priority vectors or activity lists :cite:`Hartmann1998`. They trade
  computing time for schedule quality, and they usually start from solutions
  produced by priority rules.

Extensions modeled in LOGOS
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Nuclear outage planning needs several features beyond the classical RCPSP. The
LOGOS scheduler supports the following:

- time-varying resource availability and shift calendars;
- crew skill substitution, zone-locked equipment, and multi-zone location
  occupancy;
- consumable (non-renewable) resources with restocking;
- plant system-state (isolation) conflicts and radiation-dose budgets;
- finish-to-start lags, mobilization lead times, regulatory time windows, and
  hold points;
- multiple execution modes per activity (the multi-mode RCPSP).

The modeling details are given in :ref:`sec-pert-class`.
