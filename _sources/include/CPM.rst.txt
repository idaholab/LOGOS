.. _sec-CPM:

Critical Path Model (CPM)
=========================

The CPM model is designed to perform schedule duration calculations given a set of
activities linked by a graph structure. This model is designed to be used in a
RAVEN workflow where the activity duration values can be changed through a specific
strategy (either sampling or optimization).

The schedule requires a ``start`` and an ``end`` activity, and a list of additional
activities with their corresponding duration values.

Every activity is defined in an "Activity" class instance where these parameters are provided:

- **name** (string): ID of the activity (required)
- **duration** (float): planned activity duration (required)
- **res** (list of dictionaries): resources required to complete the activity (optional). Each 
required resource is specified by a dictionary in the form of ``{resource_ID: amount_required}`` 
where ``resource_ID`` indicates the name of the required resource while "amount_required" indicates 
the required amount.

There are two ways to specify the graph.

Graph Definition via XML ``map`` Nodes
--------------------------------------

In the first way, the graph is defined using :xmlNode:`map` nodes. In each instance
of the :xmlNode:`map` node, an activity is defined and the following information is
required:

- activity ID (``act`` attribute)
- activity duration (``dur`` attribute)
- list of outgoing activities (node text)

Example of CPM input XML in a RAVEN input file:

.. code-block:: xml

   <Models>
     <ExternalModel name="CPMmodel" subType="LOGOS.BaseCPMmodel">
       <variables>start,b,c,d,end,f,g,h,end_time,CP</variables>
       <CPtime>end_time</CPtime>
       <CPid>CP</CPid>
       <map act='start' dur='10'>f,b,h</map>
       <map act='b'     dur='20'>c</map>
       <map act='c'     dur='5' >g,d</map>
       <map act='d'     dur='10'>end</map>
       <map act='f'     dur='15'>g</map>
       <map act='g'     dur='5' >end</map>
       <map act='h'     dur='15'>end</map>
       <map act='end'   dur='20'></map>
     </ExternalModel>
   </Models>

Graph Definition via Python ``project`` Class
---------------------------------------------

In the second way, the graph structure is defined in a ``.py`` file, in a
``project()`` class. In this class, each activity is defined by its ID and duration
(through the ``Activity`` object). Then the graph structure is defined through a
dictionary: for each activity, a list of outgoing activities is provided.
As an example, the completion of activity ``c`` can enable the start of 
activities ``g`` and ``d``.

Example of schedule definition in an external ``.py`` file:

.. code-block:: python

   from LOGOS.src.CPM.PertMain2 import Pert
   from LOGOS.src.CPM.PertMain2 import Activity

   class project():
     start = Activity("start", 10)
     b     = Activity("b",     20)
     c     = Activity("c",      5)
     d     = Activity("d",     10)
     f     = Activity("f",     15)
     g     = Activity("g",      5)
     h     = Activity("h",     15)
     end   = Activity("end",   20)

     graph = {start: [f, b, h],
              b    : [c],
              c    : [g, d],
              d    : [end],
              f    : [g],
              g    : [end],
              h    : [end],
              end  : []}

The project schedule can now be instantiated through the ``Pert`` class:

.. code-block:: python
  outage = Pert(graph=dependencies, startTime, resourcesTS)

where these parameteres are provided:
- **graph** (dictionary): set of dependencies (required)
- **startTime** (datetime): initial time of schedule (optional)
- **resourcesTS** (pd.dataframe): resources availability (optional)
- **priorities**: priority values assigne to each acitivty (optional)

The resources availability time series is provided in form of a pandas dataframe indexed by the 
datetime array that needs to match defined startTime. The time series is required to be sample on an hourly basis.
An example is provided below where resourcesTS and startTime are constructed.
In this case, resourcesTS provides the availability temporal profile of one available crew (with skill res1)
and two available crews (with skill res2).

.. code-block:: python
  N = 30 
  outageStartTime = datetime(2025, 10, 20, 8)
  hourly_index = pd.date_range(start=outageStartTime, periods=N, freq='h')
  resourcesTS = pd.DataFrame({'res1': 1*np.ones(N), 'res2': 2*np.ones(N)}, index=hourly_index)

These are methods associated with the ``Pert`` class:

- **getCriticalPathSymbolic()**: return critical path as an ordered list of activity indicates
- **eturnScheduleEndTime()**: return end time of project schedule

Resource-Constrained Project Scheduling Problem (RCPSP)
--------------------------------------

If the resources are specified in the schedule problem, then it is possible to use the ``Pert``
class to solve the RCPSP optimization problem as follows

.. code-block:: python
  outage.calculateScheduleWithResources(sgs='MD-Knapsack')


where the parameter ``sgs`` indicates the strategy to select activities out of available candidates 
at each time step (indicated as schedule generation scheme - SGS).
Allowed values for the ``sgs``''`` parameters are:

- **max_use_res_act**: select the first N activities in candidates only if present and future 
resources are available
- **max_use_res_ranked**: rank activities based on float values and select the first N activities 
in candidates only if present and future resources are available
- **max_use_res_shuffled**: randomly shuffle the initial list of activities and select the 
first N activities in candidates only if present and future resources are available
- **MD-Knapsack**: select N activities through the multi-dimensional knapsack optimization 
model in candidates only if present resources are available. This assumes that once a resource 
has been tasked to an activty, that resource is assigned until the activity has been completed. 
This might lead to negative resource availability.

CPM Model and RAVEN
--------------------------------------

In RAVEN the graph can be imported in two ways. In the first way, the graph is defined in the
\xmlNode{map} nodes. In each instance of the \xmlNode{map} node, an activity is defined and the
following information is required: activity ID (act attribute), activity duration (dur attribute),
and the list of outgoing activities.

Example of CPM input XML in RAVEN input file:
\begin{lstlisting}[style=XML]
  <Models>
    <ExternalModel name="CPMmodel" subType="LOGOS.BaseCPMm odel">
      <variables>start,b,c,d,end,f,g,h,end_time,CP</variables>
      <analysis>activity_duration</analysis> 
      <CPtime>end_time</CPtime>
      <CPid>CP</CPid>
      <map act='start' dur='10'>f,b,h</map>
      <map act='b'     dur='20'>c</map>
      <map act='c'     dur='5' >g,d</map>
      <map act='d'     dur='10'>end</map>
      <map act='f'     dur='15'>g</map>
      <map act='g'     dur='5' >end</map>
      <map act='h'     dur='15'>end</map>
      <map act='end'   dur='20'></map>
    </ExternalModel>
  </Models>
\end{lstlisting}

In the second way, the graph structure can be define in a .py file and it is specified in a
project() class (see below). In this class each activity is define in terms of ID and duration
(see Activity object below).
Then the graph structure is defined through a dictionary, for each activity, a list of list of
outgoing activities is provided.

Example of schedule definition in an external .py file:
\begin{lstlisting}[language=Python]
  from LOGOS.src.CPM.PertMain2 import Pert
  from LOGOS.src.CPM.PertMain2 import Activity

  class project():
    start = Activity("start", 10)
    b     = Activity("b",     20)
    c     = Activity("c",      5)
    d     = Activity("d",     10)
    f     = Activity("f",     15)
    g     = Activity("g",      5)
    h     = Activity("h",     15)
    end   = Activity("end",   20)

    graph = {start: [f,b,h],
             b    : [c],
             c    : [g,d],
             d    : [end],
             f    : [g],
             g    : [end],
             h    : [end],
             end  : []}
  \end{lstlisting}

The \xmlNode{analysis} node indicates which type of analysis is performed with RAVEN:
\begin{itemize}
  \item activity_duration: RAVEN sample acitivty duration values
  \item activity_priority: RAVEN sample acitivty priority values 
\end{itemize}

If activity_priority is chosen, then it is required to provide an additional information: the 
chosen schedule generation scheme (SGS) as shown in the example below:

\begin{lstlisting}[style=XML]
    <ExternalModel name="CPMmodel" subType="LOGOS.BaseCPMmodel">
      <variables>start,b,c,d,end,f,g,h,end_time,CP</variables>
      <analysis>activity_duration</analysis> <!--activity_duration or activity_priority-->
      <sgs>max_use_res_ranked</sgs>
      <CPtime>end_time</CPtime>
      <CPid>CP</CPid>
    </ExternalModel>
  </Models>
\end{lstlisting}

The \xmlNode{sgs} node indicates which SGS has been chosen (see above):
\begin{itemize}
  \item max_use_res_act
  \item max_use_res_ranked
  \item max_use_res_shuffled
  \item MD-Knapsack
\end{itemize}

The CPM model return two parameters. The first one is the critical path time value (i.e., a float),
while the second one is the actual critical path which is represented as a sequence of activity ID
that are part of the critial path (i.e., a string) separated by an underscore.
The RAVEN ID for the critical path time value is specified in the \xmlNode{CPtime}.
The RAVEN ID for the critical path is specified in the \xmlNode{CPid}.

CPM Model and RAVEN
-------------
In RAVEN, the graph can be imported in two ways:

**1. XML definition using \xmlNode{map} nodes:**

.. code-block:: xml

  <Models>
    <ExternalModel name="CPMmodel" subType="LOGOS.BaseCPMmodel">
      <variables>start,b,c,d,end,f,g,h,end_time,CP</variables>
      <analysis>activity_duration</analysis> 
      <CPtime>end_time</CPtime>
      <CPid>CP</CPid>
      <map act='start' dur='10'>f,b,h</map>
      <map act='b'     dur='20'>c</map>
      <map act='c'     dur='5' >g,d</map>
      <map act='d'     dur='10'>end</map>
      <map act='f'     dur='15'>g</map>
      <map act='g'     dur='5' >end</map>
      <map act='h'     dur='15'>end</map>
      <map act='end'   dur='20'></map>
    </ExternalModel>
  </Models>

**2. Python file using ``project()`` class:**

.. code-block:: python

  from LOGOS.src.CPM.PertMain2 import Pert
  from LOGOS.src.CPM.PertMain2 import Activity

  class project():
    start = Activity("start", 10)
    b     = Activity("b",     20)
    c     = Activity("c",      5)
    d     = Activity("d",     10)
    f     = Activity("f",     15)
    g     = Activity("g",      5)
    h     = Activity("h",     15)
    end   = Activity("end",   20)

    graph = {start: [f,b,h],
             b    : [c],
             c    : [g,d],
             d    : [end],
             f    : [g],
             g    : [end],
             h    : [end],
             end  : []}

The analysis xml node specifies the type of analysis:

- **activity_duration**: RAVEN samples activity durations
- **activity_priority**: RAVEN samples activity priorities

If ``activity_priority`` is chosen, the schedule generation scheme must be specified:

.. code-block:: xml

  <ExternalModel name="CPMmodel" subType="LOGOS.BaseCPMmodel">
    <variables>start,b,c,d,end,f,g,h,end_time,CP</variables>
    <analysis>activity_duration</analysis>
    <sgs>max_use_res_ranked</sgs>
    <CPtime>end_time</CPtime>
    <CPid>CP</CPid>
  </ExternalModel>


Allowed SGS values:

- **max_use_res_act**
- **max_use_res_ranked**
- **max_use_res_shuffled**
- **MD-Knapsack**

The CPM model returns:

- **Critical path time** (float): The critical path time value (a float)
- **Critical path** (string of activity IDs separated by underscores): The actual 
critical path, represented as a sequence of activity IDs that are part of the critical 
path, separated by underscores (a string)

The RAVEN ID for critical path time is specified in ``<CPtime>``.
The RAVEN ID for critical path is specified in ``<CPid>``.
