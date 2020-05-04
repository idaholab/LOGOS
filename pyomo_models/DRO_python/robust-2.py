# This is an implementation of an extension of the stochastic programming model from
# Priorotization via stochastic optimization, Management Science, Koc and Morton, 2016
# this implementation uses only uncertainty in the budget.

"""
Author: Dave and Ivi
Date: April 22, 2020
Updated by wangc
"""

from pyomo.core import *
from pyomo.environ import *
from pyomo.opt import SolverFactory
opt = SolverFactory("cbc")
model = AbstractModel()

# Input data
model.T = Set() # parameter for time
model.I = Set(ordered=True) # parameter for projects
model.J = Set() # parameter for options for selecting project i

model.Sm = Set(ordered=True) # scenarios
model.IJ = Set(dimen=2) # parameter for possible combinations between projects and options
model.IM = Set(ordered=True) # param for combinations between projects and MustDo and Optional

model.epsradius = Param(within=NonNegativeReals,default=0.0) # this is epsilon the radius in DRO

model.a = Param(model.IJ,default=0) # parameter for NPV of project i
model.b = Param(model.T,model.Sm,default=0) # parameter for budget in period t scenario omega
model.c = Param(model.IJ,model.T,default=0) # parameter for cost of project i under option j in period t
model.q = Param(model.Sm) # parameter for probabilities
model.dist = Param(model.Sm,model.Sm)

# Variables
model.x = Var(model.IJ,model.Sm, domain=NonNegativeIntegers, bounds=(0,1)) # variable x, 1 if project i is selected
model.y = Var(model.I,model.Sm, domain=NonNegativeIntegers, bounds=(0,1)) # variable y, 1 if project i has higher priority than i
model.s = Var(model.I,model.I,domain=NonNegativeIntegers, bounds=(0,1))
model.zz = Var(model.IJ,domain=NonNegativeIntegers, bounds=(0,1)) # these are the z variables from the model
model.nu = Var(model.Sm)
model.gamma = Var(within=NonNegativeReals)
#Objective function

def obj_rule(model):
  """
    Objective function
    @ In, model, pyomo instance, model instance of pyomo
    @ Out, obj_rule, pyomo expression, expression of objective function
  """
  return (-model.gamma*model.epsradius+sum(model.nu[om]*model.q[om] for om in model.Sm)) # new obj f-n 9a

model.z = Objective(rule=obj_rule, sense=maximize)

# Constraints
def NodesInInit(model, k):
  """
    Method that we use to create the set of options j for given project i
    @ In, model, pyomo instance, model instance of pyomo
    @ In, k, str, index for model set model.I
    @ Out, retval, list, list of options j for given project i
  """
  retval = []
  for (i,j) in model.IJ:
    if i == k:
      retval.append(j)
  return retval
model.NodesIn = Set(model.I, initialize=NodesInInit)

# new constraint 9b
def drocon(model, om, omm):
  """
    Method that we use to create the constraint for DRO problem
    @ In, model, pyomo instance, model instance of pyomo
    @ In, om, str, index for model set model.Sm
    @ In, omm, str, index for model set model.Sm
    @ Out, drocon, pyomo expression, expression of DRO constraint
  """
  return -model.gamma*model.dist[om,omm]+model.nu[om] <= sum(model.a[ij]*model.x[ij,omm] for ij in model.IJ)
model.drocon = Constraint(model.Sm,model.Sm,rule=drocon)

# combined 1b and 1h
def orderConstraintI(model, i, j):
  """
    Method that we use to create order constraint
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.I
    @ In, j, str, index for model set model.I
    @ Out, orderConstraintI, pyomo expression, expression of order constraint
  """
  if i < j:
    return model.s[i,j] + model.s[j,i] == 1
  else:
    return Constraint.Skip
model.orderConstraintI = Constraint(model.I, model.I,rule=orderConstraintI)

# equation 14c from MS paper
def constraintY(model, i):
  """
    Method that we use to create constraint on variable model.y
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.I
    @ Out, constraintY, pyomo expression, expression of constraint on variable model.y
  """
  return model.s[i,i] == 0
model.constraintY = Constraint(model.I,rule=constraintY)

# equation 1c
def constraint1c(model, i, iprime, om):
  """
    Method that we use to create the constraint 1c
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.I
    @ In, iprime, str, index for model set model.I
    @ In, om, str, index for model set model.Sm
    @ Out, constraint1c, pyomo expression, expression of constraint 1c
  """
  if i != iprime:
    return model.y[iprime,om] + model.s[i,iprime] -1 <= model.y[i,om]
  else:
    return Constraint.Skip
model.constraint_1c = Constraint(model.I, model.I, model.Sm,rule=constraint1c)

# equation 1d
def axConstraintRule(model, t, om):
  """
    Method that we use to create the aux constraint
    @ In, model, pyomo instance, model instance of pyomo
    @ In, t, str, index for model set model.T
    @ In, om, str, index for model set model.Sm
    @ Out, axConstraintRule, pyomo expression, expression of aux constraint
  """
  return sum(model.c[ij,t]*model.x[ij,om] for ij in model.IJ) <= model.b[t,om]
model.ax_constraint_rule = Constraint(model.T, model.Sm,rule=axConstraintRule)

#equation 1e
def constraintX(model, i, om): #sum of all x[i,j]=y[i,om] for all j
  """
    Method that we use to create the constraint on model.x
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.I
    @ In, om, str, index for model set model.Sm
    @ Out, axConstraintRule, pyomo expression, expression of constraint on model.x
  """
  return sum(model.x[i,j,om]  for j in model.NodesIn[i]) == model.y[i,om]
model.constraintX = Constraint(model.I,model.Sm, rule=constraintX)

#equation 1f
def mustConstraint(model, i, om):
  """
    Method that we use to create must-do constraint
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.IM
    @ In, om, str, index for model set model.Sm
    @ Out, mustConstraint, pyomo expression, expression of must-do constraint
  """
  return model.y[i,om]==1
model.must_constraint = Constraint(model.IM,model.Sm,rule=mustConstraint)

#new 1i
def constraint1i(model, i, iprime, idprime):
  """
    Method that we use to create constraint 1i
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.I
    @ In, iprime, str, index for model set model.I
    @ In, idprime, str, index for model set model.I
    @ Out, constraint1i, pyomo expression, expression of constraint 1i
  """
  if i!=iprime and iprime!=idprime and idprime !=i:
   return model.s[i,iprime]+model.s[iprime,idprime]+model.s[idprime,i]<=2
  else:
   return Constraint.Skip
model.constraint_1i=Constraint(model.I,model.I,model.I,rule=constraint1i)

#equation 1j
def consistentConstraint(model, i, i_prime, j, om):
  """
    Method that we use to create constraint 1j
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.I
    @ In, iprime, str, index for model set model.I
    @ In, idprime, str, index for model set model.I
    @ In, om, str, index for model set model.Sm
    @ Out, consistentConstraint, pyomo expression, expression of constraint 1j
  """
  if i!=i_prime and (i_prime,j) in model.IJ:
    return sum(model.x[i,j_prime,om]  for j_prime in model.NodesIn[i] if j_prime <=j) >= model.x[i_prime,j,om]+model.s[i,i_prime]-1
  else:
    return Constraint.Skip
model.consistentConstraint=Constraint(model.I,model.I,model.J,model.Sm,rule=consistentConstraint)

#new 1k
def constraint1k(model, i, j):
  """
    Method that we use to create constraint 1k
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.I
    @ In, j, str, index for model set model.J
    @ Out, constraint1k, pyomo expression, expression of constraint 1k
  """
  if (i,j) in model.IJ:
    return sum(model.x[i,j,om] for om in model.Sm) <= model.zz[i,j]*len(model.Sm)
  else:
    return Constraint.Skip
model.constraint_1k=Constraint(model.I,model.J,rule=constraint1k)

#new 1l
def constraint1l(model, i):
  """
    Method that we use to create constraint 1l
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.I
    @ Out, constraint1l, pyomo expression, expression of constraint 1l
  """
  return sum(model.zz[i,j] for j in model.NodesIn[i]) <=1
model.constraint_1l=Constraint(model.I,rule=constraint1l)

# Create a model instance and optimize
instance = model.create_instance('Robust_Input_1.dat')
instance.pprint()

results = opt.solve(instance, tee=True)

with open('Results-1.txt', 'w') as f:
    for v in instance.component_objects(Var, active=True):
#        print ("Variable",v)
        f.write ('{} {}\n'.format("; Variable; ", v))
        varobject = getattr(instance, str(v))
        for index in varobject:
#            print ("   ",index, varobject[index].value)
            f.write ('{} {}\n'.format(index, varobject[index].value))
