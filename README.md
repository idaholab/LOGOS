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

### Problems that can be solved by this package:
- Deterministic Capital Budgeting
- Risk-informed stochastic Capital Budgeting
- Multiple Knapsack problem optimization
- Multi-dimensional Knapsack problem optimization
- Multi-choice Knapsack problem optimization
- Multi-choice multi-dimensional Knapsack problem optimization
- SSC cashflow and NPV models

## Installation:
./build.sh

## How to run:
- source activate logos_libraries
- path/to/Logos/.logos -i inputfile.xml -o outputfile.csv

## Tests
./src/tests

## Docs
./docs

### Developers:
