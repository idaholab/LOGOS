import sys
from pyomo.core import *
from pyomo.environ import *
from pyomo.opt import SolverFactory
# opt = SolverFactory("glpk")
opt = SolverFactory("cbc")
# model = ConcreteModel()
model = AbstractModel()

############## Input data
model.Tm = Param(within=NonNegativeIntegers) # upper bound on project's makespan
model.Jm = Param(within=NonNegativeIntegers) # Total jobs + 2 artificial jobs
model.Rm = Set() # set of resources
model.T = RangeSet(1, model.Tm) # time periods
model.J = RangeSet(1, model.Jm) # jobs
model.Kr = Param(model.Rm) # number of units of renewable resource r available in perior t
model.D = Param(model.J) # duration of job j
model.Kjr = Param(model.J, model.Rm) # number of units of renewable resources r consumed by job j while in process
model.JP = Set(dimen=2)
model.Pred = Param(model.JP)

def NodesIn_init(model, jp):
  retval = []
  for (j, p) in model.JP:
    if j == jp:
      retval.append(p)
  return retval
model.Predecessors = Set(model.J, initialize=NodesIn_init)

################# variables
model.x = Var(model.J,model.T, domain=NonNegativeIntegers, bounds=(0,1)) #


################## Objective
def obj_rule(model):
  return sum((model.T[t]-1)*model.x[model.J.last(),t] for t in model.T)
model.minMakespan = Objective(rule=obj_rule, sense=minimize)

################## Constraint

def constraintX(model, j):
  return sum(model.x[j, t]  for t in model.T) == 1
model.constraintX = Constraint(model.J, rule=constraintX)

def predecessorsConstraint(model, j, t, jp, p):
  if j == jp:
    return sum(model.x[j, tprime] for tprime in model.T if tprime <= t) <= sum(model.x[model.Pred[(jp,p)], tprime] for tprime in model.T if tprime <= t - model.D[j])
  else:
    return Constraint.Skip
model.predecessorsConstraint = Constraint(model.J, model.T, model.JP, rule=predecessorsConstraint)

def resourcesConstraint(model, t, r):
  return sum(sum(model.Kjr[j, r] * model.x[j, tprime] for tprime in model.T if tprime >= t and tprime <= t + model.D[j]-1) for j in model.J) <= model.Kr[r]
model.resourcesConstraint = Constraint(model.T, model.Rm, rule=resourcesConstraint)

# Create a model instance and optimize
instance = model.create_instance('inputData.dat')
results = opt.solve(instance, tee=True)

with open('Results.txt', 'w') as f:
    for v in instance.component_objects(Var, active=True):
#        print ("Variable",v)
        f.write ('{} {}\n'.format("; Variable; ", v))
        varobject = getattr(instance, str(v))
        for index in varobject:
#            print ("   ",index, varobject[index].value)
            f.write ('{} {}\n'.format(index, varobject[index].value))
