import sys
from pyomo.core import *
from pyomo.environ import *
from pyomo.opt import SolverFactory
# opt = SolverFactory("glpk")
opt = SolverFactory("glpk")
# model = ConcreteModel()
model = AbstractModel()

############## Input data
model.Tm = Param(within=NonNegativeIntegers) # upper bound on project's makespan
model.Jm = Param(within=NonNegativeIntegers) # Total jobs + 2 artificial jobs
model.Rm = Set() # set of resources
model.T = RangeSet(1, model.Tm) # time periods
model.J = RangeSet(1, model.Jm) # jobs
model.Kr = Param(model.Rm) # number of units of renewable resource r available in period t
model.D = Param(model.J) # duration of job j
model.Kjr = Param(model.J, model.Rm) # number of units of renewable resources r consumed by job j while in process
model.JP = Set(dimen=2) # set of precedcessors
model.Pred = Param(model.JP) # immediate predecessors

################# variables
model.x = Var(model.J,model.T, domain=NonNegativeIntegers, bounds=(0,1)) #

################## Objective
def obj_rule(model):
  return sum((model.T[t]-1)*model.x[model.J.last(),t] for t in model.T)
model.minMakespan = Objective(rule=obj_rule, sense=minimize)

################## Constraint on X

def constraintX(model, j):
  return sum(model.x[j, t]  for t in model.T) - 1 == 0
model.constraintX = Constraint(model.J, rule=constraintX)

################### Constraint on predecessors
def predecessorsConstraint(model, jp, p, t):
  return sum(model.x[model.Pred[(jp,p)], tprime] for tprime in model.T if tprime <= t) <= sum(model.x[jp, tprime] for tprime in model.T if tprime <= t - model.D[jp])
model.predecessorsConstraint = Constraint(model.JP, model.T, rule=predecessorsConstraint)

##################### Constraint on resources
def resourcesConstraint(model, r, t):
  return (0, sum(sum(model.Kjr[j, r] * model.x[j, tprime] for tprime in model.T if tprime >= t and tprime <= t + model.D[j]-1) for j in model.J), model.Kr[r])
model.resourcesConstraint = Constraint(model.Rm, model.T, rule=resourcesConstraint)

# Create a model instance and optimize
instance = model.create_instance('inputData_J10.dat')
instance.pprint()
results = opt.solve(instance, tee=True)

with open('Results.txt', 'w') as f:
    for v in instance.component_objects(Var, active=True):
#        print ("Variable",v)
        f.write ('{} {}\n'.format("; Variable; ", v))
        varobject = getattr(instance, str(v))
        for index in varobject:
#            print ("   ",index, varobject[index].value)
            f.write ('{} {}\n'.format(index, varobject[index].value))
