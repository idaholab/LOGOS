.. _sec-pert-class:

The ``Pert`` Scheduling Class
-----------------------------

The ``Pert`` class is the core scheduling engine of the CPM module. It
represents a project as a directed acyclic graph of ``Activity`` objects,
bounded by a ``START`` node and an ``END`` node. The class provides two
levels of analysis:

- **CPM/PERT analysis** of the unconstrained network: early and late start
  and finish times, float, and the critical path.
- **Resource-constrained scheduling.** When resource, equipment, and location
  pools are supplied, it solves the RCPSP (:ref:`sec-sm-rcpsp`) heuristically
  with an event-driven schedule generation scheme.

It also supports Critical Chain Project Management (CCPM) buffering,
mid-outage replanning, and multi-mode execution. The exact mixed-integer
formulation in :ref:`sec-RCPSP` suits small, plain RCPSP instances. The
``Pert`` engine is designed for large, richly constrained schedules, such as
full nuclear-outage models, for which an exact model is impractical.

CPM/PERT network analysis
~~~~~~~~~~~~~~~~~~~~~~~~~

The Critical Path Method :cite:`KelleyWalker1959` and the Program Evaluation
and Review Technique :cite:`Malcolm1959` find the earliest project completion
time when resource limits are ignored. Activities are first sorted
topologically. A *forward pass* then computes the early start and early finish
times:

.. math::

   ES_j = \max_{i \in P_j} \big(EF_i + \ell_{ij}\big), \qquad
   EF_j = ES_j + d_j,

with :math:`ES_{\text{START}} = 0`. Here :math:`\ell_{ij} \ge 0` is an
optional finish-to-start lag. The project duration is
:math:`T_{cpm} = EF_{\text{END}}`. A *backward pass* starts from
:math:`LF_{\text{END}} = T_{cpm}` and computes the late finish and late start
times:

.. math::

   LF_j = \min_{i \in S_j} \big(LS_i - \ell_{ji}\big), \qquad
   LS_j = LF_j - d_j .

The total float :math:`TF_j = LS_j - ES_j` is the time by which activity
:math:`j` can be delayed without delaying the project. Activities with zero
float form the *critical path*. In LOGOS, these values also account for
mobilization lead times and for regulatory time windows, which tighten the
earliest and latest dates. The CPM quantities are the input of many priority
rules (:ref:`sec-priority-rules`). They also provide a lower bound,
:math:`T_{cpm}`, on any resource-feasible makespan.

Construction
~~~~~~~~~~~~

A ``Pert`` object can be built in two ways.

**1. From an** ``OutageData`` **object (recommended).**
When an ``OutageData`` instance is supplied, the activity graph and all
resource, equipment, location, consumable, and system-state pools are built
automatically from the planning data. The data is typically loaded from a JSON
file that follows ``outage_schema.json``. This is the path used for full
nuclear-outage models.

**2. From a manual** ``graph`` **dictionary.**
For simple CPM-only problems, the graph can be provided directly as a
dictionary that maps each ``Activity`` to the list of its successor
activities.

The constructor accepts the following parameters:

- **graph** (dictionary, optional): mapping of the form
  ``{Activity: [Activity, ...]}`` that gives the successors of each activity.
  Provide this for manual construction.
- **outage_data** (``OutageData``, optional): object that contains all
  planning data. When it is supplied, the graph is built automatically from
  its tasks.
- **priorities** (dictionary, optional): mapping from activity names to
  external priority values. These values order the candidates during
  resource-constrained scheduling.
- **seed** (integer, optional): random seed for reproducibility
  (**default**: ``2506178``).

Either ``graph`` or ``outage_data`` must be provided. The class method
``Pert.from_json_file(filepath, schema_path, priorities=None, seed=2506178)``
validates a JSON outage definition against the schema, builds the
``OutageData`` object, and returns the corresponding ``Pert`` instance.

Example: manual graph construction

.. code-block:: python

   from LOGOS.src.CPM import Pert, Activity

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

Example: construction from a JSON outage definition

.. code-block:: python

   from LOGOS.src.CPM import Pert

   schedule = Pert.from_json_file("example_10.json",
                                  schema_path="outage_schema.json")
   schedule.generateInfo()
   result = schedule.calculateScheduleWithResources(priority_rule="lf")
   print(result["scheduled_duration"], result["cpm_duration"])

Modeled constraints
~~~~~~~~~~~~~~~~~~~

When it is constructed from an ``OutageData`` object, the ``Pert`` scheduler
honors the following constraints. Each one is optional and is activated by the
corresponding fields in the JSON task definitions.

- **Precedence** with optional finish-to-start **lags** between activities.
- **Renewable crews** organized by skill type, with **skill substitution**:
  qualified alternative skills can cover a shortfall in one skill.
- **Equipment** requirements, including **zone-locked** equipment that can
  only be used within a specific location.
- **Locations and zones**, including multi-zone activities that must occupy
  several zones at the same time.
- **Consumables**, which are permanently depleted when an activity starts,
  with optional mid-outage restock deliveries.
- **Plant system states** (isolation locks): activities that require
  conflicting states of the same system are serialized.
- **Radiation dose budgets**, tracked per worker-hour against a global limit.
- **Regulatory time windows** (earliest start and latest finish) per activity.
- **Shift calendars**, which restrict when work can be performed.
- **Mobilization lead times** for activities that need advance preparation.
- **Hold points**: the tasks they block cannot start until the hold point
  completes.
- **Multi-mode execution** (MRCPSP): alternative duration and resource
  profiles per activity, selected through ``set_modes``.
- **WBS priority roll-up**: when any member of a work-breakdown package
  becomes critical, every member of the package gets elevated priority.

CPM timing methods
~~~~~~~~~~~~~~~~~~

The following methods compute and return the unconstrained CPM/PERT
quantities:

- **generateInfo()**: run the forward and backward passes. This computes the
  early start, early finish, late start, late finish, and float of every
  activity, together with the static metrics used by the priority rules.
- **getProjectDuration()**: return the unconstrained (critical-path) project
  duration :math:`T_{cpm}` in hours.
- **getCriticalPath()** / **getCriticalPathSymbolic()**: return the critical
  path(s) as ``Activity`` objects or as activity-name strings.
- **getCriticalPathWithLength()**: return the critical path as a dictionary
  that includes the duration of each activity.
- **returnScheduleEndTime()**: return the absolute end time of the schedule.

Resource-constrained scheduling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Once the resource, equipment, and location pools are populated, the schedule
can be solved as an RCPSP:

.. code-block:: python

   result = schedule.calculateScheduleWithResources(
       sgs="max_use_res_ranked", priority_rule="lf")

``calculateScheduleWithResources`` implements a parallel, event-driven
schedule generation scheme. Time advances only to the next meaningful event,
such as an activity completion, an availability-period boundary, or the early
start of a waiting activity, rather than hour by hour. At each event, the
eligible activities are ranked by a priority value and started while the
resources allow. The method accepts the following parameters:

- **sgs** (string): the candidate-selection strategy used at each event
  (**default**: ``max_use_res_ranked``).
- **max_time_hours** (float, optional): safety cutoff, in hours from the start
  time. By default it is a multiple of the CPM duration. If no further event
  can occur before the cutoff, the run is reported as deadlocked.
- **priority_rule** (string, optional): the priority rule that ranks the
  candidates, for example ``lf``, ``lrpw``, ``wcs``, or
  ``hybrid:lrpw+scrr``. See :ref:`sec-priority-rules` for all rules.

The priority values are chosen as follows. If external priorities are set
(through the constructor, ``set_priorities``, or RAVEN), they are used.
Otherwise, if ``priority_rule`` is given, that rule is used. Otherwise the
float-based default ``TF_based`` is used.

Allowed values for the ``sgs`` parameter are:

- **first**: try the candidates in priority order and start the single
  highest-priority activity that is resource-feasible.
- **max_use_res_ranked**: rank the candidates by priority and start as many of
  the highest-ranked activities as the available resources allow.
- **max_use_res_shuffled**: as above, but with the candidates in a randomly
  shuffled order.
- **md_knapsack**: select the set of activities to start by solving a
  multi-dimensional knapsack problem over the currently available resources.
- **look_ahead**: rank the candidates by their immediate value plus a
  discounted estimate of the future opportunities they unlock within a finite
  horizon.

The method returns a dictionary with the following keys:

- ``scheduled_duration``: hours from the start to the end of the last
  activity;
- ``cpm_duration``: the unconstrained CPM duration;
- ``delay_hours``: the total accumulated resource-wait delay;
- ``n_activities`` and ``n_completed``;
- ``iterations``: the number of event-loop steps.

The companion method
``calculateSerialScheduleWithResources(priority_rule='lf', max_time_hours=None)``
implements the serial SGS. It ranks all activities once and schedules each one
at its earliest feasible time, in that order.

Scenario updates, multi-mode, and replanning
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **set_durations(new_durations)**: update the activity durations (for
  example, from a RAVEN sample) and recompute all CPM values.
- **set_modes(mode_assignments)**: apply named execution modes to activities
  and recompute all CPM values.
- **set_priorities(priorities)**: update the external priority map used to
  order the candidates.
- **replan(current_time_hours, ...)**: replan the rest of the schedule from a
  mid-outage snapshot. Completed and in-progress work is frozen and the
  remaining activities are rescheduled.
- **clone_for_analysis()**: return an independent copy of the ``Pert``
  object for what-if analysis that leaves the original unchanged.

Critical Chain (CCPM) buffering
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Critical Chain Project Management :cite:`Goldratt1997` protects the
resource-constrained chain with time buffers:

- **insert_project_buffer(...)**: insert a project buffer at the end of the
  resource-constrained chain.
- **insert_feeding_buffers(...)**: insert feeding buffers where non-critical
  paths merge into the constrained chain.
- **get_buffer_status()**: report how much of each buffer has been consumed.

Validation, reporting, and visualization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **validate_schedule()**: run post-schedule feasibility checks and return a
  ``ValidationResult``. The checks cover precedence, resources, equipment,
  zones, consumables, dose, system states, time windows, shift calendars, and
  hold points.
- **check_dependency_violations()**: check whether the computed schedule
  violates any precedence constraint.
- **explain_idle_on_chain()** / **explain_idle_on_chain_detailed()**: for each
  activity on the constrained chain, explain why it waited.
- **get_schedule_dataframe()**: return the schedule as a pandas
  ``DataFrame``.
- **export_schedule_to_csv(...)** / **print_summary()**: export or print the
  computed schedule.
- **plot_activity_dag(...)**: render the activity network as a DAG with
  detailed tooltips.

The module also provides the plotting functions ``plot_gantt_chart(pert,
...)``, ``plot_resource_utilization(pert, resource_type, ...)``,
``plot_location_utilization(pert, location_id, ...)``, and
``plot_equipment_utilization(pert, equipment_id, ...)``. They write
interactive Plotly HTML files. The Gantt chart colors activities by
criticality.

.. note::

   The ``Pert`` class works both standalone and inside a RAVEN workflow,
   through the ``BaseCPMmodel`` external model. See :ref:`sec-CPM` for the
   RAVEN input format and :ref:`sec-RCPSP` for the exact mathematical
   formulation of the RCPSP.
