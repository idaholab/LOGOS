.. _sec-RCPSP:

Resource-Constrained Project Scheduling Problem (RCPSP)
=======================================================

In this section, we consider the resource-constrained project scheduling
problem (RCPSP). In RCPSP, the activities of a project have to be
scheduled such that the makespan of the project is minimized. Thereby,
technological precedence constraints must be observed, as well as
limited capacities of the renewable resources that are required to
accomplish the activities. The RCPSP for the scheduling of maintenance
and surveillance activities of a nuclear power plant can be summarized
as follows.

We consider a project that consists of a set of :math:`J` jobs
(or tasks). Due to technological requirements, precedence relations
among some of the jobs enforce that job :math:`j = 2,3,\dots,J` may not
be started before all its predecessors, denoted by :math:`P_j`, are
finished. Here, :math:`j = 1` indexes an artificial job with zero
duration, which precedes all jobs that can start at time zero, and
:math:`j = J` indexes an artificial final job, again with zero
duration, which represents the end of the project. Executing job
:math:`j` takes :math:`d_j` time periods and is supported by a set,
:math:`R`, of renewable resources.

Consider a horizon with an upper bound :math:`T` on the project’s
makespan, i.e., the time at which the final job is completed.
We assume :math:`K_r^p` units of renewable resource :math:`r \in R`
are available in each time period :math:`t = 1, 2, \dots, T`. Job
:math:`j` requires :math:`k_{jr}^p` units of the renewable resource
:math:`r \in R` for each period of the job’s duration, i.e., for time
periods when the job is in process.

The objective is to find a schedule that minimizes the project’s
makespan while respecting the constraints imposed by the precedence
relations and the limited resource availability.

Indexes and parameters
----------------------

- :math:`t = 1, 2, \dots, T`:
  time periods, where :math:`T` is an upper bound on the project’s
  makespan.
- :math:`j = 1, 2, \dots, J`:
  jobs, with :math:`j = 1` and :math:`j = J` denoting artificial jobs.
- :math:`r \in R`:
  set of renewable resources.
- :math:`d_j`:
  duration of job :math:`j`.
- :math:`K_r^p`:
  number of units of renewable resource :math:`r` available in period
  :math:`t`.
- :math:`k_{jr}^p`:
  number of units of renewable resource :math:`r` consumed by job
  :math:`j` while in process.
- :math:`P_j`:
  set of immediate predecessors of job :math:`j`.

Decision variables
------------------

- :math:`x_{jt}`:
  equals 1 if job :math:`j` completes in period :math:`t`; 0 otherwise.

Mathematical formulation
------------------------

.. math::

   \begin{aligned}
   \min \quad & \sum_{t=1}^{T} (t-1)\,x_{Jt} \\
   \text{s.t.} \quad
   & \sum_{t=1}^{T} x_{jt} = 1,
     && j = 1,\dots,J, \\
   & \sum_{t' \le t} x_{jt'} \;\le\;
     \sum_{t' \le t - d_j} x_{it'},
     && j = 2,\dots,J;\; t = 1,\dots,T;\; i \in P_j, \\
   & \sum_{j=1}^{J} \sum_{t' = t}^{t + d_j - 1} k_{jr}^p\,x_{jt'}
     \;\le\; K_r^p,
     && r \in R;\; t = 1,\dots,T, \\
   & x_{jt} \in \{0,1\},
     && j = 1,\dots,J;\; t = 1,\dots,T.
   \end{aligned}

The RCPSP model in LOGOS consists of the following modeling components:
:xmlNode:`Sets`, :xmlNode:`Parameters`, and :xmlNode:`Settings`. Each of
these components is illustrated in the following sections.

.. _subsec-rcpsp_sets:

Sets
----

This subsection contains information regarding the XML nodes used to
define the :xmlNode:`Sets` of the RCPSP model being performed through
LOGOS. :xmlNode:`Sets` specifies a collection of data, possibly
including numeric data (e.g., real or integer values) as well as
symbolic data (e.g., strings) typically used to specify the valid
indices for indexed components.

.. note::

   Numeric data provided in :xmlNode:`Sets` is treated as strings.

:xmlNode:`Sets` accepts the following sub-nodes:

- :xmlNode:`tasks`, comma/space-separated string, **required**
  Specifies the valid indices for tasks.

- :xmlNode:`resources`, comma/space-separated string, **required**
  Specifies the indices for renewable resources.

- :xmlNode:`predecessors`, comma/space-separated string, **required**
  Specifies the indices for preceding tasks.

- :xmlNode:`successors`, comma/space-separated string, **required**
  Specifies indices for successors. This sub-node accepts the following
  attribute:

  - :xmlAttr:`index`, string, **required**
    Specifies the index dependence. The valid index is
    :xmlString:`predecessors`.

Example XML:

.. code-block:: xml

   <Sets>
     <tasks>
       1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
     </tasks>

     <resources>
       r1 r2 r3 r4
     </resources>

     <predecessors>
       1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
     </predecessors>

     <successors index="predecessors">
       s1 s2 s3 s4;
       s1;
       s1;
       s1;
       s1;
       s1;
       s1;
       s1;
       s1;
       s1;
       s1
     </successors>
   </Sets>

.. _subsec-rcpsp_params:

Parameters
----------

This subsection contains information regarding the XML nodes used to
define the :xmlNode:`Parameters` of the RCPSP optimization model being
performed through LOGOS:

- :xmlNode:`available_resources`, comma/space-separated string, **required**
  Specifies the available renewable resources. This node accepts the
  following attribute:

  - :xmlAttr:`index`, string, **required**
    Specifies the indices of this parameter; keywords should be
    predefined in :xmlNode:`Sets`. Valid keywords are
    :xmlString:`resources`.

- :xmlNode:`task_resource_consumption`, comma/space-separated string, **required**
  Specifies the resource consumption for each task. This node accepts
  the following attribute:

  - :xmlAttr:`index`, comma-separated string, **required**
    Specifies the indices of this parameter; keywords should be
    predefined in :xmlNode:`Sets`. Valid keywords are
    :xmlString:`tasks, resources`.

- :xmlNode:`task_duration`, comma/space-separated string, **required**
  Specifies the duration for each task. This node accepts the following
  attribute:

  - :xmlAttr:`index`, string, **required**
    Specifies the indices of this parameter; keywords should be
    predefined in :xmlNode:`Sets`. Valid keywords are
    :xmlString:`tasks`.

- :xmlNode:`task_successors`, comma/space-separated string, **required**
  Specifies the successors for each predecessor. This node accepts the
  following attributes:

  - :xmlAttr:`index`, string, **required**
    Specifies the indices of this parameter; keywords should be
    predefined in :xmlNode:`Sets`. Valid keywords are
    :xmlString:`successors`.

  - :xmlAttr:`type`, string, *optional*
    Specifies the text type of the provided node, i.e., integer, float,
    or string. Valid values are :xmlString:`int`, :xmlString:`float`,
    or :xmlString:`str`.
    **Default**: :xmlString:`float`.

Example XML:

.. code-block:: xml

   <Parameters>
     <available_resources index="resources">
       13 13 13 12
     </available_resources>

     <task_resource_consumption index="tasks, resources">
       0  0  0  0
       10 0  0  0
       0  7  0  0
       0  9  0  0
       0  4  0  0
       0  0  0  6
       10 0  0  0
       0  0  6  0
       0  0  0  8
       0  6  0  0
       0  0  0  5
       0  0  0  0
     </task_resource_consumption>

     <task_duration index="tasks">
       0
       8
       1
       10
       6
       5
       8
       9
       1
       9
       8
       0
     </task_duration>

     <task_successors index="successors" type="str">
       2 3 4 9
       5
       7
       8
       6
       10
       11
       10
       12
       9
       12
     </task_successors>
   </Parameters>

.. _subsec-rcpsp_settings:

Settings
--------

This subsection contains information regarding the XML nodes used to
define the :xmlNode:`Settings` of the RCPSP optimization model being
performed through LOGOS:

- :xmlNode:`problem_type`, string, **required**
  Specifies the type of optimization problem. Currently, the only
  available type is :xmlString:`rcpsp`.

- :xmlNode:`solver`, string, *optional*
  Represents available solvers including:

  * :xmlString:`cbc` from ``https://github.com/coin-or/Cbc.git``
  * :xmlString:`glpk` from ``https://www.gnu.org/software/glpk/``

- :xmlNode:`sense`, string, *optional*
  Specifies :xmlString:`minimize` or :xmlString:`maximize` for
  minimization or maximization, respectively.
  **Default**: :xmlString:`minimize`.

- :xmlNode:`makespan_upperbound`, integer, **required**
  Specifies an upper bound on the makespan.

Example LOGOS input XML for RCPSP model:

.. code-block:: xml

   <?xml version="1.0" encoding="UTF-8"?>
   <Logos>
     ...
     <Settings>
       <makespan_upperbound>65</makespan_upperbound>
       <solver>cbc</solver>
       <sense>minimize</sense>
       <problem_type>rcpsp</problem_type>
     </Settings>
     ...
   </Logos>


.. _sec-pert-class:

The ``Pert`` Class
==================

While the formulation above is solved exactly as a mixed-integer program,
LOGOS also provides an event-driven scheduling engine — the ``Pert`` class —
for large, richly constrained schedules (such as full nuclear-outage models)
where an exact MILP is impractical. The ``Pert`` class is the core scheduling
engine of the CPM module. It represents a project schedule as a directed
acyclic graph of ``Activity`` objects bounded by a ``START`` and an ``END``
node, computes the classical CPM/PERT timing quantities (early start, early
finish, late start, late finish, and slack) and the critical path, and — when
resource, equipment, and location pools are provided — solves the
resource-constrained project scheduling problem heuristically using an
event-driven scheduling engine. It also supports Critical Chain Project
Management (CCPM) buffering, mid-outage replanning, and several
schedule-generation strategies.

Construction
------------

A ``Pert`` object can be built in two ways.

**1. From an** ``OutageData`` **object (recommended).**
When an ``OutageData`` instance is supplied, the activity graph and all
resource, equipment, location, consumable, and system-state pools are built
automatically from the planning data (typically loaded from a JSON file). This
is the path used for full nuclear-outage models.

**2. From a manual** ``graph`` **dictionary.**
For simple CPM-only problems, the graph can be provided directly as a
dictionary mapping each ``Activity`` to the list of its outgoing activities.

The constructor accepts the following parameters:

- **graph** (dictionary, optional): mapping of the form
  ``{Activity: [Activity, ...]}`` giving the successor activities of each
  activity. Provide this for manual construction.
- **outage_data** (``OutageData``, optional): object containing all planning
  data. When supplied, the graph is built automatically from its tasks.
- **priorities** (dictionary, optional): mapping of activity names to external
  priority values used to order candidates during resource-constrained
  scheduling.
- **seed** (integer, optional): random seed for reproducibility
  (**default**: ``2506178``).

Either ``graph`` or ``outage_data`` must be provided. A convenience class
method, ``Pert.from_json_file(filepath, schema_path)``, loads and validates an
``OutageData`` object from a JSON file and returns the corresponding ``Pert``
instance.

Example — manual graph construction:

.. code-block:: python

   from LOGOS.src.CPM.pert import Pert
   from LOGOS.src.CPM.activity import Activity

   start = Activity("start", 10)
   b     = Activity("b",     20)
   c     = Activity("c",      5)
   end   = Activity("end",   20)

   graph = {start: [b],
            b    : [c],
            c    : [end],
            end  : []}

   schedule = Pert(graph=graph)
   print(schedule.getProjectDuration())
   print(schedule.getCriticalPathSymbolic())

Example — construction from a JSON outage definition:

.. code-block:: python

   from LOGOS.src.CPM.pert import Pert

   schedule = Pert.from_json_file("outage.json", "outage_schema.json")
   result = schedule.calculateScheduleWithResources(sgs='max_use_res_ranked')

Modeled constraints
-------------------

When constructed from an ``OutageData`` object, the ``Pert`` scheduler honors
the following constraints (each is optional and activated by the corresponding
fields in the JSON task definitions):

- **Precedence** with optional finish-to-start **lags** between activities.
- **Renewable crews** organized by skill type, with **skill substitution**
  (a shortfall in one skill can be covered by qualified alternative skills).
- **Equipment** requirements, including **zone-locked** equipment that may
  only be used within a specific location.
- **Locations / zones**, including multi-zone activities that must occupy
  several zones simultaneously.
- **Consumables** that are permanently depleted when an activity starts, with
  optional mid-outage restock deliveries.
- **Plant system states** (isolation locks): activities requiring conflicting
  states of the same system are serialized.
- **Radiation dose budgets** tracked per worker-hour against a global limit.
- **Regulatory time windows** (earliest start / latest finish) per activity.
- **Shift calendars** restricting when work may be performed.
- **Mobilization lead times** for activities that need advance preparation.
- **Hold points** whose downstream (blocked) tasks cannot start until the
  hold point completes.
- **Multi-mode execution** (MMRCPSP): alternative duration/resource profiles
  per activity, selectable through ``set_modes``.
- **WBS priority roll-up**, elevating every member of a work-breakdown package
  when any member becomes critical.

CPM timing analysis
-------------------

These methods compute and expose the unconstrained CPM/PERT quantities:

- **generateInfo()**: compute early start, early finish, late start, late
  finish, and slack for every activity.
- **getProjectDuration()**: return the unconstrained project (critical-path)
  duration in hours.
- **getCriticalPath()** / **getCriticalPathSymbolic()**: return the critical
  path(s) as ``Activity`` objects, or as activity-name strings.
- **getCriticalPathWithLength()**: return the critical path as a dictionary
  including per-activity durations.
- **returnScheduleEndTime()**: return the absolute end time of the schedule.

Resource-constrained scheduling
-------------------------------

Once the resource, equipment, and location pools are populated, the schedule
can be solved as an RCPSP:

.. code-block:: python

   result = schedule.calculateScheduleWithResources(sgs='max_use_res_ranked')

The ``calculateScheduleWithResources`` method advances time only to the next
meaningful event (activity completion, availability-period boundary, or the
early-start time of a waiting activity) rather than stepping hour by hour. It
accepts the following parameters:

- **sgs** (string): the Schedule Generation Scheme used to select activities
  from the available candidates at each event (**default**:
  ``max_use_res_ranked``).
- **max_time_hours** (float, optional): safety cutoff, in hours from the start
  time. Defaults to a multiple of the CPM duration.
- **priority_rule** (string, optional): priority rule used to order candidates
  when no external priorities are supplied.

Allowed values for the ``sgs`` parameter are:

- **first**: serial SGS — try candidates in priority order and start the
  single highest-priority activity that is resource-feasible.
- **max_use_res_ranked**: rank candidates by float/value and start as many of
  the highest-ranked activities as the available resources allow.
- **max_use_res_shuffled**: as above but with a randomly shuffled candidate
  order.
- **md_knapsack**: select the set of activities through a multi-dimensional
  knapsack optimization over the currently available resources.
- **look_ahead**: rank candidates by their immediate value plus a discounted
  estimate of the future opportunities they unlock within a finite horizon.

The method returns a dictionary with the keys ``scheduled_duration`` (hours
from start to the last activity end), ``cpm_duration`` (unconstrained CPM
duration), ``delay_hours`` (total accumulated resource-wait delay),
``n_activities``, ``n_completed``, and ``iterations`` (number of event-loop
steps). A companion method, ``calculateSerialScheduleWithResources``, runs a
strictly serial schedule-generation scheme.

Scenario updates, multi-mode, and replanning
--------------------------------------------

- **set_durations(new_durations)**: update activity durations (e.g. from a
  RAVEN sample) and recompute all CPM values.
- **set_modes(mode_assignments)**: apply named execution modes to activities
  and recompute all CPM values.
- **set_priorities(priorities)**: update the external priority map used to
  order candidates.
- **replan(...)**: replan the remaining schedule from a mid-outage snapshot,
  freezing completed and in-progress work and rescheduling the rest.
- **clone_for_analysis()**: return an independent copy of the ``Pert`` object
  suitable for what-if analysis without disturbing the original.

Critical Chain (CCPM) buffering
-------------------------------

- **insert_project_buffer(...)**: insert a CCPM project buffer at the end of
  the resource-constrained chain.
- **insert_feeding_buffers(...)**: insert feeding buffers where non-critical
  paths merge into the constrained chain.
- **get_buffer_status()**: report the consumption status of all buffer
  activities.

Validation, reporting, and visualization
----------------------------------------

- **validate_schedule()**: run post-schedule feasibility checks and return a
  ``ValidationResult`` (precedence, resources, equipment, zones, consumables,
  dose, system states, time windows, shift calendar, and hold points).
- **check_dependency_violations()**: check whether the computed schedule
  violates any job-precedence constraint.
- **explain_idle_on_chain()** / **explain_idle_on_chain_detailed()**: for each
  activity on the constrained chain, explain why it waited.
- **get_schedule_dataframe()**: return the schedule as a pandas ``DataFrame``.
- **export_schedule_to_csv(...)** / **print_summary()**: export or print the
  computed schedule.
- **plot_activity_dag(...)**: render the activity network as a DAG with rich
  tooltips. The module-level function ``plot_gantt_chart(pert, ...)`` renders
  an interactive Gantt chart, coloring activities by criticality, to an HTML
  file.

.. note::

   The ``Pert`` class works both standalone and inside a RAVEN workflow
   (through the ``BaseCPMmodel`` external model). See :ref:`sec-CPM` for the
   RAVEN input format; the exact mathematical formulation of the
   resource-constrained scheduling problem is given at the top of this page.
