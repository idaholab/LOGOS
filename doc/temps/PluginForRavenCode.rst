Plugin for the RAVEN Code
=========================
.. _sec-RavenPlugin:

The deterministic model can be repeatedly solved by changing the input
values (i.e., :xmlNode:`available_capitals` (corresponding to
:math:`b_{kt}`) and :xmlNode:`net_present_values` (corresponding to
:math:`a_{ij}`)). This allows for what-if sensitivity analysis to
identify the crucial drivers behind the optimal project selection
decision. Monte Carlo simulation permits a powerful variant of this
approach in which we model :math:`a_{ij}` and :math:`b_{kt}` as random
variables, sample from their distributions, and perform a form of
uncertainty quantification in terms of the resulting distributions
governing the binary decisions :math:`x_{ij}` and the overall NPV of
the selected portfolio.

Example RAVEN input :xmlNode:`ExternalModel` XML:

.. code-block:: xml

   <Models>
     <ExternalModel name="singleKnapsack" subType="LOGOS.CapitalInvestmentModel">
       <variables>available_capitals,i1,i2,i3,i4,i5,i6,i7,i8,i9,i10,
         MaxNPV</variables>
       <ModelData>
         <Sets>
           <investments>
             i1,i2,i3,i4,i5,i6,i7,i8,i9,i10
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
           <solver>glpk</solver>
           <sense>maximize</sense>
         </Settings>
       </ModelData>
     </ExternalModel>
   </Models>

As the name suggests, an external model is an entity that is embedded in
the RAVEN code at run time. This object allows the user to import the
LOGOS module, which will then be treated as a predefined internal RAVEN
object. In other words, the **External Model** will be treated by RAVEN
as a normal external model. See the RAVEN user manual for a more
detailed description.

.. note::

   The value for attribute :xmlAttr:`subType` should always be
   :xmlString:`LOGOS.CapitalInvestmentModel`.

The :xmlNode:`variables` node specifies a list of variable names that
must match variables used/defined in the LOGOS model. In the example
above, the variable :xmlString:`available_capitals` is sampled by
RAVEN, and its value is passed to LOGOS instead of using the default
values specified in :xmlNode:`ModelData`. The decision variables
:xmlString:`i1,i2,i3,i4,i5,i6,i7,i8,i9,i10` and the objective variable
:xmlString:`MaxNPV` are collected from the output of the LOGOS model and
can be stored in the RAVEN data objects. The XML node
:xmlNode:`ModelData` is used to specify the LOGOS optimization problem,
and should be consistent with the stand-alone LOGOS input XML file.

Test Automation
---------------

Automated regression testing is a development methodology generally used
to verify the correctness and performance of software after each
modification. This methodology is integrated directly into GitHub and
GitLab for RAVEN and RAVEN-supported plugins. In this case, testing is
performed automatically as part of the continuous integration system
(CIS) process whenever a user commits a change to the repository. Tests
of changes across multiple platforms are executed with each pull
request. Results from each test execution are maintained in an approved
records repository in the CIS database, along with results from the
timing executions.

Testing System Prerequisites
----------------------------

The module test system consists of scripts written in Bash and Python. It
may be used on any platform supported by RAVEN (i.e., Linux, macOS, and
Windows). The following conditions must be satisfied for the module test
system to function properly:

- RAVEN should be installed and updated, since RAVEN itself is used to
  run the module tests.
- The system running the tests must be configured with the software
  prerequisites necessary to build and run RAVEN. These include:

  * a Python interpreter,
  * Python libraries (``h5py``, ``matplotlib``, ``numpy``, ``scipy``,
    and ``scikit-learn``),
  * development tools (C++ compiler, Miniconda package manager for
    Python, and Git source code control).

- RAVEN must be built with the appropriate compiler before it can be
  used to run the tests.
- The LOGOS submodule must be initialized and fully updated. Additional
  prerequisites include PYOMO, ``glpk``, and ``coincbc``.

Test Location and Definition
----------------------------

LOGOS is a RAVEN-supported plugin and is managed by Git. RAVEN plugins
provide an option to associate a workflow or a set of RAVEN external
models with RAVEN without including them directly in the main RAVEN
repository. The benefits include modularity, access restriction, and
regression testing for compatibility with RAVEN as it continues to
evolve. In this way, we are able to treat this repository as separate,
yet still use one from within the other.

The main structure of the LOGOS repository is shown in
Figure :ref:`fig-Logos`. The folder *tests* contains the inputs for the
tests, in addition to the corresponding output (in the ``gold`` folder)
used to ensure that the behavior of the code does not change when new
modifications are introduced. These tests can also be organized in
subfolders based on their characteristics.

.. figure:: LogosRepo.jpg
   :scale: 50%
   :align: center

   Folder tree of the LOGOS repository.
   .. _fig-Logos:

The RAVEN repository contains a complete testing system for providing
regression testing for itself and for its plugins. The LOGOS module
tests are defined in the same manner as those for RAVEN itself. A single
test consists of a RAVEN input file along with the associated data
needed to perform that run. This can include input data, external
models, and Python files. These may be placed in the *tests* directory
or in related subdirectories.

Each directory that contains tests to be run by the framework must
contain a test specification file named ``tests``. The syntax of these
files is defined by the RAVEN test framework, which controls how each
test is run and sets the criteria used to determine whether it passed or
not. An example of a test specification file is:

.. code-block:: python

   [Tests]
     [./skp_optimization]
       type = 'RavenFramework'
       input = 'test_skp.xml'
       UnorderedCsv = 'skp/test_skp.csv'
     [../]
   []

In the example above, one test, named ``skp_optimization``, is defined
using the RAVEN test module (``type = 'RavenFramework'``). Comparison
criteria are also defined in the ``tests`` file. In most cases, one or
more output files generated by running the specified input file with
RAVEN are compared against a gold standard provided by the developer and
stored in the repository. Typically, comparisons are performed on
numeric values contained in CSV files up to a defined tolerance.

When these file comparisons are specified by the test developer,
reference files must have the same name and be placed in the ``gold``
subdirectory below the directory containing the ``tests`` file.

Running the Tests
-----------------

The integrated tests can also be run manually, for example by executing:

.. code-block:: bash

   path/to/raven/raven_framework path/to/LOGOS/tests/TestName

Continuous Integration System (CIVET)
-------------------------------------

CIVET, developed at INL, is used for continuous integration,
verification, enhancement, and testing of RAVEN and LOGOS. Each time a
developer proposes a modification to the contents of the LOGOS
repository, CIVET triggers the automated tests to be run on the modified
version. These tests must all pass before a proposed change can become
part of the official repository. In this way, the LOGOS project is
protected from the accidental introduction of flaws into software that
required a significant investment of resources to develop.
