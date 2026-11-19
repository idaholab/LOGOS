.. _sec-priority-rules:

Priority Rules
--------------

A priority rule assigns a value :math:`v(j)` to every activity :math:`j` that
is eligible for scheduling. A schedule generation scheme then uses these values
to decide which activity gets resources first
:cite:`Kolisch1996,KolischHartmann1999`. Priority rules are fast and
deterministic, and they are easy to explain to planners. For these reasons they
are the standard single-pass heuristics for the RCPSP. They also provide the
initial solutions for the metaheuristics in LOGOS.

Each rule states whether the activity with the **minimum** (min) or the
**maximum** (max) value is chosen first. The rules are classified as follows:

- **Static** rules depend only on the project data and the CPM analysis. They
  are computed once by ``Pert.generateInfo()``.
- **Dynamic** rules depend on the partial schedule, for example on the finish
  times of the scheduled predecessors or on the remaining resource capacity.
  They are recomputed at every decision time :math:`t`.

Following :cite:`Nekoueian2023`, the rules below are grouped by the
information they use: activity information, network information, resource
information, and scheduling (CPM) information. Dynamic rules, learned rules,
and hybrid combinations are described after these groups. The number in
parentheses after a rule name (for example, "N23 #2") is the rule's ID in
Table 4 of :cite:`Nekoueian2023`.

Notation
~~~~~~~~

In addition to the symbols in :ref:`sec-sm-rcpsp`, the rules use the
following notation.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Symbol
     - Meaning
   * - :math:`t`
     - current decision time of the scheduler
   * - :math:`D_t`
     - decision set: the activities that are eligible at time :math:`t`
   * - :math:`\tilde a_k(t)`
     - remaining capacity of resource :math:`k` at time :math:`t`
   * - :math:`u_{jk} = r_{jk}/a_k`
     - normalized demand of :math:`j` on :math:`k` (equal to 1 for an
       occupied location zone)
   * - :math:`W_j = d_j \sum_{k} r_{jk}`
     - work content of activity :math:`j`
   * - :math:`Level_j`
     - number of activities on the longest path from :math:`j` to the end of
       the project: :math:`Level_j = 1 + \max_{i \in S_j} Level_i`, with
       :math:`Level = 1` for the last activity
   * - :math:`E(i, j)`
     - earliest precedence- and resource-feasible start of :math:`i` if
       :math:`j` starts at :math:`t`
   * - :math:`F_i`
     - actual (scheduled) finish time of activity :math:`i`

In LOGOS, the resource set :math:`K` contains three kinds of resources: crew
skills, for which :math:`r_{jk}` is the crew count; equipment items, for which
:math:`r_{jk}` is the quantity needed; and location zones, for which
:math:`r_{jk} = 1` for each occupied zone.

Using priority rules
~~~~~~~~~~~~~~~~~~~~

A rule is selected by its key, passed as the ``priority_rule`` argument of the
scheduler:

.. code-block:: python

   from LOGOS.src.CPM import Pert

   pert = Pert.from_json_file("example_10.json",
                              schema_path="outage_schema.json")
   pert.generateInfo()                          # CPM values and static metrics

   # Parallel (event-driven) SGS
   result = pert.calculateScheduleWithResources(priority_rule="lrpw")
   print(result["scheduled_duration"])

   # Serial SGS
   result = pert.calculateSerialScheduleWithResources(priority_rule="lf")

   # Hybrid rule (see below)
   result = pert.calculateScheduleWithResources(priority_rule="hybrid:lrpw+scrr")

Keys are case-insensitive. The scheduler applies a rule as follows:

1. At each decision time :math:`t`, the scheduler computes :math:`v(j)` for
   every activity in :math:`D_t`.
2. The activities are sorted by :math:`v(j)` in the rule's direction. For
   static rules, ties are broken by the learned rule ``mehh_8000_b``. For
   dynamic and hybrid rules, ties are broken by ``lf``.
3. Any order that would violate precedence is repaired, so that predecessors
   always come before their successors.
4. The activities are started in this order while crews, equipment, and
   locations allow.

**Parallel and serial schemes.** In the parallel scheme
(``calculateScheduleWithResources``), dynamic rules are evaluated at every
event time and so react to the actual progress of the schedule. In the
serial scheme (``calculateSerialScheduleWithResources``), all activities are
ranked once, at :math:`t = 0`, before anything is scheduled. Dynamic rules
therefore reduce to their static counterparts there: ESTD becomes EST, EFTD
becomes EFT, MSLD becomes MSLK, and DRC/DRS are measured against the full
capacity. Use the parallel scheme to get the intended behavior of the dynamic
rules.

**Other ways to set priorities.** Two assignment modes produce priority values
without using a ranking rule:

- ``TF_based`` (the default when neither external priorities nor a rule are
  given) uses a sigmoid of the total float:

  .. math::

     v(j) = 1 - \frac{1}{1 + \exp\!\big(\theta - \min(TF_j,\, TF^{WBS}_j)\big)},
     \qquad \theta = \max(5,\ 0.01\,T_{cpm}).

  :math:`TF^{WBS}_j` is the aggregate float of the work-breakdown package that
  contains :math:`j`. Because the smaller of the two floats is used, a package
  with any critical member is prioritized as a whole. A higher value is
  scheduled first.

- ``external`` priorities are supplied by the user, by RAVEN (through
  ``<map attr='priority'>``, see :ref:`sec-CPM`), or by a metaheuristic,
  through the ``priorities`` constructor argument or ``set_priorities``. When
  external priorities are set, they take precedence over ``priority_rule``.
  Activities without a value get 0.5.

Activity-information rules
~~~~~~~~~~~~~~~~~~~~~~~~~~

These rules use only the attributes of the activity itself.

**SPT: Shortest Processing Time** (N23 #2). Key ``duration``, min.

.. math:: v(j) = d_j

Short activities start first, which frees resources quickly. The rule ignores
the network, so it is mainly a baseline and a component of hybrid rules.

**LPT: Longest Processing Time** (N23 #1) :cite:`Liu2025`. Key ``lpt``, max.

.. math:: v(j) = d_j

Long activities start early so that they do not become the project tail. This
is useful when a few long tasks, such as turbine or generator work, dominate
the outage.

**N: Minimum task number** (N23 #43) :cite:`Capacho2007`. Key ``act_index``,
min.

.. math:: v(j) = \text{position of } j \text{ in the input order}

This rule follows the order in which the planner listed the tasks. It serves
as a reference rule and as a deterministic tie-breaker.

Network-information rules
~~~~~~~~~~~~~~~~~~~~~~~~~

These rules use the structure of the precedence network.

**MIS / LIS: Most / Least Immediate Successors** (N23 #3, #4)
:cite:`Liu2025`. Keys ``mis`` (max) and ``lis`` (min).

.. math:: v(j) = |S_j|

MIS starts the activities that release the most work right away.

**MTS / LTS: Most / Least Total Successors** (N23 #5, #6)
:cite:`KolischHartmann1999,Liu2025`. Keys ``mts_exact`` (max) and ``lts`` (min).

.. math:: v(j) = |\bar S_j|

MTS gives priority to activities that gate large parts of the network, and it
is a robust general-purpose rule. LTS is its mirror and is mainly useful for
diversity in multi-pass runs.

The key ``mts`` (max) is a LOGOS variant of MTS that counts a shared
descendant once for every path to it. It is computed by
:math:`v(j) = \sum_{i \in S_j} \big(1 + v(i)\big)`, which gives more weight to
activities upstream of merging paths. ``mts`` is kept for reproducibility of
earlier results; ``mts_exact`` gives the literature definition.

**MTP: Most Total Predecessors** (LOGOS addition). Keys ``mtp_exact`` and
``mtp`` (path-count variant), max.

.. math:: v(j) = |\bar P_j|

This rule favors activities deep in the network. Its main use is as an input
of the learned rules.

**NP: Number of Predecessors** (N23 #38, adapted). Key ``np``, min.

.. math:: v(j) = |P_j|

In :cite:`Capacho2007`, NP ranks subgraphs. LOGOS applies it at the activity
level, where it favors loosely constrained work.

**LFS: Least Float per Successor** (N23 #7) :cite:`Liu2025`. Key ``lfs``, min.

.. math:: v(j) = \frac{LS_j - ES_j}{\max(1, |S_j|)}

This rule combines urgency (float) with impact (number of immediate
successors). An activity with little float and many successors goes first.

**LNRJ: Least Non-Related Jobs** (N23 #8) :cite:`Liu2025,Luo2023`. Key
``lnrj``, min.

.. math:: v(j) = n - |\bar P_j| - |\bar S_j|

Activities that are precedence-related to many others, and therefore cannot
run in parallel with them, are scheduled first. Activities that can run in
parallel with many others are left as flexible fill work.

**LPF: Longest Path Following** (N23 #9) :cite:`Liu2025,Luo2023`. Key
``lpf``, max.

.. math:: v(j) = Level_j

:math:`Level_j` counts *activities*, not hours, on the longest path to the
end. It measures the topological depth still to go, so it differs from LST
when durations vary.

**MS/L: Most Successors per Level** (N23 #10) :cite:`Liu2025,Luo2023`. Key
``ms_l``, max.

.. math:: v(j) = \frac{|\bar S_j|}{Level_j}

This rule favors activities that open up a wide downstream network within a
short chain.

**GRPW / RPW: (Greatest) Rank Positional Weight** (N23 #11)
:cite:`AlvarezValdes1989,KolischHartmann1999,Liu2025`. Keys ``grpw_imm`` and ``rpw``,
max.

.. math::

   \text{GRPW}_j = d_j + \sum_{i \in S_j} d_i, \qquad
   \text{RPW}_j  = d_j + \sum_{i \in \bar S_j} d_i

These rules favor activities with a large downstream workload. GRPW is among
the best rules for the parallel SGS :cite:`AlvarezValdes1989`.

The key ``grpw`` (max) is a LOGOS variant that sums the durations of the
*predecessors* over all paths, :math:`v(j) = d_j + \sum_{\text{paths to } j}
d_{\text{pred}}`. It rewards activities late in the network, which is the
opposite of the classical intent. It is kept because earlier optimization
baselines depend on it. Prefer ``grpw_imm`` or ``rpw`` in new studies.

**LRPW: Least Rank Positional Weight** (N23 #13)
:cite:`Nekoueian2023,Liu2025`. Keys ``lrpw`` and ``lrpw_imm``, min.

.. math::

   \texttt{lrpw}:\ v(j) = d_j + \sum_{i \in \bar S_j} d_i, \qquad
   \texttt{lrpw\_imm}:\ v(j) = d_j + \sum_{i \in S_j} d_i

``lrpw`` follows the worked example in :cite:`Nekoueian2023`, which uses all
successors. ``lrpw_imm`` follows the immediate-successor definition in
:cite:`Liu2025`. Short, light subnetworks are completed first. Although this
seems counterintuitive, LRPW was the best single rule in the study of
:cite:`Nekoueian2023`.

**GPRPW: Greatest Proportional Rank Positional Weight** (N23 #12)
:cite:`Liu2025`. Key ``gprpw``, max.

.. math:: v(j) = \frac{d_j + \sum_{i \in S_j} d_i}{d_j}

This is GRPW relative to the activity's own duration. It favors short
activities with long immediate successors. For zero-duration activities
(dummies and milestones), :math:`v(j) = 1 + \sum_{i \in S_j} d_i`.

**MTSPT: Most Total Successors Processing Time** (N23 #19) :cite:`Liu2025`.
Key ``mtspt``, max.

.. math:: v(j) = \sum_{i \in \bar S_j} d_i

This is RPW without the activity's own duration.

**TTS: Task time plus total successors** (N23 #45, adapted)
:cite:`Capacho2007`. Key ``tts_alb``, max.

.. math:: v(j) = d_j + |\bar S_j|

The two terms have different units (hours and a count), so treat this rule as
experimental.

**STS: Maximum average successor time** (N23 #46, adapted)
:cite:`Capacho2007`. Key ``sts``, max.

.. math:: v(j) = \frac{\sum_{i \in \bar S_j} d_i}{\max(1, |\bar S_j|)}

**TSSK: Total successors divided by slack** (N23 #47, adapted)
:cite:`Capacho2007`. Key ``tssk``, max.

.. math:: v(j) = \frac{|\bar S_j|}{TF_j + 1}

This is a ratio of impact to urgency, similar in spirit to LFS. In
:cite:`Capacho2007`, the slack is measured in workstations; LOGOS uses the
total float in hours.

Resource-information rules
~~~~~~~~~~~~~~~~~~~~~~~~~~

These rules use the resource demands of the activities. In LOGOS, the demand
combines crew skills, equipment, and location zones.

**GRD / SRD: Greatest / Smallest Resource Demand** (N23 #20)
:cite:`Liu2025`. Keys ``grd`` (max) and ``srd`` (min).

.. math:: v(j) = d_j \sum_{k \in K} r_{jk} = W_j

GRD is the work content of the activity. It starts heavy crew and equipment
jobs early so that they do not collide later. The sum is not normalized, so
plentiful resources (for example, laborers) dominate scarce ones (for example,
cranes). When capacities differ widely, prefer ``trs`` or ``avgrr``.

**GRR / SRR: Greatest / Smallest Resource Requirement** (N23 #20, #21)
:cite:`Liu2025`. Keys ``grr`` (max) and ``srr`` (min).

.. math:: v(j) = \sum_{k \in K} r_{jk}

**TRD: Total Resource Demand** (N23 #24) :cite:`Liu2025`. Key ``trd``, min.
TRD has the same definition as SRR and is registered as a separate key.

**RR family: normalized resource requirement.** Keys ``rr``, ``avgrr``,
``maxrr``, and ``minrr``, all max.

.. math::

   \text{rr}_j = \frac{|\{k : u_{jk} > 0\}|}{|K|}, \quad
   \text{avgrr}_j = \frac{1}{|K|} \sum_{k} u_{jk}, \quad
   \text{maxrr}_j = \max_{k} u_{jk}, \quad
   \text{minrr}_j = \min_{k} u_{jk}

``maxrr`` is the bottleneck rule: it favors activities that use a large share
of some scarce resource. ``minrr`` is usually zero, because few activities
use every resource. These four quantities are also inputs of the learned
rules.

**TRS: Total Resource Scarcity** (N23 #25) :cite:`Liu2025`. Key ``trs``, min.

.. math:: v(j) = \sum_{k \in K} \frac{r_{jk}}{a_k}

Activities that use little of the scarce capacity go first. This is the
reverse of ``avgrr``.

**GCRR / SCRR: Greatest / Smallest Cumulative Resource Requirement**
(N23 #22, #23) :cite:`Nekoueian2023,Liu2025`. Keys ``gcrr`` (max) and
``scrr`` (min).

.. math:: v(j) = W_j + \sum_{i \in S_j} W_i

This is the work content of :math:`j` plus that of its immediate successors.
SCRR was the second-best single rule in :cite:`Nekoueian2023`.

**WRUP: Weighted Resource Utilization and Precedence** (N23 #30)
:cite:`Liu2025`. Key ``wrup``, max.

.. math:: v(j) = 0.7\,|S_j| + 0.3 \sum_{k \in K} \frac{r_{jk}}{a_k}

The successor count is not normalized, so it usually dominates the resource
term.

**WACRU: Weighted Activity Criticality and Resource Utilization** (N23 #29)
:cite:`Wang2017,Luo2023`. Key ``wacru``, max.

.. math::

   v(j) = w \sum_{q \in S_j} \big(1 + TF_q\big)^{-\alpha}
        + (1 - w) \sum_{k \in K} \frac{r_{jk}}{a_k}

The first term measures criticality: many critical immediate successors give
a high value. :cite:`Wang2017` uses :math:`w = \alpha = 0.5`, while
:cite:`Nekoueian2023` uses :math:`w = 0.3`. LOGOS uses :math:`w = 0.3` and
:math:`\alpha = 0.5`.

Scheduling-information rules
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

These rules use the CPM times from :ref:`sec-pert-class`
:cite:`DavisPatterson1975,Kolisch1996`. The CPM values include lags,
mobilization leads, and time-window tightening.

.. list-table::
   :header-rows: 1
   :widths: 30 12 30 10

   * - Rule (N23 ID)
     - Key
     - :math:`v(j)`
     - Dir.
   * - EST: Earliest Start Time (#14)
     - ``es``
     - :math:`ES_j`
     - min
   * - EFT: Earliest Finish Time (#15)
     - ``ef``
     - :math:`EF_j`
     - min
   * - LST: Latest Start Time (#16)
     - ``ls``
     - :math:`LS_j`
     - min
   * - LFT: Latest Finish Time (#17), the default
     - ``lf``
     - :math:`LF_j`
     - min
   * - MSLK: Minimum Slack (#18)
     - ``mslk``
     - :math:`TF_j = LS_j - ES_j`
     - min
   * - Max LST (#54)
     - ``max_lst``
     - :math:`LS_j`
     - max
   * - Max LFT (#55)
     - ``max_lft``
     - :math:`LF_j`
     - max

LFT and LST are the classical choices for meeting deadlines. They work well on
outage networks where the critical path dominates. MSLK often does better on
resource-heavy instances. EST and EFT follow the CPM plan, much like
first-in-first-out. Max LST and Max LFT were designed for choosing between
alternative subgraphs. In a plain RCPSP they deliberately defer critical work,
so use them only for diversity.

Dynamic rules
~~~~~~~~~~~~~

Dynamic rules are recomputed at each decision time :math:`t` from the partial
schedule.

**WCS: Worst Case Slack** :cite:`Kolisch1996`. Key ``wcs``, min.

.. math:: v(j) = LS_j - \max_{i \in D_t,\, i \ne j} E(j, i)

If :math:`|D_t| = 1`, this reduces to :math:`LS_j - t`. WCS is one of the
strongest classical rules for the parallel SGS
:cite:`Kolisch1996,KolischHartmann1999`. It needs :math:`O(|D_t|^2)` pairwise
feasibility checks per decision.

**ACS: Average Case Slack** :cite:`Kolisch1996`. Key ``acs``, min.

.. math:: v(j) = LS_j - \frac{1}{|D_t|} \sum_{i \in D_t,\, i \ne j} E(j, i)

This is a less pessimistic version of WCS.

**IRSM: Improved Resource Scheduling Method** :cite:`Kolisch1996`. Key
``irsm``, min.

.. math:: v(j) = \max_{i \in D_t,\, i \ne j} \max\big(0,\ E(i, j) - LS_i\big)

IRSM picks the activity whose start delays the other candidates least beyond
their latest start times.

**RSM: Resource Scheduling Method** (N23 #36) :cite:`Shaffer1965`. Key
``rsm``, min.

.. math:: v(j) = \max_{i \in D_t,\, i \ne j} \max\big(0,\ t + d_j - LS_i\big)

RSM assumes that every other candidate :math:`i` waits for :math:`j` to
finish. It is cheaper than IRSM because no pairwise feasibility check is
needed.

**ESTD / EFTD / MSLD: Dynamic EST, EFT, and Slack** (N23 #31–33)
:cite:`Klein2000,Liu2025,Wang2017`. Keys ``estd``, ``eftd``, and ``msld``,
min.

.. math::

   ES_j(t) = \max\Big(t,\ \max_{i \in P_j} \big(F_i + \ell_{ij}\big)\Big),
   \qquad
   \text{EFTD}_j = ES_j(t) + d_j,
   \qquad
   \text{MSLD}_j = LS_j - ES_j(t)

MSLD is MSLK updated with the actual delays. It was the best dynamic rule in
:cite:`Nekoueian2023`. In the LOGOS parallel SGS, every candidate's
predecessors have already finished, so :math:`ES_j(t) = t`. As a result,
ESTD ties for all candidates (and is decided by its ``lf`` tie-breaker), MSLD
ranks like LST, and EFTD ranks like SPT.

**DRD / DRC / DRS: Dynamic resource rules** (N23 #26–28)
:cite:`SchirmerRiesenberg1997,Liu2025`. Keys ``drd``, ``drc``, and ``drs``,
min.

.. math::

   \text{DRD}_j = \sum_{k} \frac{r_{jk}}{\max_{h \in D_t} r_{hk}}, \qquad
   \text{DRC}_j = \sum_{k} \big(\tilde a_k(t) - r_{jk}\big), \qquad
   \text{DRS}_j = \sum_{k} \frac{r_{jk}}{\tilde a_k(t)}

DRD scales each demand by the largest demand in the decision set and skips
resources that no candidate uses. DRC prefers activities that leave the least
capacity unused. DRS is TRS measured against the *remaining* capacity. If
:math:`\tilde a_k(t) = 0` while :math:`r_{jk} > 0`, the activity cannot start,
and DRS is :math:`+\infty`.

**NONDEP: Least Non-Dependent jobs** (N23 #34) :cite:`Cooper1976`. Key
``nondep``, min.

.. math:: v(j) = \big|\{ i \in D_t \setminus \{j\} : i \notin \bar S_j \cup \bar P_j \}\big|

This is LNRJ restricted to the current decision set. The LOGOS formula is an
interpretation, because the original definition could not be consulted. As
with ESTD, candidates in the LOGOS parallel SGS are never precedence-related,
so NONDEP ties for all candidates.

**MAXSP: Maximum Schedule Pressure** (N23 #35)
:cite:`BrowningYassine2010,Wang2017`. Key ``maxsp``, max.

.. math:: v(j) = \frac{t - LF_j}{d_j\, \rho_j}

:math:`\rho_j` is the fraction of :math:`j`'s duration that remains: it is 1
for activities that have not started and is less than 1 during replanning.
For zero-duration activities, :math:`d_j \rho_j = 1`. MAXSP prioritizes
activities that are already late relative to their deadline. It was designed
for multi-project settings.

**RAN: Random** (N23 #37). Key ``random``. This rule produces a uniformly
random order, seeded through ``Pert(seed=...)``. It serves as a baseline and as
a source of diversity for sampling methods.

Learned rules
~~~~~~~~~~~~~

The keys ``mehh_8000_b``, ``mehh_3375_b``, ``mehh_1000_b``, ``mehh_125_b``, and
``gphh_b`` are closed-form priority expressions evolved by genetic
programming on benchmark RCPSP instances. This is the automated design of
priority rules studied in :cite:`Luo2023`. All of them are min rules. Their
inputs are normalized by the network-wide maximum of each quantity:

.. math::

   \widehat{ES},\ \widehat{EF},\ \widehat{LS},\ \widehat{LF},\quad
   TPC = \frac{mtp}{\max mtp},\quad TSC = \frac{mts}{\max mts},\quad
   RR,\ AvgRReq,\ MaxRReq,\ MinRReq,

where :math:`\widehat{ES} = ES / \max ES` (and similarly for the other CPM
times), and RR, AvgRReq, MaxRReq, and MinRReq are ``rr``, ``avgrr``,
``maxrr``, and ``minrr``. For example, ``gphh_b`` is

.. math::

   v = -AvgRReq - \widehat{EF} - \widehat{ES} - \widehat{LF} - 2\widehat{LS}
       - \max(AvgRReq, TSC) - \min(AvgRReq, -TSC) + \min(\widehat{EF}, \widehat{LS}).

The other expressions are defined in ``src/CPM/cpm_utils.py``. The learned
rules are often better than any single classical rule. Because they were
trained with the path-count variants ``mts`` and ``mtp``, those variants must
be kept as their inputs.

Hybrid rules
~~~~~~~~~~~~

A hybrid rule combines the raw values of two or more rules
:cite:`Nekoueian2023`. Its key has the form ``hybrid:<a>+<b>[+<c>...]``, for
example ``hybrid:lrpw+scrr`` or ``hybrid:lrpw+scrr+ms_l``. Any static or
dynamic rule except ``random`` can be a component.

- If all components have the same direction, the values are added,
  :math:`v(j) = \sum_r v_r(j)`, and the hybrid uses that direction.
- If the directions are mixed, the hybrid is a min rule with

  .. math::

     v(j) = \frac{\sum_{r \in \text{min rules}} v_r(j)}
                 {\sum_{r \in \text{max rules}} v_r(j)} .

  A non-positive denominator gives :math:`+\infty` (lowest priority).

A hybrid that contains a dynamic component is recomputed at every decision
time. Because raw values are combined, a component with larger values
dominates. For example, SCRR (work content) usually outweighs LRPW (hours) in
``hybrid:lrpw+scrr``. In :cite:`Nekoueian2023`, no hybrid beat LRPW alone, but
adding MSLD improved the weaker components, such as SCRR and MS/L.

Choosing a rule
~~~~~~~~~~~~~~~

- For a single deterministic pass without tuning, start with ``lf`` (the
  default), ``mslk``, or ``lrpw``.
- In the study of :cite:`Nekoueian2023`, the best single rules by average
  makespan were LRPW, SCRR, MS/L, MSLD, NONDEP, MTS, and LNRJ, and the worst
  were MAXSP, LFT, and LST. Those results were obtained on instances with
  alternative subgraphs. Results on plain RCPSP instances and on outage data
  can differ, so benchmark the rules on representative schedules before
  choosing one.
- When schedule quality matters more than speed, run several rules and keep
  the best schedule (multi-pass), or use a metaheuristic seeded with
  rule-based solutions.
- The dynamic resource-aware rules ``wcs``, ``acs``, and ``irsm`` need
  :math:`O(|D_t|^2)` feasibility checks per decision. Use them on small and
  medium networks.

Quick reference
~~~~~~~~~~~~~~~

S = static, D = dynamic.

.. list-table::
   :header-rows: 1
   :widths: 38 30 18 14

   * - Keys
     - Rules
     - Direction
     - Type
   * - ``duration``, ``lpt``, ``act_index``
     - SPT, LPT, N
     - min, max, min
     - S
   * - ``mis``, ``lis``
     - MIS, LIS
     - max, min
     - S
   * - ``mts_exact``, ``mts``, ``lts``
     - MTS, MTS (path count), LTS
     - max, max, min
     - S
   * - ``mtp_exact``, ``mtp``, ``np``
     - MTP, MTP (path count), NP
     - max, max, min
     - S
   * - ``lfs``, ``lnrj``, ``lpf``, ``ms_l``
     - LFS, LNRJ, LPF, MS/L
     - min, min, max, max
     - S
   * - ``grpw_imm``, ``rpw``, ``grpw``, ``gprpw``, ``mtspt``
     - GRPW, RPW, GRPW (LOGOS), GPRPW, MTSPT
     - max
     - S
   * - ``lrpw``, ``lrpw_imm``
     - LRPW
     - min
     - S
   * - ``tts_alb``, ``sts``, ``tssk``
     - TTS, STS, TSSK
     - max
     - S
   * - ``grd``, ``srd``, ``grr``, ``srr``, ``trd``
     - GRD, SRD, GRR, SRR, TRD
     - max, min, max, min, min
     - S
   * - ``rr``, ``avgrr``, ``maxrr``, ``minrr``
     - normalized requirement
     - max
     - S
   * - ``trs``, ``gcrr``, ``scrr``, ``wrup``, ``wacru``
     - TRS, GCRR, SCRR, WRUP, WACRU
     - min, max, min, max, max
     - S
   * - ``es``, ``ef``, ``ls``, ``lf``, ``mslk``
     - EST, EFT, LST, LFT, MSLK
     - min
     - S
   * - ``max_lst``, ``max_lft``
     - Max LST, Max LFT
     - max
     - S
   * - ``mehh_8000_b``, ``mehh_3375_b``, ``mehh_1000_b``, ``mehh_125_b``, ``gphh_b``
     - learned (GP)
     - min
     - S
   * - ``wcs``, ``acs``, ``irsm``, ``rsm``
     - WCS, ACS, IRSM, RSM
     - min
     - D
   * - ``estd``, ``eftd``, ``msld``
     - ESTD, EFTD, MSLD
     - min
     - D
   * - ``drd``, ``drc``, ``drs``, ``nondep``
     - DRD, DRC, DRS, NONDEP
     - min
     - D
   * - ``maxsp``
     - MAXSP
     - max
     - D
   * - ``random``
     - RAN
     - n/a
     - n/a
   * - ``hybrid:<a>+<b>[+...]``
     - hybrid
     - sum, or min/max ratio
     - S or D
   * - ``TF_based``, ``external``
     - assignment modes
     - higher value first
     - n/a
