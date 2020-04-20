#File name: Model_MS_eq14_direct.py


# This is an implementation of the stochastic programming model from
# Priorotization via stochastic optimization, Management Science, Koc and Morton, 2016
# this implementation uses only uncertainty in the budget 
# implemented model is given in equations 14a - 14f
# this implementation is direct and does not use the 2 stage stochastic optimization available in pyomo
# can be used with the 9 project data or 41 project data from EE paper

from pyomo.environ import *
from pyomo.core import * 

model = AbstractModel()


# Input data

model.Tm = Param(within=NonNegativeIntegers) # time periods, T in years, strictly posiitve integers
model.Im = Param(within=NonNegativeIntegers) # candidate projects, I, strictly posiitve integers
model.Sm = Param(within=NonNegativeIntegers) # scenarios from 1 to Omega


model.T = RangeSet(1, model.Tm) # parameter for time ranging from 1 to T
model.I = RangeSet(1, model.Im) # parameter for projects ranging from 1 to |I|
model.O = RangeSet(1,model.Sm) # parameter for scenarios
model.q = Param(model.O) # parameter for probabilities
model.a = Param(model.I) # parameter for NPV of project i 
model.b = Param(model.T,model.O) # parameter for budget in period t scenario omega
model.c = Param(model.I, model.T) # parameter for cost of project i in period t 


# Variables

model.x = Var(model.I,model.O, domain=NonNegativeIntegers, bounds=(0,1)) # variable x, 1 if project i is selected
model.y = Var(model.I, model.I, domain=NonNegativeIntegers, bounds=(0,1)) # variable y, 1 if project i has higher priority than i

#Objective function

def obj_rule(model):
    return sum( model.q[om]*sum(model.a[i]*model.x[i,om] for i in model.I) for om in model.O)

model.z = Objective(rule=obj_rule, sense=maximize)


# Constraints

# equation 14b
def orderConstraintI(model, i, j):
  if i == j:
    return model.y[i,j] == model.y[j,i]
  else:
    return model.y[i,j] + model.y[j,i] >= 1

model.orderConstraintI = Constraint(model.I, model.I, rule=orderConstraintI)


# equation 14c
def constraintY(model, i):
  return model.y[i,i] == 0

model.constraintY = Constraint(model.I)


# equation 14d
def consistentConstraint(model, i, j, om):
  if i == j:
    return model.y[i,j] == model.y[j,i]
  else:
    return model.x[j,om] + model.y[i,j] -1 <= model.x[i,om]

model.consistentConstraint = Constraint(model.I, model.I, model.O, rule=consistentConstraint)


# equation 14e
def ax_constraint_rule(model, t, om):
  return sum(model.c[i,t]*model.x[i,om] for i in model.I) <= model.b[t,om]
  

model.AxbContraint = Constraint(model.T, model.O, rule=ax_constraint_rule) 
