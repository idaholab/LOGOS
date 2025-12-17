.. _sec-KnapsackModels:

Knapsack Models
===============

LOGOS contains a set of knapsack models that can be used in combination
with RAVEN when the desired optimization problem requires specific
models to generate the knapsack parameters. More specifically, all these
models can be included in a RAVEN :xmlNode:`EnsembleModel`, and RAVEN
optimization methods (e.g., genetic algorithms) can then be employed to
find the optimal solution.

.. _subsec-SimpleKnapsackModel:

Simple Knapsack Model
---------------------

This model corresponds to the classical knapsack problem, characterized
by a set of elements that can be chosen (or not). The goal is to
maximize the sum of the chosen element values, subject to the constraint
that the sum of their cost values does not exceed a capacity constraint
(specified via a variable defined in the node).

The IDs of the variables representing cost, value, and choice of each
element are indicated in the node. The model generates two variables:

- the validity of the chosen solution (specified in the node): either
  valid (0) or invalid (1) if the capacity constraint is not satisfied;
- ``totalValue`` (specified in the node): the sum of the values of the
  chosen elements.

When calculating the objective variable, if the constraint is not
satisfied, then the variable is penalized by multiplying the project
value by :math:`-1`.

Example LOGOS input XML for ``SimpleKnapsackModel``:

.. code-block:: xml

   <ExternalModel name="knapsack" subType="LOGOS.SimpleKnapsackModel">
     <variables>element1Status,element2Status,element3Status,
                element4Status,element5Status,element1Val,
                element2Val,element3Val,element4Val,
                element5Val,element1Cost,element2Cost,
                element3Cost,element4Cost,element5Cost,
                validity,totalValue,capacityID</variables>
     <capacity>capacityID</capacity>
     <penaltyFactor>1.</penaltyFactor>
     <outcome>validity</outcome>
     <choiceValue>totalValue</choiceValue>
     <map value="element1Val" cost="element1Cost">element1Status</map>
     <map value="element2Val" cost="element2Cost">element2Status</map>
     <map value="element3Val" cost="element3Cost">element3Status</map>
     <map value="element4Val" cost="element4Cost">element4Status</map>
     <map value="element5Val" cost="element5Cost">element5Status</map>
   </ExternalModel>

.. _subsec-MultipleKnapsackModel:

MultipleKnapsack Model
----------------------

This model considers the multiple knapsack problem, characterized by a
set of elements that can be chosen (or not) across multiple knapsacks.
The goal is to maximize the sum of the chosen element values, subject to
the capacity constraints of each knapsack.

The capacity of each knapsack is defined in the node.

As before, the IDs of the variables that represent cost, value, and
choice of each element are indicated in the node. The model generates
two variables:

- the validity of the chosen solution (specified in the node): either
  valid (0) or invalid (1) if any capacity constraint is not satisfied;
- ``totalValue`` (specified in the node): the sum of the values of the
  chosen elements.

When calculating the objective variable, if any capacity constraint is
not satisfied, then the variable is penalized by multiplying the project
value by :math:`-1`.

Example LOGOS input XML for ``MultipleKnapsackModel``:

.. code-block:: xml

   <Models>
     <ExternalModel name="knapsack" subType="LOGOS.MultipleKnapsackModel">
       <variables>e1Status,e2Status,e3Status,e4Status,e5Status,
                  e1Val   ,e2Val   ,e3Val   ,e4Val   ,e5Val,
                  e1Cost  ,e2Cost  ,e3Cost  ,e4Cost  ,e5Cost,
                  validity,totalValue,
                  K1_cap,K2_cap,K3_cap</variables>
       <knapsack ID="1">K1_cap</knapsack>
       <knapsack ID="2">K2_cap</knapsack>
       <knapsack ID="3">K3_cap</knapsack>
       <penaltyFactor>1.</penaltyFactor>
       <outcome>validity</outcome>
       <choiceValue>totalValue</choiceValue>
       <map value="e1Val" cost="e1Cost">e1Status</map>
       <map value="e2Val" cost="e2Cost">e2Status</map>
       <map value="e3Val" cost="e3Cost">e3Status</map>
       <map value="e4Val" cost="e4Cost">e4Status</map>
       <map value="e5Val" cost="e5Cost">e5Status</map>
     </ExternalModel>
   </Models>
