Critical Path Model (CPM)
=========================
.. _sec-CPM:

The CPM model is designed to perform schedule duration calculations given a set of
activities linked by a graph structure. This model is designed to be used in a
RAVEN workflow where the activity duration values can be changed through a specific
strategy (either sampling or optimization).

The schedule requires a ``start`` and an ``end`` activity, and a list of additional
activities with their corresponding duration values.

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

Model Outputs
-------------

The CPM model returns two parameters:

1. The critical path time value (a float).
2. The actual critical path, represented as a sequence of activity IDs that are
   part of the critical path, separated by underscores (a string).

The RAVEN ID for the critical path time value is specified in :xmlNode:`CPtime`.
The RAVEN ID for the critical path is specified in :xmlNode:`CPid`.
