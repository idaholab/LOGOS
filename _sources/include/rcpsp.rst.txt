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
