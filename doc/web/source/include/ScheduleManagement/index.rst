.. _sec-schedule-management:

Planning and Resource Intelligence for Schedule Management
==========================================================

LOGOS provides a scheduling engine for large, resource-intensive projects such
as nuclear power plant refueling outages. An outage consists of thousands of
maintenance, inspection, and surveillance activities. Each activity requires
qualified crews, equipment, and workspace. Activities are linked by
technological precedence relations and are further restricted by plant system
states, regulatory time windows, radiation-dose budgets, and shift calendars.
The planning goal is to complete all work as early as possible, which shortens
the time the unit is off-line, while respecting every constraint.

The CPM module (``src/CPM``) handles this planning task at three levels:

- **Network analysis.** It computes the classical Critical Path Method (CPM)
  and Program Evaluation and Review Technique (PERT) timing quantities, the
  activity float, and the critical path of the unconstrained project network.
- **Resource-constrained scheduling.** It builds a feasible schedule under the
  limited availability of crews, equipment, and locations with an
  event-driven schedule generation scheme. A *priority rule* decides which
  eligible activity starts first.
- **Heuristic optimization.** It improves the schedule with metaheuristics
  that search over activity priorities and execution modes.

The engine runs standalone from Python or inside a RAVEN workflow, where
activity durations or priorities are sampled for uncertainty quantification
and optimization (see :ref:`sec-CPM`). This section is organized as follows.
It first states the resource-constrained project scheduling problem (RCPSP)
that the engine addresses. It then describes the ``Pert`` scheduling class,
which implements the CPM/PERT analysis and the resource-constrained scheduler.
Next, it documents the available priority rules. A description of the
heuristic optimization algorithms will follow in a later subsection.

.. The heuristic optimization subsection (GA, GGA, GANS, ALNS) will be added
   as ``heuristics.rst`` in this folder.

.. toctree::
   :maxdepth: 2

   rcpsp_overview
   pert
   priority_rules
