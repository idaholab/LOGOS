.. _sec-ModelingComponents:

Overview of Capital Budgeting Modeling Components
=================================================

We consider a capital budgeting problem for a nuclear generation station, with
possible extension to a larger fleet of plants. Due to limited resources, we
can only select a subset from a number of candidate investment projects. Our
goal is to maximize overall net present value (NPV), or a variant of this
objective when we incorporate uncertainty into project cost and revenue
streams. In doing so, we must respect resource limits and capture key
structural and stochastic dependencies of the system. Example projects include
upgrading a steam turbine, refurbishing or replacing a set of reactor coolant
pumps, and replacing a set of feed-water heaters. Selecting an individual
project has multiple facets and implications.

- **Rewards or Net Present Values**:
  Selecting a project can improve revenue (e.g., upgrading a steam turbine may
  lead to an uprate in plant capacity resulting in larger revenue from selling
  power). Replacing a key system component can improve reliability, increasing
  revenue due to a reduction in forced outages as well as operations and
  maintenance (O\&M) costs. Choosing to perform minimum maintenance versus
  refurbishing a component or replacing and improving a system can produce
  reward streams that can be negative or positive depending on the selection.
  The parameter :xmlNode:`net_present_values` is used to specify the rewards
  (see Section :ref:`subsec-Parameters`).

- **Resources and Liabilities**:
  Critical resources include:

  1. capital costs,
  2. O\&M costs,
  3. time and labor-hours during a planned outage, and
  4. personnel, installation and maintenance equipment, workspaces, etc.

  Within these categories, resources can be further sub-categorized (each
  subcategory having its own budget) according to the plant’s organizational
  structure to provide multiple “colors” of money within capital costs, O\&M
  costs, personnel availability, etc.

  The set :xmlNode:`resources` and the parameter
  :xmlNode:`available_capitals` are used to specify the resources and
  liabilities (see Sections :ref:`subsec-Sets` and :ref:`subsec-Parameters`).

- **Costs**:
  Selecting a project in year :math:`t` induces multiple cost streams in year
  :math:`t` as well as in subsequent years; we interpret “cost” broadly to
  include commitment of critical resources. The parameter :xmlNode:`costs` is
  used to specify the costs (see Section :ref:`subsec-Parameters`).

- **Time Periods**:
  Multiple capital projects can compete for the same time period, limiting
  project selection. The set :xmlNode:`time_periods` is used to provide
  indices for **costs** and **available capitals** (see
  Section :ref:`subsec-Sets`).

- **Options**:
  The goal of selecting a project is typically to improve or maintain a
  particular function the plant performs, and there may be multiple ways to
  carry out the task. A project may be performed over a three-year
  period—say, years “:math:`t, t+1, t+2`”—or the start of the project could
  instead be two years hence, changing the period to
  “:math:`t+2, t+3, t+4`”. Alternatively, at increased cost and benefit, it
  may be possible to complete the project in two years:
  “:math:`t, t+1`” or “:math:`t+2, t+3`”. When selecting a project to uprate
  plant capacity, we may have the options of increasing it by 3\% or 6\%.

  In all these cases, we can perform the project in at most one particular way
  out of a collection of options. We represent this by cloning a “project”
  into multiple project–option pairs, and adding a constraint saying that we
  can select at most one from this set of options. The set
  :xmlNode:`options` is used to provide indices for these multiple
  project–option pairs (see Section :ref:`subsec-Sets`).

- **Capitals**:
  If we consider maintenance for multiple units in an NPP in parallel, it must
  be decided whether to accept a particular replacement and, if so, in which
  unit to conduct the corresponding replacement. In this case, the set
  :xmlNode:`capitals` is used to provide indices for these units (see
  Section :ref:`subsec-Sets`).

- **Available Capitals**:
  These are the budgets available for resources/units. The parameter
  :xmlNode:`available_capitals` is used to specify the available capital for
  different resources/units at different :math:`t` (see
  Section :ref:`subsec-Parameters`).

- **Non-Selection**:
  Not selecting a project also has implications, such as a growth in O\&M
  costs in future years, a decrease in plant production, an increase in forced
  outages, and even risking a premature end to plant life. Thus, not
  selecting a project can be seen as one more “option” for how a larger
  project is executed, expanding the list discussed earlier. Selection of the
  “do nothing” option is reflected in both liability streams and reward
  streams. This can be activated by setting :xmlNode:`nonSelection` to
  :xmlString:`True` (see Section :ref:`subsec-Settings`).

- **Uncertainty**:
  One limitation of traditional optimization models for capital budgeting is
  that they do not account for uncertainty in reward and cost streams
  associated with individual projects, nor do they account for uncertainty in
  resource availability in future years. Projects can incur cost overruns,
  especially when projects are large, performed infrequently, or when there is
  uncertainty regarding technical viability, external contractors, and/or
  suppliers of requisite parts and materials. Occasionally, projects are
  performed ahead of schedule and with savings in cost. Planned budgets for
  capital improvements can be cut, and key personnel may be lost. Or, there
  may be surprise budgetary windfalls for maintenance activities due to
  decreased costs for “unplanned” maintenance. The XML node
  :xmlNode:`Uncertainties` is used to specify such uncertainties (see
  Section :ref:`subsec-Uncertainties`).

LOGOS consists of a collection of modeling entities/components that define
different aspects of the model, including :xmlNode:`Sets`,
:xmlNode:`Parameters`, :xmlNode:`Uncertainties`, and
:xmlNode:`ExternalConstraints`. In addition, the :xmlNode:`Settings` block
specifies how the overall computation should run.

.. _subsec-Sets:

Sets
----

This subsection describes the XML nodes used to define the
:xmlNode:`Sets` of the optimization model being performed through LOGOS.
:xmlNode:`Sets` specifies collections of data, possibly including numeric
data (e.g., real or integer values) as well as symbolic data (e.g.,
strings), typically used to specify the valid indices for indexed
components.

.. note::

   Numeric data provided in :xmlNode:`Sets` is treated as strings.

:xmlNode:`Sets` accepts the following sub-nodes:

- :xmlNode:`investments`, comma/space-separated string, **required**
  Specifies the valid indices for investment projects.
- :xmlNode:`capitals`, comma/space-separated string, *optional*
  Specifies the indices for NPP units.
- :xmlNode:`time_periods`, comma/space-separated string, *optional*
  Specifies the indices for time.
- :xmlNode:`resources`, comma/space-separated string, *optional*
  Specifies indices for the resources and liabilities.
- :xmlNode:`options`, semi-colon-separated list of strings, *optional*
  Specifies the indices for multiple project–option pairs.
  This sub-node accepts the following attribute:

  - :xmlAttr:`index`, string, **required**
    Specifies the index dependence. The valid index is
    :xmlString:`investments`.

Example XML:

.. code-block:: xml

   <Sets>
     <investments>
       HPFeedwaterHeaterUpgrade
       PresurizerReplacement
       ...
       ReplaceMoistureSeparatorReheater
     </investments>
     <time_periods>year1 year2 year3 year4 year5</time_periods>
     <resources>CapitalFunds OandMFunds</resources>
     <options index="investments">
       PlanA PlanB DoNothing;
       PlanA PlanB PlanC;
       ...
       PlanA PlanB PlanC DoNothing
     </options>
   </Sets>

.. _subsec-Parameters:

Parameters
----------

This subsection describes the XML nodes used to define the
:xmlNode:`Parameters` of the optimization model being performed through
LOGOS:

- :xmlNode:`net_present_values`, comma/space-separated string, **required**
  Specifies the NPVs for capital projects or project–option pairs.
  Optional attribute:

  - :xmlAttr:`index`, comma-separated string, *optional*
    Specifies the indices of this parameter; keywords should be predefined
    in :xmlNode:`Sets`. Valid keywords are :xmlString:`investments` and
    :xmlString:`options`.
    **Default**: :xmlString:`investments`.

- :xmlNode:`costs`, comma/space-separated string, **required**
  Specifies the costs for capital projects or project–option pairs.
  Optional attribute:

  - :xmlAttr:`index`, comma-separated string, *optional*
    Specifies the indices of this parameter; keywords should be predefined
    in :xmlNode:`Sets`. Valid keywords are:

    * :xmlString:`investments`
    * :xmlString:`investments, time_periods`
    * :xmlString:`options`
    * :xmlString:`options, resources`
    * :xmlString:`options, time_periods`
    * :xmlString:`options, resources, time_periods`

    **Default**: :xmlString:`investments`.

- :xmlNode:`available_capitals`, comma/space-separated string, **required**
  Specifies the available capitals for capital projects or project–option
  pairs.
  Optional attribute:

  - :xmlAttr:`index`, comma-separated string, *optional*
    Specifies the indices of this parameter; keywords should be predefined
    in :xmlNode:`Sets`. Valid keywords are:

    * :xmlString:`resources`
    * :xmlString:`time_periods`
    * :xmlString:`capitals`
    * :xmlString:`resources, time_periods`
    * :xmlString:`capitals, time_periods`

    **Default**: None.

Example XML:

.. code-block:: xml

   <Parameters>
     <net_present_values index="options">
       27.98 27.17 0.
       -10.07 -9.78 -9.22
       ...
       8.26 7.56 7.34 0.
     </net_present_values>
     <costs index="options,resources,time_periods">
       12.99 1.3 0 0 0
       ...
       0.01 0 0 0 0
     </costs>
     <available_capitals index="resources,time_periods">
       22.6 36.7 20.6 23.6 22.7
       0.08 0.17 0.05 0.15 0.14
     </available_capitals>
   </Parameters>

.. _subsec-Uncertainties:

Uncertainties
-------------

This subsection describes the XML nodes used to define the
:xmlNode:`Uncertainties` of the optimization model being performed
through LOGOS:

- :xmlNode:`available_capitals`, *optional*
  Specifies the scenarios associated with available capitals. This node
  accepts the attribute :xmlAttr:`index`, which should be consistent with
  the :xmlNode:`available_capitals` defined in :xmlNode:`Parameters`.
  It accepts the following sub-nodes:

  - :xmlNode:`totalScenarios`, integer, **required**
    Specifies the total number of scenarios for this parameter.
  - :xmlNode:`probabilities`, comma/space-separated float, **required**
    Specifies the probability for each scenario. The length must equal the
    total number of scenarios.
  - :xmlNode:`scenarios`, comma/space-separated float, **required**
    Specifies all scenarios for this parameter. The length must equal
    (total number of scenarios) × (length of the parameter as defined in
    :xmlNode:`Parameters`).

- :xmlNode:`net_present_values`, *optional*
  Specifies the scenarios associated with :xmlNode:`net_present_values`.
  This node accepts the attribute :xmlAttr:`index`, which should be
  consistent with :xmlNode:`net_present_values` as defined in
  :xmlNode:`Parameters`. It accepts the following sub-nodes:

  - :xmlNode:`totalScenarios`, integer, **required**
  - :xmlNode:`probabilities`, comma/space-separated float, **required**
  - :xmlNode:`scenarios`, comma/space-separated float, **required**

The overall number of scenarios is the total number of scenarios for
:xmlNode:`available_capitals` multiplied by the total number of scenarios
for :xmlNode:`net_present_values`.

Example XML:

.. code-block:: xml

   <Uncertainties>
     <available_capitals index="resources,time_periods">
       <totalScenarios>10</totalScenarios>
       <probabilities>
         0.5, 0.5
       </probabilities>
       <scenarios>
         20.0 34.0 17.0 20.0 18.0 0.08 0.17 0.05 0.15 0.14
         23.0 38.0 22.0 25.0 24.0 0.08 0.17 0.05 0.15 0.14
       </scenarios>
     </available_capitals>
     <net_present_values index="options">
       <totalScenarios>9</totalScenarios>
       <probabilities>
         0.3 0.8
       </probabilities>
       <scenarios>
         13.3129 12.0228 0.0 -10.07
         ...
       </scenarios>
     </net_present_values>
   </Uncertainties>

External Constraints
--------------------
.. _subsec-ExternalConstraints:

This subsection describes the XML nodes used to define the
:xmlNode:`ExternalConstraints` of the optimization model being performed
through LOGOS. This node accepts the following sub-node(s):

- :xmlNode:`constraint`, string, **required**
  Specifies the external Python module file name along with its absolute
  or relative path. This external Python module contains the user-defined
  additional constraint.

  .. note::

     If a relative path is specified, the code first checks relative to
     the working directory, then relative to the location of the input
     file. The working directory can be specified in :xmlNode:`Settings`
     (see Section :ref:`subsec-Settings`). The extension ``.py`` is
     optional for the module file name.

  This sub-node also requires the following attribute:

  - :xmlAttr:`name`, string, **required**
    Specifies the name of the constraint that will be added to the
    optimization problem.

Example XML:

.. code-block:: xml

   <ExternalConstraints>
     <constraint name="con_I">externalConst</constraint>
     <constraint name="con_II">externalConstII.py</constraint>
   </ExternalConstraints>

These constraints are Python modules with a format automatically
interpretable by LOGOS. Users can define their own constraint, and LOGOS
will embed and use the constraint as though it were an internal part of
the optimization model.

Example Python function:

.. code-block:: python

   # External constraint function
   import numpy as np
   import pyomo.environ as pyomo

   def initialize():
       """
       Optional method.

       Optimization model parameter values can be updated/modified
       without directly accessing the optimization model.
       Values will be updated in-place.

       Returns
       -------
       updateDict : dict
           Dictionary of the form {paramName: paramInfoDict},
           where paramInfoDict contains {Indices: Values}.
           Indices are parameter indices (either strings or tuples of
           strings, depending on the parameter's dimensionality).
           Values are the new values being assigned.
       """
       updateDict = {
           "available_capitals": {"None": 16},
           "costs": {
               "1": 1, "2": 3, "3": 7, "4": 4, "5": 8,
               "6": 9, "7": 6, "8": 10, "9": 2, "10": 5,
           },
       }
       return updateDict

   def constraint(var, sets, params):
       """
       Required method.

       External constraint provided by the user that will be added to the
       optimization problem.

       Parameters
       ----------
       var : object
           The internally used decision variable. The dimensions/indices
           depend on the type of optimization problem (i.e., the
           ``<problem_type>`` from the LOGOS input file).
           Currently supported types:

           1. "singleknapsack": var[:] indexed by the elements of
              the ``investments`` set.
           2. "multipleknapsack": var[:,:] indexed by combinations
              of ``investments`` and ``capitals``.
           3. "mckp": var[:,:] indexed by combinations of
              ``investments`` and ``options``.

           Note: all index elements are converted to strings.

       sets : dict
           All sets from the LOGOS input file, stored as
           {setName: setObject}.

       params : dict
           All parameters from the LOGOS input file, stored as
           {paramName: paramObject}.

       Returns
       -------
       tuple
           Either (constraintRule,) or (constraintRule, indices).

       Notes
       -----
       Any modifications to "sets" and "params" in this function have
       impact only locally within this module. Internal constraints and
       objectives in LOGOS use the original versions.
       """

       investments = sets["investments"]

       def constraintRule(self, i):
           """
           Expression for the user-provided external constraint.

           Parameters
           ----------
           self : object
               Required by Pyomo, but not used here.
           i : str
               Element from the index set.

           Returns
           -------
           expression
               A Pyomo expression defining the constraint for index i.
           """
           # Example: place an upper bound on the i-th decision variable
           return var[i] <= 1

       # Return the rule and the indexing set for iterative construction
       return (constraintRule, investments)


.. _subsec-Settings:

Settings: Options for Optimization
----------------------------------

This subsection describes the XML nodes used to define the
:xmlNode:`Settings` of the optimization model being performed through
LOGOS:

- :xmlNode:`problem_type`, string, **required**
  Specifies the type of optimization problem. Available types include:

  * :xmlString:`SingleKnapsack`,
  * :xmlString:`MultipleKnapsack`,
  * :xmlString:`MCKP` for risk-informed stochastic optimization;

  * :xmlString:`droskp`, :xmlString:`dromkp`,
    :xmlString:`dromckp` for distributionally robust optimization;

  * :xmlString:`cvarskp`, :xmlString:`cvarmkp`,
    :xmlString:`cvarmckp` for CVaR-based optimization.

- :xmlNode:`solver`, string, *optional*
  Specifies the solver, e.g.:

  * :xmlString:`cbc` from ``https://github.com/coin-or/Cbc.git``
  * :xmlString:`glpk` from ``https://www.gnu.org/software/glpk/``

- :xmlNode:`sense`, string, *optional*
  Specifies :xmlString:`minimize` or :xmlString:`maximize` for minimization
  or maximization, respectively.
  **Default**: :xmlString:`minimize`.

- :xmlNode:`mandatory`, comma/space-separated string, *optional*
  Specifies regulatorily mandated or must-do projects.

- :xmlNode:`nonSelection`, boolean, *optional*
  Indicates whether the investment options include a *DoNothing* option.
  **Default**: :xmlString:`False`.

- :xmlNode:`lowerBounds`, comma/space-separated integers, *optional*
  Specifies the lower bounds for decision variables.

- :xmlNode:`upperBounds`, comma/space-separated integers, *optional*
  Specifies the upper bounds for decision variables.

- :xmlNode:`consistentConstraintI`, string, *optional*
  Indicates whether this constraint is enabled.
  **Default**: :xmlString:`True`.

- :xmlNode:`consistentConstraintII`, string, *optional*
  Indicates whether this constraint is enabled.
  **Default**: :xmlString:`False`.

- :xmlNode:`solverOptions`, *optional*
  Accepts additional options for the solver provided in
  :xmlNode:`solver`. A simple XML node containing only tags and text
  can be used to pass options, for example:

  .. code-block:: xml

     <solverOptions>
       <threads>1</threads>
       <StochSolver>EF</StochSolver>
     </solverOptions>

  If the problem type is distributionally robust optimization, an
  additional option :xmlNode:`radius_ambiguity` can be used to control
  the Wasserstein distance

  .. (see Section :ref:`sec-DROCapitalBudgeting`).

  If the problem type uses conditional value-at-risk, additional options are available:

  .. (see Section :ref:`sec-CVaR`)

  - :xmlNode:`risk_aversion`, float in :math:`[0,1]`, *optional*
    Indicates the weight on maximizing expected NPV versus penalizing
    solutions that yield low-NPV scenarios.

  - :xmlNode:`confidence_level`, float in :math:`[0,1]`, *optional*
    Indicates the confidence level, i.e., the :math:`\alpha`-percentile
    of the loss.

Example XML:

.. code-block:: xml

   <Settings>
     <mandatory>
       PresurizerReplacement
       ...
       ReplaceInstrumentationAndControlCables
     </mandatory>
     <nonSelection>True</nonSelection>
     <consistentConstraintI>True</consistentConstraintI>
     <consistentConstraintII>True</consistentConstraintII>
     <solver>cbc</solver>
     <solverOptions>
       <threads>1</threads>
       <StochSolver>EF</StochSolver>
     </solverOptions>
     <sense>maximize</sense>
     <problem_type>mckp</problem_type>
   </Settings>
