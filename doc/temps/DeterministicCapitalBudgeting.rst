Deterministic Capital Budgeting
===============================
.. _sec-DeterministicCapitalBudgeting:

We consider a capital budgeting problem for a nuclear generation station, with
possible extension to a larger fleet of plants. Due to limited resources, we can
select only a subset from a list of candidate capital projects. Our goal is to
maximize the overall NPV associated with the selected subset. In doing so, we
must respect resource limits and capture key structural and stochastic
dependencies of the system, although in this section we start with the simpler
deterministic case, ignoring randomness. Example projects include upgrading a
steam turbine, refurbishing or replacing a set of reactor coolant pumps, and
replacing a set of feed-water heaters.

Indices, Sets, Data, and Decision Variables
-------------------------------------------

We use the following notation.

**Indexes and sets**

- :math:`t \in T`: time periods (years)
- :math:`i \in I`: investment candidate projects
- :math:`j \in J_i`: options for selecting project :math:`i`
- :math:`k \in K`: types of resources

**Data**

- :math:`a_{ij}`: reward (revenue minus financial cost) of selecting project
  :math:`i` via option :math:`j`
- :math:`b_{kt}`: available budget for resource type :math:`k` in year
  :math:`t`
- :math:`c_{ijkt}`: consumption of resource of type :math:`k` in year
  :math:`t` if project :math:`i` is performed via option :math:`j`

**Decision variables**

- :math:`x_{ij}`: equals 1 if project :math:`i` is selected via option
  :math:`j`, and 0 otherwise.

Model Formulation
-----------------

*Formulation.*

.. math::
   :label: model-deter

   \begin{aligned}
   \max_x \quad
   & \sum_{i \in I} \sum_{j \in J_i} a_{ij} x_{ij}
   \tag{\ref{obj_deter}} \\
   \text{s.t.} \quad
   & \sum_{j \in J_i} x_{ij} = 1,
     && i \in I, \\
   & \sum_{i \in I} \sum_{j \in J_i} c_{ijkt} x_{ij} \le b_{kt},
     && k \in K,\; t \in T, \\
   & x_{ij} \in \{0,1\},
     && j \in J_i,\; i \in I.
   \end{aligned}

The decision variables :math:`x_{ij}` indicate whether we choose to do project
:math:`i` by means :math:`j`. Restated, if :math:`x_{ij} = 1`, then we recommend
doing project :math:`i` via option :math:`j`; taken together, these decision
variables produce both a portfolio of selected projects and a schedule for
performing those projects over time.

The set of available options :math:`j \in J_i` can explicitly include the
“do-nothing” option. The first constraint ensures that we choose exactly one
option from the available set for each project, including the possibility of
selecting the do-nothing option. Even if we select the do-nothing option for a
project, it induces an NPV :math:`a_{ij}`, which may be negative, representing
growing O&M costs, losses in plant efficiency, etc. The second structural
constraint ensures that the budget of each resource :math:`k` is respected in
each year :math:`t`. The objective function includes the NPV for each
project–option pair, :math:`a_{ij}`, and the corresponding NPV is selected by
the binary decision variable :math:`x_{ij}`.

Projects that must be done (e.g., for safety or regulatory reasons) can be
handled within the same mathematical formulation. The set :math:`J_i`
typically includes a do-nothing option for each project, but when project
:math:`i` must be done, we simply omit the do-nothing option from :math:`J_i`.
Alternatively, one can remove the explicit do-nothing option, replace the first
structural constraint with an inequality, and add an additional set of must-do
projects with equality constraints. Both approaches are mathematically
equivalent and represent modeling choices.

In LOGOS, we use :xmlNode:`mandatory` and :xmlNode:`nonSelection` to handle
these conditions:

- :xmlNode:`mandatory` specifies must-do projects.
- :xmlNode:`nonSelection` activates the do-nothing option.

.. note::

   If a project is listed under :xmlNode:`mandatory`, the do-nothing option is
   not allowed for this project. Handling the do-nothing option implicitly
   leads to the NPV being calculated relative to that of the do-nothing option.

The objective of capital budgeting is to find the right combination of binary
decisions for every investment so that overall profit is maximized. The output
is a collection of projects to be carried out, which we refer to as a *project
portfolio*. In practice, several optional constraints (resources/liabilities,
dependencies/synergies, options, time windows, etc.) must often be fulfilled.
This leads to various knapsack-type problem variants. In the following
subsections, we present several variants of the above capital budgeting problem.

Single Knapsack Problem Optimization
------------------------------------
.. _subsec-skp:

Simple Knapsack Problem
~~~~~~~~~~~~~~~~~~~~~~~

The simple knapsack problem (KP) for capital budgeting is defined as follows:
we are given an investment set :math:`I`, consisting of investments
:math:`i \in I` with profit :math:`a_i` (e.g., NPV), cost :math:`c_i`, and
available budget :math:`b`. The objective is to select a subset of :math:`I`
such that the total profit is maximized and the total cost does not exceed
:math:`b`.

The integer programming formulation is:

.. math::
   :label: simpleKP

   \begin{aligned}
   \max_x \quad
   & \sum_{i \in I} a_i x_i \\
   \text{s.t.} \quad
   & \sum_{i \in I} c_i x_i \le b, \\
   & x_i \in \{0,1\}, \quad i \in I.
   \end{aligned}

Example LOGOS input XML:

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

     <Settings>
       <solver>cbc</solver>
       <sense>maximize</sense>
     </Settings>
   </Logos>

Example LOGOS output CSV:

.. code-block:: none

   1,2,3,4,5,6,7,8,9,10,MaxNPV
   1.0,1.0,0.0,1.0,0.0,0.0,0.0,0.0,1.0,1.0,106.0

In this case, projects **1, 2, 4, 9, and 10** are selected with a maximum
profit of 106.0.

Bounded Knapsack Problem
~~~~~~~~~~~~~~~~~~~~~~~~

In some cases, not all investments are distinct. For example, there may be
:math:`n_i` identical pumps or valves to be replaced. Then the number of
decision variables equals the number of distinct investments, rather than the
total number of items. The constraint for decision variables becomes:

.. math::

   0 \le x_i \le n_i, \quad i \in I.

The resulting bounded knapsack problem (BKP) is:

.. math::
   :label: boundedKP

   \begin{aligned}
   \max_x \quad
   & \sum_{i \in I} a_i x_i \\
   \text{s.t.} \quad
   & \sum_{i \in I} c_i x_i \le b, \\
   & x_i \in \{0, \dots, n_i\}, \quad i \in I.
   \end{aligned}

Example LOGOS input XML:

.. code-block:: xml

   <Logos>
     <Sets>
       <investments>
         1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13,
         14, 15, 16, 17, 18, 19, 20, 21, 22
       </investments>
     </Sets>

     <Parameters>
       <net_present_values index="investments">
         150,35,200,60,60,45,60,40,30,10,70,
         30,15,10,40,70,75,80,20,12,50,10
       </net_present_values>
       <costs index="investments">
         9,13,153,50,15,68,27,39,23,52,11,32,
         24,48,73,42,43,22,7,18,4,30
       </costs>
       <available_capitals>
         400
       </available_capitals>
     </Parameters>

     <Settings>
       <lowerBounds>
         0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
         0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
       </lowerBounds>
       <upperBounds>
         1,1,2,2,2,3,3,3,1,3,1,
         1,2,2,1,1,1,1,1,2,1,2
       </upperBounds>
       <solver>glpk</solver>
       <sense>maximize</sense>
     </Settings>
   </Logos>

Example LOGOS output CSV:

.. code-block:: none

   1,2,...,21,22,MaxNPV
   1.0,1.0,...,1.0,0.0,1010.0

Multi-Dimensional Knapsack Problem (DKP)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We now account for limited commitments of multiple critical resources such as:

- capital costs,
- O&M costs,
- time and labor-hours during outages,
- personnel, equipment, and workspace.

Let :math:`c_{ik}` denote the cost of investment :math:`i` in resource
dimension :math:`k` and :math:`b_k` the available amount of resource :math:`k`.
The multi-dimensional knapsack problem is:

.. math::
   :label: DKP-static

   \begin{aligned}
   \max_x \quad
   & \sum_{i \in I} a_i x_i \\
   \text{s.t.} \quad
   & \sum_{i \in I} c_{ik} x_i \le b_k, && k \in K, \\
   & x_i \in \{0,1\}, && i \in I.
   \end{aligned}

If costs and available capital vary over time, we obtain a time-indexed DKP.
Let :math:`c_{it}` be the cost of investment :math:`i` at time :math:`t` and
:math:`b_t` the available capital at time :math:`t`. Then:

.. math::
   :label: DKP

   \begin{aligned}
   \max_x \quad
   & \sum_{i \in I} a_i x_i \\
   \text{s.t.} \quad
   & \sum_{i \in I} c_{it} x_i \le b_t, && t \in T, \\
   & x_i \in \{0,1\}, && i \in I.
   \end{aligned}

Example LOGOS input XML:

.. code-block:: xml

   <Logos>
     <Sets>
       <investments>
         1,2,3,4,5,6,7,8,9
       </investments>
       <time_periods>
         1,2,3,4,5
       </time_periods>
     </Sets>

     <Parameters>
       <net_present_values index="investments">
         2.315,0.824,22.459,60.589,0.667,5.173,4.003,0.582,0.122
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
         0.03,0.03,0.688,0.0,0.0
       </costs>
       <available_capitals index="time_periods">
         0.665,4.712,9.642,3.458,1.683
       </available_capitals>
     </Parameters>

     <Settings>
       <solver>glpk</solver>
       <sense>maximize</sense>
     </Settings>
   </Logos>

Example LOGOS output CSV:

.. code-block:: none

   1,2,3,4,5,6,7,8,9,MaxNPV
   1.0,1.0,0.0,0.0,1.0,0.0,0.0,1.0,0.0,4.388

Multiple Knapsack Problem Optimization
--------------------------------------
.. _subsec-mkp:

Another variant arises when we consider maintenance for multiple units in a
NPP in parallel; we must decide whether to accept a replacement and, if so, in
which unit to conduct it. We introduce binary decision variables for each
investment–unit pair:

.. math::

   x_{im} \in \{0,1\}, \quad i \in I,\; m \in M.

The resulting multiple knapsack problem (MKP) is:

.. math::
   :label: MKP

   \begin{aligned}
   \max_x \quad
   & \sum_{m \in M} \sum_{i \in I} a_i x_{im} \\
   \text{s.t.} \quad
   & \sum_{i \in I} c_i x_{im} \le b_m,
     && m \in M, \\
   & \sum_{m \in M} x_{im} \le 1,
     && i \in I, \\
   & x_{im} \in \{0,1\},
     && i \in I,\; m \in M.
   \end{aligned}

Example LOGOS input XML:

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

     <Settings>
       <solver>cbc</solver>
       <sense>maximize</sense>
       <problem_type>MultipleKnapsack</problem_type>
     </Settings>
   </Logos>

Example LOGOS output CSV:

.. code-block:: none

   1,2,3,4,5,6,7,8,9,10,capitals,MaxNPV
   0.0,0.0,1.0,1.0,1.0,0.0,0.0,0.0,0.0,0.0,unit_1,452.0
   1.0,0.0,0.0,0.0,0.0,1.0,0.0,0.0,1.0,0.0,unit_2,452.0

Multiple-Choice Multi-Dimensional Knapsack Problem Optimization
----------------------------------------------------------------
.. _subsec-mckp:

In some cases there may be multiple ways to carry out each project. For
investment :math:`i`, option :math:`j` has cost :math:`c_{ij}` and profit
:math:`a_{ij}`. Let :math:`J_i` be the set of options for investment :math:`i`.
Using decision variables :math:`x_{ij}` (1 if option :math:`j` is chosen for
investment :math:`i`), the multiple-choice knapsack problem (MCKP) is:

.. math::
   :label: MCKP-basic

   \begin{aligned}
   \max_x \quad
   & \sum_{i \in I} \sum_{j \in J_i} a_{ij} x_{ij} \\
   \text{s.t.} \quad
   & \sum_{j \in J_i} x_{ij} = 1,
     && i \in I, \\
   & \sum_{i \in I} \sum_{j \in J_i} c_{ij} x_{ij} \le b, \\
   & x_{ij} \in \{0,1\},
     && j \in J_i,\; i \in I.
   \end{aligned}

Considering limited resources and multi-year investments, the MCKP can be
extended to a D-dimensional MCKP (D-MCKP). Projects may span multiple years
(e.g., :math:`t, t+1, t+2` or shifted windows), or have different sizes and
benefits (e.g., 3% vs. 6% capacity uprates).

A D-MCKP formulation is:

.. math::
   :label: DMCKP-time

   \begin{aligned}
   \max_x \quad
   & \sum_{i \in I} \sum_{j \in J_i} a_{ij} x_{ij} \\
   \text{s.t.} \quad
   & \sum_{j \in J_i} x_{ij} = 1,
     && i \in I, \\
   & \sum_{i \in I} \sum_{j \in J_i} c_{ijt} x_{ij} \le b_t,
     && t \in T, \\
   & x_{ij} \in \{0,1\},
     && j \in J_i,\; i \in I.
   \end{aligned}

More generally, with multiple resource dimensions:

.. math::
   :label: DMCKP-general

   \begin{aligned}
   \max_x \quad
   & \sum_{i \in I} \sum_{j \in J_i} a_{ij} x_{ij} \\
   \text{s.t.} \quad
   & \sum_{j \in J_i} x_{ij} = 1,
     && i \in I, \\
   & \sum_{i \in I} \sum_{j \in J_i} c_{ijkt} x_{ij} \le b_{kt},
     && k \in K,\; t \in T, \\
   & x_{ij} \in \{0,1\},
     && j \in J_i,\; i \in I.
   \end{aligned}

Example LOGOS input XML:

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
         2.046 2.679 2.489 2.61 2.313 1.02 3.013 2.55 3.351 3.423 3.781 2.525
         2.169 2.267 2.747 4.309 6.452 2.849 7.945 2.538 1.761 3.002 3.449
         2.865 3.999 2.283 0.9 8.608
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

     <Settings>
       <solver>cbc</solver>
       <sense>maximize</sense>
       <problem_type>mckp</problem_type>
     </Settings>
   </Logos>

Example LOGOS output CSV:

.. code-block:: none

   1__1,2__1,3__1,4__1,4__2,4__3,...,17__1,MaxNPV
   1.0,1.0,1.0,1.0,0.0,0.0,...,1.0,59.82600000000001

In the output file, names of the form
``"investmentIndex"__"optionIndex"`` specify each decision variable. For
example, ``1__1`` indicates that investment **1** with option **1** is
selected.
