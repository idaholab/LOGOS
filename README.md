![LOGOS Logo](.doc/logos/LOGOS.png)

# Logos: Operation Optimization Toolkit

LOGOS is a software package which contains a set of discrete optimization models
that can be employed for capital budgeting optimization problems. More specifically,
provided a set of items (characterized by cost and reward values) and constraints,
these models select the best combination of items which maximizes overall reward
and satisfies the provided constraints. The developed models are based on different
versions of the knapsack optimization algorithms. Two main classes of optimization
models have been initially developed: deterministic and stochastic. Stochastic
optimization models evolve deterministic models by explicitly considering data
uncertainties (associated to constraints or item cost and reward). These models
can be employed as stand-alone models or interfaced with the INL developed RAVEN
code to propagate data uncertainties and analyze the generated data
(i.e., sensitivity analysis).

## Optimization for Capital Investments

### Optimization Approaches
- Deterministic Optimization
- Stochastic Optimization
- Distributionally Robust Optimization
- Conditional Value-at-Risk Optimization

### Problems that can be solved by this package:
- Deterministic Capital Budgeting
- Risk-informed stochastic Capital Budgeting
- Multiple Knapsack problem optimization
- Multi-dimensional Knapsack problem optimization
- Multi-choice Knapsack problem optimization
- Multi-choice multi-dimensional Knapsack problem optimization
- SSC cashflow and NPV models

## Installation:
path/to/LOGOS/build.sh --install

## How to run:
- source activate LOGOS_libraries
- path/to/LOGOS/.logos -i inputfile.xml -o outputfile.csv

## Tests
python run_tests.py

## Docs
path/to/LOGOS/doc

### Other Software
Idaho National Laboratory is a cutting edge research facility which is a constantly producing high quality research and software. Feel free to take a look at our other software and scientific offerings at:

[Primary Technology Offerings Page](https://www.inl.gov/inl-initiatives/technology-deployment)

[Supported Open Source Software](https://github.com/idaholab)

[Raw Experiment Open Source Software](https://github.com/IdahoLabResearch)

[Unsupported Open Source Software](https://github.com/IdahoLabCuttingBoard)


### Licensing
-----
This software is licensed under the terms you may find in the file named "LICENSE" in this directory.

### Developers
-----
By contributing to this software project, you are agreeing to the following terms and conditions for your contributions:

You agree your contributions are submitted under the Apache license. You represent you are authorized to make the contributions and grant the license. If your employer has rights to intellectual property that includes your contributions, you represent that you have received permission to make contributions and grant the required license on behalf of that employer.
