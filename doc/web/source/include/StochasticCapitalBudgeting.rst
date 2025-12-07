.. _sec-StochasticCapitalBudgeting:

Prioritizing Project Selection to Hedge Against Uncertainty
===========================================================

One limitation of traditional optimization models for capital budgeting
is that they do not account for risk and uncertainty in profit and cost
streams associated with individual projects, nor do they account for
risk in resource availability in future years. Projects can incur cost
overruns, especially when projects are large, performed infrequently, or
when there is risk regarding technical viability, external contractors,
and/or suppliers of requisite parts and materials. Occasionally,
projects are performed ahead of schedule and with savings in cost.
Planned budgets for capital improvements can be cut, and key personnel
may be lost. Or, there may be surprise budgetary windfalls for
maintenance activities due to decreased costs for “unplanned”
maintenance.

In such cases, how should we resolve capital budgeting when we have risk
forecasts for costs, profits, and budgets? One approach is to re-solve
the models described in the previous section once refined forecasts for
these parameters become available. However, it is not always practical
to fully revise a project portfolio whenever better forecasts become
available.

To prioritize project selection using a risk forecast for these
parameters, a two-stage stochastic optimization model
:cite:`PrioritizingProjectSelection` is employed to provide priority
lists to decision-makers and support better risk-informed decisions.
Its inputs include those described in the previous sections for
different variants of the capital budgeting problem, except that a
probabilistic description of the uncertain parameters is integrated into
the optimization process.

The two-stage stochastic optimization model forms a priority list as its
first-stage decision, then forms a corresponding project portfolio for
each scenario as its second-stage decision. When forming the optimal
second-stage project portfolio under a specific scenario, the model
ensures that the portfolio is consistent with the first-stage
prioritization (i.e., a project can be selected only if all
higher-priority projects are also selected). Thus, the portfolios of
projects corresponding to different scenarios are nested.

Notation and formulation
------------------------

Indices and sets
^^^^^^^^^^^^^^^^

- :math:`t \in T`:
  time periods (years).
- :math:`i, i' \in I`:
  candidate projects.
- :math:`j \in J_i`:
  options for selecting project :math:`i`.
- :math:`k \in K`:
  types of resources.
- :math:`m \in M`:
  units of the nuclear power plant (NPP).
- :math:`\omega \in \Omega`:
  scenarios.

Data
^^^^

- :math:`a_i^\omega`:
  reward for selecting project :math:`i` under scenario :math:`\omega`.
- :math:`a_{ij}^\omega`:
  reward for selecting project :math:`i` via option :math:`j`
  under scenario :math:`\omega`.
- :math:`b^\omega`:
  available budget under scenario :math:`\omega`.
- :math:`b_k^\omega`:
  available budget for a resource of type :math:`k` under scenario
  :math:`\omega`.
- :math:`b_t^\omega`:
  available budget in year :math:`t` under scenario :math:`\omega`.
- :math:`b_m^\omega`:
  available budget for unit :math:`m` under scenario :math:`\omega`.
- :math:`b_{kt}^\omega`:
  available budget for a resource of type :math:`k` in year :math:`t`
  under scenario :math:`\omega`.
- :math:`c_i^\omega`:
  cost of investment :math:`i` under scenario :math:`\omega`.
- :math:`c_{ik}^\omega`:
  consumption of resource of type :math:`k` if project :math:`i` is
  selected under scenario :math:`\omega`.
- :math:`c_{ijt}^\omega`:
  consumption of resources in year :math:`t` if project :math:`i` is
  performed via option :math:`j` under scenario :math:`\omega`.
- :math:`c_{ijkt}^\omega`:
  consumption of resource of type :math:`k` in year :math:`t` if
  project :math:`i` is performed via option :math:`j` under scenario
  :math:`\omega`.
- :math:`q^\omega`:
  probability of scenario :math:`\omega`.

Decision variables
^^^^^^^^^^^^^^^^^^

- :math:`x_i^\omega`:
  equals 1 if project :math:`i` is selected under scenario :math:`\omega`;
  0 otherwise.
- :math:`x_{im}^\omega`:
  equals 1 if project :math:`i` is selected for unit :math:`m` under
  scenario :math:`\omega`; 0 otherwise.
- :math:`x_{ij}^\omega`:
  equals 1 if project :math:`i` is selected via option :math:`j` under
  scenario :math:`\omega`; 0 otherwise.
- :math:`y_{ii'}`:
  equals 1 if project :math:`i` has no lower priority than project
  :math:`i'`; 0 otherwise.

.. _subsec-RIskp:

Risk-Informed Single Knapsack Problem Optimization
--------------------------------------------------

Risk-Informed Simple Knapsack Problem
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Formulation
~~~~~~~~~~~

.. math::
   :label: RISimpleKP

   \begin{aligned}
   \max_{x} \quad
   & \sum_{\omega \in \Omega} q^\omega \sum_{i \in I} a_i^\omega x_i^\omega \\
   \text{s.t.} \quad
   & \sum_{i \in I} c_i^\omega x_i^\omega \le b^\omega,
     && \forall \omega \in \Omega, \\
   & y_{ii'} + y_{i'i} \ge 1,
     && i < i', \\
   & x_i^\omega \ge x_{i'}^\omega + y_{ii'} - 1,
     && i \ne i',\ \forall \omega \in \Omega.
   \end{aligned}

For simplicity in what follows, the variable :math:`y_{ii'} = 1` means
that project :math:`i` is of higher priority than :math:`i'`, even
though the variable definition allows for ties (i.e., projects of the
same priority).

- Constraint :eq:`RISimpleKP` (budget constraint) requires the solution
  to be within budget under each scenario.
- Constraint :math:`y_{ii'} + y_{i'i} \ge 1` indicates that either
  project :math:`i` is of higher priority than project :math:`i'`, or
  vice versa, or that both are of equal priority (i.e., a tie).
- Constraint :math:`x_i^\omega \ge x_{i'}^\omega + y_{ii'} - 1`
  indicates that if project :math:`i` is of higher priority than project
  :math:`i'` (:math:`y_{ii'} = 1`) and we select the lower priority
  project, then we must also select the higher priority project. If
  :math:`y_{ii'} = 0`, or if :math:`x_{i'}^\omega = 0`, then the
  constraint is vacuous.

In order to handle risk in the capital budgeting problems, the entity
:xmlNode:`Uncertainties`
(see :ref:`subsec-Uncertainties`) is used to specify different scenarios
of input parameters.

Example LOGOS input XML
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: xml

   <Logos>
     <Sets>
       <investments>
         1,2,3,4,5,6,7,8,9,10
       </investments>
     </Sets>

     <Parameters>
       <net_present_values index="investments">
         18,20,17,19,25,21,27,23,25,24
       </net_present_values>
       <costs index="investments">
         1,3,7,4,8,9,6,10,2,5
       </costs>
       <available_capitals>
         15
       </available_capitals>
     </Parameters>

     <Uncertainties>
       <available_capitals>
         <totalScenarios>10</totalScenarios>
         <probabilities>
           0.012, 0.019, 0.032, 0.052, 0.086,
           0.142, 0.235, 0.188, 0.141, 0.093
         </probabilities>
         <scenarios>
           11, 12, 13, 14, 15, 16, 17, 18, 19, 20
         </scenarios>
       </available_capitals>
       <net_present_values>
         <totalScenarios>2</totalScenarios>
         <probabilities>
           0.3, 0.7
         </probabilities>
         <scenarios>
           18,20,17,19,25,21,27,23,25,24,
           18,20,17,19,25,21,27,23,25,24
         </scenarios>
       </net_present_values>
     </Uncertainties>
     ...
   </Logos>

When running this case, LOGOS generates a CSV (comma-separated values)
file containing solutions for the optimization problem (i.e., values of
decision variables and the maximum profit; :code:`MaxNPV` is used to
describe the maximum profit). The header of this CSV file contains the
indices listed under :xmlNode:`investments`, used as indices for the
decision variables, the objective variable :code:`MaxNPV`, the scenario
name, and the associated probability weight. The data provides the
values for both decision variables and the objective variable.

Example LOGOS output CSV
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :linenos:
   :caption: Example output for risk-informed simple knapsack

   1,10,2,3,4,5,6,7,8,9,ScenarioName,ProbabilityWeight,MaxNPV
   1.0,0.0,0.0,0.0,0.0,0.0,0.0,1.0,0.0,1.0,scenario_1,0.0036,70.0
   1.0,0.0,0.0,0.0,0.0,0.0,0.0,1.0,0.0,1.0,scenario_6,0.0224,70.0
   1.0,0.0,0.0,0.0,0.0,0.0,0.0,1.0,0.0,1.0,scenario_5,0.0096,70.0
   ...
   1.0,1.0,1.0,0.0,0.0,0.0,0.0,1.0,0.0,1.0,scenario_18,0.0987,114.0

Risk-Informed Multi-Dimensional Knapsack Problem
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Formulation
~~~~~~~~~~~

.. math::
   :label: RIMultiDKP

   \begin{aligned}
   \max_{x} \quad
   & \sum_{\omega \in \Omega} q^\omega \sum_{i \in I} a_i^\omega x_i^\omega \\
   \text{s.t.} \quad
   & \sum_{i \in I} c_{it}^\omega x_i^\omega \le b_t^\omega,
     && t \in T,\ \forall \omega \in \Omega, \\
   & y_{ii'} + y_{i'i} \ge 1,
     && i < i', \\
   & x_i^\omega \ge x_{i'}^\omega + y_{ii'} - 1,
     && i \ne i',\ \forall \omega \in \Omega.
   \end{aligned}

Example LOGOS input XML
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: xml

   <Logos>
     <Sets>
       <investments>
         1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16
       </investments>
       <time_periods>
         1,2,3,4,5
       </time_periods>
     </Sets>

     <Parameters>
       <net_present_values index="investments">
         2.315,0.824,22.459,60.589,0.667,5.173,4.003,0.582,0.122,
         -2.870,-0.102,-0.278,-0.322,-3.996,-0.246,-20.155
       </net_present_values>
       <costs index="investments, time_periods">
         0.219,0.257,0.085,0.0,0.0,
         0.0,0.0,0.122,0.103,0.013,
         5.044,1.839,0.0,0.0,0.0,
         6.74,6.134,10.442,0.0,0.0,
         0.425,0.0,0.0,0.0,0.0,
         2.125,2.122,0.0,0.0,0.0,
         2.387,0.19,0.012,2.383,0.192,
         0.0,0.95,0.0,0.0,0.0,
         0.03,0.03,0.688,0.0,0.0,
         0,0.2,0.763,0.739,2.539,
         0.081,0.032,0,0,0,
         0.3,0,0,0,0,
         0.347,0,0,0,0,
         4.025,0.297,0,0,0,
         0.095,0.095,0.095,0,0,
         5.487,5.664,0.5,6.803,6.778
       </costs>
       <available_capitals index="time_periods">
         18,18,18,18,18
       </available_capitals>
     </Parameters>

     <Uncertainties>
       <available_capitals>
         <totalScenarios>10</totalScenarios>
         <probabilities>
           0.012, 0.019, 0.032, 0.052, 0.086,
           0.142, 0.235, 0.188, 0.141, 0.093
         </probabilities>
         <!--
           scenarios is ordered by numberScenarios * parametersIndex,
           numberScenarios * time_periods = 10 * 5
         -->
         <scenarios>
           11, 11, 11, 11, 11,
           12, 12, 12, 12, 12,
           13, 13, 13, 13, 13,
           14, 14, 14, 14, 14,
           15, 15, 15, 15, 15,
           16, 16, 16, 16, 16,
           17, 17, 17, 17, 17,
           18, 18, 18, 18, 18,
           19, 19, 19, 19, 19,
           20, 20, 20, 20, 20
         </scenarios>
       </available_capitals>
     </Uncertainties>

     <Settings>
       <mandatory>10,11,12,13,14,15,16</mandatory>
       <solver>cbc</solver>
       <sense>maximize</sense>
     </Settings>
   </Logos>

Example LOGOS output CSV
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :linenos:
   :caption: Example output for risk-informed multi-dimensional knapsack

   1,10,11,12,13,14,15,16,2,3,4,5,6,7,8,9,ScenarioName,ProbabilityWeight,MaxNPV
   1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.0,0.0,1.0,0.0,0.0,1.0,0.0,scenario_1,0.012,-23.581
   ...
   1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.0,1.0,1.0,1.0,0.0,1.0,1.0,scenario_10,0.093,42.303

Risk-Informed Multiple Knapsack Problem Optimization
----------------------------------------------------
.. _subsec-RImkp:

Model formulation
^^^^^^^^^^^^^^^^^

Objective and priority consistency
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. math::
   :label: RIMKP_obj

   \mathop{\max}_{x,y} \;
   \sum_{\omega \in \Omega} q^\omega
   \sum_{i \in I} \sum_{m \in M} a_i^\omega x_{im}^\omega

subject to

.. math::
   :label: RIMKP_conb

   y_{ii'} + y_{i'i} \ge 1,
   \qquad i < i',\ i,i' \in I,

.. math::
   :label: RIMKP_conc

   \sum_{m=1}^M x_{im}^\omega
   \ge \sum_{m=1}^M x_{i'm}^\omega + y_{ii'} - 1,
   \qquad i \ne i',\ i,i' \in I,\ \omega \in \Omega.

Constraint :eq:`RIMKP_conb` indicates that either project :math:`i`
is of higher priority than project :math:`i'`, or vice versa, or that
both are of equal priority (i.e., a tie).

Constraint :eq:`RIMKP_conc` indicates that if project :math:`i`
is higher priority than project :math:`i'`
(:math:`y_{ii'} = 1`), and we select the lower priority project
*for some unit*, then we must also select the higher priority project.
If :math:`y_{ii'} = 0`, or if
:math:`\sum_{m=1}^{M} x_{i'm}^\omega = 0`, then the constraint is
vacuous.

Budget and unit selection constraints
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. math::
   :label: RIMKP_budget

   \sum_{i \in I} c_i^\omega x_{im}^\omega \le b_m^\omega,
   \qquad m \in M,\ \omega \in \Omega,

Constraint :eq:`RIMKP_budget` requires that we be within budget for
each unit under each scenario.

.. math::
   :label: RIMKP_oneUnit

   \sum_{m \in M} x_{im}^\omega \le 1,
   \qquad i \in I,\ \omega \in \Omega,

Constraint :eq:`RIMKP_oneUnit` ensures that each project :math:`i` is
selected for at most one unit.

Example LOGOS input XML
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: xml

   <Logos>
     <Sets>
       <investments>
         1,2,3,4,5,6,7,8,9,10
       </investments>
       <capitals>
         unit_1, unit_2
       </capitals>
     </Sets>

     <Parameters>
       <net_present_values index="investments">
         78, 35, 89, 36, 94, 75, 74, 79, 80, 16
       </net_present_values>
       <costs index="investments">
         18, 9, 23, 20, 59, 61, 70, 75, 76, 30
       </costs>
       <available_capitals index="capitals">
         103, 156
       </available_capitals>
     </Parameters>

     <Uncertainties>
       <available_capitals>
         <totalScenarios>2</totalScenarios>
         <probabilities>
           0.3 0.7
         </probabilities>
         <scenarios>
           103, 156,
           103, 156
         </scenarios>
       </available_capitals>
       <net_present_values>
         <totalScenarios>2</totalScenarios>
         <probabilities>
           0.3, 0.7
         </probabilities>
         <scenarios>
           78, 35, 89, 36, 94, 75, 74, 79, 80, 16,
           78, 35, 89, 36, 94, 75, 74, 79, 80, 16
         </scenarios>
       </net_present_values>
     </Uncertainties>

     <Settings>
       <solver>glpk</solver>
       <sense>maximize</sense>
       <problem_type>MultipleKnapsack</problem_type>
     </Settings>
   </Logos>

Example LOGOS output CSV
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :linenos:
   :caption: Example output for risk-informed multiple knapsack

   "('1', 'unit_1')","('1', 'unit_2')","('10', 'unit_1')","('10', 'unit_2')","('2', 'unit_1')","('2', 'unit_2')","('3', 'unit_1')",
   "('3', 'unit_2')","('4', 'unit_1')","('4', 'unit_2')","('5', 'unit_1')","('5', 'unit_2')","('6', 'unit_1')","('6', 'unit_2')",
   "('7', 'unit_1')","('7', 'unit_2')","('8', 'unit_1')","('8', 'unit_2')","('9', 'unit_1')","('9', 'unit_2')",
   ScenarioName,ProbabilityWeight,MaxNPV
   1.0,0.0,0.0,0.0,0.0,0.0,1.0,0.0,0.0,1.0,0.0,1.0,1.0,0.0,0.0,0.0,0.0,0.0,0.0,1.0,scenario_1,0.09,452.0
   1.0,0.0,0.0,0.0,0.0,0.0,1.0,0.0,0.0,1.0,0.0,1.0,1.0,0.0,0.0,0.0,0.0,0.0,0.0,1.0,scenario_2,0.21,452.0
   ...

.. _subsec-RImckp:

Risk-Informed Multiple-Choice Multi-Dimensional Knapsack Problem Optimization
-------------------------------------------------------------------------------

Model formulation
^^^^^^^^^^^^^^^^^

Objective and priority consistency
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. math::
   :label: RIMCKP_obj

   \mathop{\max}_{x,y} \;
   \sum_{\omega \in \Omega} q^\omega
   \sum_{i \in I} \sum_{j \in J_i} a_{ij}^\omega x_{ij}^\omega

subject to

.. math::
   :label: RIMCKP_conb

   y_{ii'} + y_{i'i} \ge 1,
   \qquad i < i',\ i,i' \in I,

.. math::
   :label: RIMCKP_conc

   \sum_{j=1}^{J_i} x_{ij}^\omega
   \ge \sum_{j=1}^{J_i} x_{i'j}^\omega + y_{ii'} - 1,
   \qquad i \ne i',\ i,i' \in I,\ \omega \in \Omega.

Constraint :eq:`RIMCKP_conb` indicates that either project :math:`i` is
of higher priority than project :math:`i'`, or vice versa, or that both
are of equal priority (i.e., a tie).

Constraint :eq:`RIMCKP_conc` indicates that if project :math:`i` is
higher priority than project :math:`i'`
(:math:`y_{ii'} = 1`), and we select the lower priority project *under
some option*, then we must also select the higher priority project. If
:math:`y_{ii'} = 0`, or if
:math:`\sum_{j=1}^{J_i} x_{i'j}^\omega = 0`, then the constraint is
vacuous.

Budget and option constraints
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. math::
   :label: RIMCKP_budget

   \sum_{i \in I} \sum_{j \in J_i} c_{ijkt}^\omega x_{ij}^\omega
   \le b_{kt}^\omega,
   \qquad k \in K,\ t \in T,\ \omega \in \Omega,

Constraint :eq:`RIMCKP_budget` requires that we be within budget in each
time period, for each resource type, and under each scenario.

.. math::
   :label: RIMCKP_oneOpt

   \sum_{j \in J_i} x_{ij}^\omega \le 1,
   \qquad i \in I,\ \omega \in \Omega.

Constraint :eq:`RIMCKP_oneOpt` ensures that we select project :math:`i`
via at most one option. This illustrates a situation in which we must
include the :code:`DoNothing` option among the alternatives to optional
projects.

Example LOGOS input XML
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: xml

   <Logos>
     <Sets>
       <investments>
         1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
         11, 12, 13, 14, 15, 16, 17
       </investments>
       <options index="investments">
         1;
         1;
         1;
         1,2,3;
         1,2,3,4;
         1,2,3,4,5,6,7;
         1;
         1;
         1;
         1;
         1;
         1;
         1;
         1;
         1;
         1;
         1
       </options>
     </Sets>

     <Parameters>
       <net_present_values index="options">
         2.046
         2.679
         2.489
         2.61
         2.313
         1.02
         3.013
         2.55
         3.351
         3.423
         3.781
         2.525
         2.169
         2.267
         2.747
         4.309
         6.452
         2.849
         7.945
         2.538
         1.761
         3.002
         3.449
         2.865
         3.999
         2.283
         0.9
         8.608
       </net_present_values>
       <costs index="options">
         36538462
         83849038
         4615385
         2788461538
         2692307692
         5480769231
         1634615385
         2981730768
         7211538462
         9038461538
         649038462
         650000000
         216346154
         212500000
         3076923077
         3942307692
         1144230769
         675721154
         1442307692
         99711538
         4807692
         123076923
         138461538
         86538462
         108653846
         75092404
         6413462
         147932692
       </costs>
       <available_capitals>
         15E9
       </available_capitals>
     </Parameters>

     <Uncertainties>
       <available_capitals>
         <totalScenarios>3</totalScenarios>
         <probabilities>
           0.2,0.6,0.2
         </probabilities>
         <scenarios>
           5E9,10E9,15E9
         </scenarios>
       </available_capitals>
     </Uncertainties>

     <Settings>
       <solver>cbc</solver>
       <solverOptions>
         <threads>1</threads>
         <StochSolver>EF</StochSolver>
       </solverOptions>
       <sense>maximize</sense>
       <problem_type>mckp</problem_type>
     </Settings>
   </Logos>

Example LOGOS output CSV
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :linenos:
   :caption: Example output for risk-informed multiple-choice knapsack

   "('1', '1')","('10', '1')","('11', '1')","('12', '1')","('13', '1')","('14', '1')","('15', '1')","('16', '1')",
   "('17', '1')","('2', '1')","('3', '1')","('4', '1')","('4', '2')","('4', '3')","('5', '1')","('5', '2')","('5', '3')",
   "('5', '4')","('6', '1')","('6', '2')","('6', '3')","('6', '4')","('6', '5')","('6', '6')","('6', '7')","('7', '1')",
   "('8', '1')","('9', '1')",ScenarioName,ProbabilityWeight,MaxNPV
   1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,1.0,1.0,1.0,1.0,scenario_1,0.2,53.865
   1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.0,0.0,1.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,1.0,1.0,1.0,1.0,scenario_2,0.6,59.488
   1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.0,0.0,0.0,0.0,1.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,1.0,1.0,1.0,1.0,scenario_3,0.2,59.826
