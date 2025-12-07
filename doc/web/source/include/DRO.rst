.. _sec-DROCapitalBudgeting:

Distributionally Robust Optimization
====================================

We consider another risk-averse decision making approach using
distributionally robust optimization (DRO). To this end, we begin with a
nominal stochastic optimization problem:

.. math::

   \max_{s \in S} \sum_{\sigma \in \Sigma} q^\sigma f(s,\xi^\sigma).

In our context, the nominal model is a stochastic capital budgeting problem in
which we maximize expected NPV, so we may take
:math:`f(s,\xi^\sigma) = NPV(s,\xi^\sigma)`. Here, :math:`s \in S` indicates
the constraints that a prioritized solution must satisfy when we prioritize
project selection subject to uncertainty in costs and NPV for each project, as
well as uncertainty in resource availability. The goal is to prioritize in
order to maximize expected NPV under the nominal distribution specified by the
probability mass function :math:`q^\sigma`, :math:`\sigma \in \Sigma`.

We suppose that :math:`\xi` is a discrete random vector with finite sample
space :math:`\Omega`, so that :math:`\xi^\omega`, :math:`\omega \in \Omega`,
enumerates all possible realizations. We further suppose that we only have
observations :math:`\xi^\sigma`, :math:`\sigma \in \Sigma \subset \Omega`,
possibly a strict subset, arising in a data-driven setting. In such a
data-driven setting we might have, for example,
:math:`q^\sigma = 1 / |\Sigma|` for all :math:`\sigma \in \Sigma`.

A DRO variant of this nominal stochastic optimization model is:

.. math::

   \max_{s \in S} \min_{p \in P}
   \sum_{\omega \in \Omega} p^\omega f(s,\xi^\omega).

Here, we may view this DRO model as playing a “game” against nature. First we
select :math:`s \in S`, and then, knowing :math:`s`, nature selects a
worst-case probability distribution :math:`p \in P` to minimize the expected
NPV, which we seek to maximize. The set :math:`P` is the *distributional
uncertainty set* (DUS), which we will define more precisely below. For now, it
is enough to think of :math:`P` as a neighborhood of probability distributions
centered on the given probability mass function :math:`q`, with the radius of
the neighborhood specified by a parameter :math:`\varepsilon`.

- If :math:`\varepsilon = 0`, the DRO model reduces to the nominal model.
- If :math:`\varepsilon` is very large, nature will select the single
  worst-case scenario (e.g., lowest budgets, highest costs, lowest NPVs).
  This is typically too conservative to be useful.

With moderate values of :math:`\varepsilon`, we obtain solutions that hedge
against deviations from :math:`q` without being excessively conservative.

We do *not* interpret nature as malevolent. Rather, the inner minimization
:math:`\min_{p\in P}` acts as a regularizer to avoid over-fitting our solution
to one specific estimated distribution, in much the same way that regularizers
are used in high-dimensional statistics and machine learning.

.. _subsec-WassersteinDistance:

Defining a Distributional Uncertainty Set via the Wasserstein Distance
----------------------------------------------------------------------

We constrain nature to choose a probability mass function :math:`p` that is
“close” to the nominal (or empirical) distribution :math:`q`. We define the
DUS as:

.. math::

   P = \Bigl\{
         p :
         D(p,q) \le \varepsilon,\;
         \sum_{\omega \in \Omega} p^\omega = 1,\;
         p^\omega \ge 0,\; \omega \in \Omega
       \Bigr\},

where :math:`D(p,q)` is a distance between nature’s choice :math:`p` and the
nominal distribution :math:`q`. The radius :math:`\varepsilon` governs how
much we allow :math:`p` to deviate from :math:`q`, and thus the degree of
conservatism.

There are many ways to measure :math:`D(p,q)`, including the
Kolmogorov–Smirnov distance, Kullback–Leibler divergence, chi-squared
distances, total variation, and more general :math:`\psi`-divergences. The
Wasserstein distance, based on optimal transport, is particularly useful in
DRO. For a distribution with known finite support, the Wasserstein distance
:math:`D(p,q)` between a nominal distribution :math:`q^\sigma`,
:math:`\sigma \in \Sigma`, and a candidate robust distribution :math:`p^\omega`,
:math:`\omega \in \Omega`, is given by the optimal value of the following
transportation problem:

.. math::
   :label: WassersteinEq

   \begin{aligned}
   D(q,p) = \min_z \quad
   & \sum_{\sigma \in \Sigma} \sum_{\omega \in \Omega}
       d_{\sigma,\omega} z_{\sigma,\omega} \\
   \text{s.t.} \quad
   & \sum_{\omega \in \Omega} z_{\sigma,\omega} = q^\sigma,
     && \sigma \in \Sigma, \\
   & \sum_{\sigma \in \Sigma} z_{\sigma,\omega} = p^\omega,
     && \omega \in \Omega, \\
   & z_{\sigma,\omega} \ge 0,
     && \sigma \in \Sigma,\; \omega \in \Omega.
   \end{aligned}

Intuitively, :math:`z_{\sigma,\omega}` denotes how much probability mass
:math:`q^\sigma` must be transported a distance :math:`d_{\sigma,\omega}` from
:math:`\xi^\sigma` to :math:`\xi^\omega`. If :math:`\Omega = \Sigma`,
:math:`p^\omega = q^\omega` for all :math:`\omega`, and
:math:`d_{\omega,\omega} = 0`, then :math:`D(p,q) = 0`.

To fully specify :math:`D(p,q)`, we must define
:math:`d_{\sigma,\omega} = \text{dist}(\xi^\sigma,\xi^\omega)`. We may choose
:math:`\text{dist}(\cdot,\cdot)` to be, for example, an :math:`\ell_\eta`-norm:

.. math::

   d_{\sigma,\omega}
   = \|\xi^\sigma - \xi^\omega\|_\eta.

With the Wasserstein distance, and a given distribution :math:`q`, we define:

.. math::

   P = \Bigl\{
         p :
         D(p,q) \le \varepsilon,\;
         \sum_{\omega \in \Omega} p^\omega = 1,\;
         p^\omega \ge 0,\; \omega \in \Omega
       \Bigr\}

for a chosen radius :math:`\varepsilon`. We think of :math:`P` as a ball (or
neighborhood) of probability distributions centered at :math:`q`. With
:math:`\Sigma \subset \Omega`:

- If :math:`\varepsilon = 0`, then :math:`P` collapses to the singleton
  :math:`\{q\}`.
- Larger :math:`\varepsilon` creates larger neighborhoods and more
  conservative robust models.

We can represent :math:`P` using the following *extended-variable*
constraints:

.. math::
   :label: WassersteinConstraints

   \begin{aligned}
   & \sum_{\sigma \in \Sigma} \sum_{\omega \in \Omega}
       d_{\sigma,\omega} z_{\sigma,\omega} \le \varepsilon, \\
   & \sum_{\omega \in \Omega} z_{\sigma,\omega} = q^\sigma,
     && \sigma \in \Sigma, \\
   & \sum_{\sigma \in \Sigma} z_{\sigma,\omega} = p^\omega,
     && \omega \in \Omega, \\
   & z_{\sigma,\omega} \ge 0,
     && \sigma \in \Sigma,\; \omega \in \Omega.
   \end{aligned}

.. _subsec-TractableReformulation:

Towards a Computationally Tractable Reformulation
-------------------------------------------------

Due to the nested :math:`\max \min` in the DRO model, it is not directly
amenable to off-the-shelf solvers. We therefore reformulate it.

For the moment, fix :math:`s \in S`, so that :math:`f(s,\xi^\omega)` is a
known numerical value for each :math:`\omega \in \Omega`. Nature’s problem can
then be written as:

.. math::

   \begin{aligned}
   \min_{p,z} \quad
   & \sum_{\omega \in \Omega} p^\omega f(s,\xi^\omega) \\
   \text{s.t.} \quad
   & \sum_{\sigma \in \Sigma} \sum_{\omega \in \Omega}
       d_{\sigma,\omega} z_{\sigma,\omega}
       \le \varepsilon
       && : [-\gamma], \\
   & \sum_{\omega \in \Omega} z_{\sigma,\omega}
       = q^\sigma,
       && \sigma \in \Sigma : [\nu^\sigma], \\
   & \sum_{\sigma \in \Sigma} z_{\sigma,\omega}
       = p^\omega,
       && \omega \in \Omega : [\beta^\omega], \\
   & z_{\sigma,\omega} \ge 0,
       && \sigma \in \Sigma,\; \omega \in \Omega.
   \end{aligned}

Here :math:`\gamma`, :math:`\nu^\sigma`, and :math:`\beta^\omega` are dual
variables. Nature optimizes over :math:`z` and :math:`p` to select a worst-case
distribution within radius :math:`\varepsilon` of :math:`q`.

Taking the dual of this linear DRO program, and substituting
:math:`\beta^\omega = -f(s,\xi^\omega)`, we obtain:

.. math::
   :label: dualProlem

   \begin{aligned}
   \max_{\gamma,\nu} \quad
   & -\gamma \varepsilon + \sum_{\sigma \in \Sigma} \nu^\sigma q^\sigma \\
   \text{s.t.} \quad
   & -\gamma d_{\sigma,\omega} + \nu^\sigma
       \le f(s,\xi^\omega),
       && \sigma \in \Sigma,\; \omega \in \Omega, \\
   & \gamma \ge 0.
   \end{aligned}

To build intuition, consider two extremes:

- **Case :math:`\varepsilon = 0`.** There is no penalty for large
  :math:`\gamma` in the objective. As :math:`\gamma` grows large, the
  constraints for :math:`\sigma \ne \omega` become vacuous. For
  :math:`\sigma = \omega`, :math:`d_{\sigma,\sigma} = 0`, so the constraint
  becomes :math:`\nu^\sigma \le f(s,\xi^\sigma)`. Optimizing yields
  :math:`\sum_\sigma q^\sigma f(s,\xi^\sigma)`, which is exactly the nominal
  expected value.
- **Case :math:`\varepsilon \to \infty`.** To avoid a large penalty
  :math:`-\gamma \varepsilon` in the objective, we must have :math:`\gamma = 0`.
  The constraint reduces to :math:`\nu^\sigma \le f(s,\xi^\omega)` for all
  :math:`\omega`, so :math:`\nu^\sigma = \min_\omega f(s,\xi^\omega)`. The
  objective becomes:

  .. math::

     \sum_\sigma q^\sigma \min_\omega f(s,\xi^\omega)
     = \min_\omega f(s,\xi^\omega),

  which corresponds to nature placing probability 1 on the worst-case scenario.

Specializing :math:`s \in S` to the capital budgeting prioritization decisions,
and :math:`f(s,\xi^\omega)` to the NPV under scenario :math:`\omega`, the DRO
variant of the stochastic capital budgeting problem is:

.. math::
   :label: fullDRO

   \begin{aligned}
   \max_{x,y,\gamma,\nu} \quad
   & -\gamma \varepsilon + \sum_{\sigma \in \Sigma} \nu^\sigma q^\sigma \\
   \text{s.t.} \quad
   & -\gamma d_{\sigma,\omega} + \nu^\sigma
       \le \sum_{i \in I} \sum_{j \in J_i} a_{ij}^\omega x_{ij}^\omega,
       && \sigma \in \Sigma,\; \omega \in \Omega, \\
   & y_{ii'} + y_{i'i} \ge 1,
       && i < i',\; i,i' \in I, \\
   & \sum_{j=1}^{J_i} x_{ij}^\omega
       \ge \sum_{j=1}^{J_i} x_{i'j}^\omega + y_{ii'} - 1,
       && i \ne i',\; i,i' \in I,\; \omega \in \Omega, \\
   & \sum_{i \in I} \sum_{j \in J_i}
       c_{ijkt}^\omega x_{ij}^\omega
       \le b_{kt}^\omega,
       && k \in K,\; t \in T,\; \omega \in \Omega, \\
   & \sum_{j \in J_i} x_{ij}^\omega \le 1,
       && i \in I,\; \omega \in \Omega, \\
   & \gamma \ge 0.
   \end{aligned}

.. _subsec-DROSettings:

LOGOS Settings for DRO Problems
-------------------------------

The DRO approach extends the stochastic optimization approach discussed in
Section :ref:`sec-StochasticCapitalBudgeting`. Both share the same input
structure except for the :xmlNode:`Settings` block.

In both cases, the user specifies a collection of scenarios via an
:xmlNode:`Uncertainties` block. The :xmlNode:`problem_type` within
:xmlNode:`Settings` selects the type of DRO problem. Currently available DRO
problem types are:

- :xmlString:`droskp`
- :xmlString:`dromkp`
- :xmlString:`dromckp`

The user can use :xmlNode:`radius_ambiguity` to control the Wasserstein radius
:math:`\varepsilon` for DRO problems.

Example LOGOS input XML for DRO:

.. code-block:: xml

   <Settings>
   <Logos>
     <solver>cbc</solver>
     <solverOptions>
       <StochSolver>EF</StochSolver>
       <!-- epsilon radius -->
       <radius_ambiguity>0.1</radius_ambiguity>
     </solverOptions>
     <sense>maximize</sense>
     <problem_type>droskp</problem_type>
   </Settings>
   </Logos>

.. _subsec-DRO_SKP:

DRO for Single Knapsack Problem
-------------------------------

*Model formulation.*

.. math::
   :label: DROSimpleKP

   \begin{aligned}
   \max_{x,y} \max_{\gamma,\nu} \quad
   & -\gamma \varepsilon + \sum_{\sigma \in \Sigma} \nu^\sigma q^\sigma \\
   \text{s.t.} \quad
   & -\gamma d_{\sigma,\omega} + \nu^\sigma
     \le \sum_{i \in I} a_i^\omega x_i^\omega,
     && \sigma \in \Sigma,\; \omega \in \Omega, \\
   & \sum_{i \in I} c_i^\omega x_i^\omega
       \le b^\omega,
       && \omega \in \Omega, \\
   & y_{ii'} + y_{i'i} \ge 1,
       && i < i', \\
   & x_i^\omega \ge x_{i'}^\omega + y_{ii'} - 1,
       && i \ne i', \\
   & x_i^\omega, y_{ii'}^\omega \in \{0,1\}, \\
   & \gamma \ge 0.
   \end{aligned}

See Section :ref:`subsec-DRO_DKP` for an example LOGOS input file. The
multi-dimensional knapsack problem is a straightforward extension of the
single-dimensional case, and both share :xmlNode:`problem_type`
:xmlString:`droskp`.

.. _subsec-DRO_DKP:

DRO for Multi-Dimensional Knapsack Problem
------------------------------------------

*Model formulation.*

.. math::
   :label: DROMultiDKP

   \begin{aligned}
   \max_{x,y} \max_{\gamma,\nu} \quad
   & -\gamma \varepsilon + \sum_{\sigma \in \Sigma} \nu^\sigma q^\sigma \\
   \text{s.t.} \quad
   & -\gamma d_{\sigma,\omega} + \nu^\sigma
       \le \sum_{i \in I} a_i^\omega x_i^\omega,
       && \sigma \in \Sigma,\; \omega \in \Omega, \\
   & \sum_{i \in I} c_{it}^\omega x_i^\omega
       \le b_t^\omega,
       && t \in T,\; \omega \in \Omega, \\
   & y_{ii'} + y_{i'i} \ge 1,
       && i < i', \\
   & x_i^\omega \ge x_{i'}^\omega + y_{ii'} - 1,
       && i \ne i', \\
   & x_i^\omega, y_{ii'}^\omega \in \{0,1\}, \\
   & \gamma \ge 0.
   \end{aligned}

Example LOGOS input XML:

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
           the number of scenarios is determined by the length of
           <probabilities>; here: 10 * 5
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
       <solverOptions>
         <StochSolver>EF</StochSolver>
         <!-- epsilon radius -->
         <radius_ambiguity>0.1</radius_ambiguity>
       </solverOptions>
       <sense>maximize</sense>
       <problem_type>droskp</problem_type>
     </Settings>
   </Logos>

.. _subsec-DRO_MKP:

DRO for Multiple Knapsack Problem
---------------------------------

*Model formulation.*

.. math::
   :label: DROMKP

   \begin{aligned}
   \max_{x,y} \max_{\gamma,\nu} \quad
   & -\gamma \varepsilon + \sum_{\sigma \in \Sigma} \nu^\sigma q^\sigma \\
   \text{s.t.} \quad
   & -\gamma d_{\sigma,\omega} + \nu^\sigma
       \le \sum_{i \in I} \sum_{m \in M} a_i^\omega x_{im}^\omega,
       && \sigma \in \Sigma,\; \omega \in \Omega, \\
   & \sum_{i \in I} c_i^\omega x_{im}^\omega
       \le b_m^\omega,
       && m \in M,\; \omega \in \Omega, \\
   & y_{ii'} + y_{i'i} \ge 1,
       && i < i', \\
   & \sum_{m=1}^M x_{im}^\omega
       \ge \sum_{m=1}^M x_{i'm}^\omega + y_{ii'} - 1,
       && i \ne i',\; i,i' \in I,\; \omega \in \Omega, \\
   & x_{im}^\omega, y_{ii'}^\omega \in \{0,1\}, \\
   & \gamma \ge 0.
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

     <Uncertainties>
       <available_capitals>
         <totalScenarios>10</totalScenarios>
         <probabilities>
           0.1 0.1 0.1 0.1 0.1 0.1 0.1 0.1 0.1 0.1
         </probabilities>
         <scenarios>
           101, 154,
           102, 155,
           103, 156,
           104, 157,
           105, 158,
           106, 159,
           107, 160,
           108, 161,
           109, 162,
           110, 163
         </scenarios>
       </available_capitals>
     </Uncertainties>

     <Settings>
       <solver>cbc</solver>
       <solverOptions>
         <StochSolver>EF</StochSolver>
         <!-- epsilon radius -->
         <radius_ambiguity>0.1</radius_ambiguity>
       </solverOptions>
       <sense>maximize</sense>
       <problem_type>dromkp</problem_type>
     </Settings>
   </Logos>

.. _subsec-DRO_MCKP:

DRO for Multiple-Choice Knapsack Problem
----------------------------------------

*Model formulation.*

.. math::
   :label: DROMCKP

   \begin{aligned}
   \max_{x,y} \max_{\gamma,\nu} \quad
   & -\gamma \varepsilon + \sum_{\sigma \in \Sigma} \nu^\sigma q^\sigma \\
   \text{s.t.} \quad
   & -\gamma d_{\sigma,\omega} + \nu^\sigma
       \le \sum_{i \in I} \sum_{j \in J_i} a_{ij}^\omega x_{ij}^\omega,
       && \sigma \in \Sigma,\; \omega \in \Omega, \\
   & \sum_{i \in I} \sum_{j \in J_i}
       c_{ijkt}^\omega x_{ij}^\omega
       \le b_{kt}^\omega,
       && k \in K,\; t \in T,\; \omega \in \Omega, \\
   & y_{ii'} + y_{i'i} \ge 1,
       && i < i', \\
   & \sum_{j=1}^{J_i} x_{ij}^\omega
       \ge \sum_{j=1}^{J_i} x_{i'j}^\omega + y_{ii'} - 1,
       && i \ne i',\; i,i' \in I,\; \omega \in \Omega, \\
   & x_{ij}^\omega, y_{ii'}^\omega \in \{0,1\}, \\
   & \gamma \ge 0.
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
       <solver>glpk</solver>
       <solverOptions>
         <StochSolver>EF</StochSolver>
         <!-- epsilon radius -->
         <radius_ambiguity>0.1</radius_ambiguity>
       </solverOptions>
       <sense>maximize</sense>
       <problem_type>dromckp</problem_type>
     </Settings>
   </Logos>
