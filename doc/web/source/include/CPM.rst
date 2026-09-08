.. _sec-CPM:

Critical Path Model (CPM)
=========================

The CPM model performs schedule duration calculations for a set of activities
linked by a graph structure. It is designed to be driven from a RAVEN workflow,
where activity duration or priority values are varied through a sampling or
optimization strategy and the resulting (resource-constrained) critical-path
duration is returned to RAVEN.

The scheduling engine itself — the ``Pert`` class and its ``Activity`` objects,
together with the resource-constrained schedule-generation schemes — is
documented in :ref:`sec-pert-class`. This section describes how to drive that
engine from a RAVEN input file through the ``BaseCPMmodel`` external model.

Model definition
----------------

The CPM model is declared as a RAVEN :xmlNode:`ExternalModel` with
``subType="LOGOS.BaseCPMmodel"``. The schedule (activities, precedence, and
resource pools) is loaded from an external JSON file; RAVEN then samples
selected activity durations and/or priorities, and the model returns the
resource-constrained project duration.

Example of a CPM external model in a RAVEN input file:

.. code-block:: xml

   <Models>
     <ExternalModel name="CPMmodel" subType="LOGOS.BaseCPMmodel">
       <inputs>R_C101, R_C102, R_C103</inputs>
       <outputs>end_time</outputs>
       <project_file>example_10.json</project_file>
       <schema>outage_schema.json</schema>
       <CPtime>end_time</CPtime>
       <sgs>max_use_res_ranked</sgs>
       <map act='C101' attr='duration'>R_C101</map>
       <map act='C102' attr='duration'>R_C102</map>
       <map act='C103' attr='priority'>R_C103</map>
     </ExternalModel>
   </Models>

The following nodes are recognized:

- :xmlNode:`variables`, **required**
  The RAVEN variables handled by the model: the sampled input variables mapped
  below, plus the output variable named in :xmlNode:`CPtime`.

- :xmlNode:`project_file`, **required**
  Path to the JSON file defining the schedule (activities, precedence, and
  resource / equipment / location pools). It is loaded and validated through
  ``Pert.from_json_file``.

- :xmlNode:`schema`, **required**
  Path to the JSON schema used to validate :xmlNode:`project_file`.

- :xmlNode:`CPtime`, **required**
  Name of the RAVEN variable that receives the computed project
  (critical-path) duration.

- :xmlNode:`sgs`, *optional*
  The Schedule Generation Scheme passed to the resource-constrained solver.
  Allowed values are :xmlString:`first`, :xmlString:`max_use_res_ranked`,
  :xmlString:`max_use_res_shuffled`, :xmlString:`md_knapsack`, and
  :xmlString:`look_ahead` (see :ref:`sec-pert-class` for their meaning).

- :xmlNode:`map`, **required** (one per sampled variable)
  Binds a RAVEN variable to an attribute of a specific activity. Each
  :xmlNode:`map` node provides:

  - :xmlAttr:`act`, **required**
    The ID of the activity to update.

  - :xmlAttr:`attr`, **required**
    The activity attribute to set from the RAVEN value. Valid values are
    :xmlString:`duration` or :xmlString:`priority`.

  - node text, **required**
    The name of the RAVEN variable supplying the value.

Analysis type: durations vs. priorities
----------------------------------------

The type of analysis is expressed through the :xmlAttr:`attr` attribute of the
:xmlNode:`map` nodes rather than a dedicated node:

- **duration sampling**: use ``attr='duration'`` maps so that RAVEN-sampled
  values overwrite the corresponding activity durations before the schedule is
  recomputed.
- **priority sampling**: use ``attr='priority'`` maps so that RAVEN-sampled
  values set the external priorities used to order candidate activities during
  resource-constrained scheduling under the chosen :xmlNode:`sgs`.

Both kinds of maps may be combined in a single model.

Model output
------------

At each RAVEN sample the model applies the mapped durations and priorities,
computes the resource-constrained schedule with the selected :xmlNode:`sgs`,
and stores the resulting project (critical-path) duration into the RAVEN
variable named in :xmlNode:`CPtime`.
