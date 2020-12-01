# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED

# This is an implementation of an extension of the stochastic programming model from
# Priorotization via stochastic optimization, Management Science, Koc and Morton, 2016
# this implementation uses only uncertainty in the budget

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

model._lambda = Param(within=NonNegativeReals,default=0.0) # this is risk aversion for CVaR
model._alpha = Param(within=NonNegativeReals,default=0.95) # this is confidence level for CVaR

model.a = Param(model.IJ,default=0) # parameter for NPV of project i
model.b = Param(model.T,model.Sm,default=0) # parameter for budget in period t scenario omega
model.c = Param(model.IJ,model.T,default=0) # parameter for cost of project i under option j in period t
model.q = Param(model.Sm) # parameter for probabilities
# Variables

model.x = Var(model.IJ,model.Sm, domain=NonNegativeIntegers, bounds=(0,1)) # variable x, 1 if project i is selected
model.y = Var(model.I,model.Sm, domain=NonNegativeIntegers, bounds=(0,1)) # variable y, 1 if project i has higher priority than i
model.s = Var(model.I,model.I,domain=NonNegativeIntegers, bounds=(0,1))
# model.zz = Var(model.IJ,domain=NonNegativeIntegers, bounds=(0,1)) # these are the z variables from the model
model.nu = Var(model.Sm, domain=NonNegativeReals)
model.u = Var(within=Reals)

#Objective function
def obj_rule(model):
  """
    Objective function
    @ In, model, pyomo instance, model instance of pyomo
    @ Out, obj_rule, pyomo expression, expression of objective function
  """
  expr = sum(model.q[om]*sum(model.a[ij]*model.x[ij,om] for ij in model.IJ) for om in model.Sm) * (1-model._lambda) - \
         model._lambda * (model.u + 1./(1.-model._alpha) * summation(model.q, model.nu))
  return expr

def maxNPV(model):
  """
    Max NPV function
    @ In, model, pyomo instance, model instance of pyomo
    @ Out, maxNPV, pyomo expression, expression of max NPV function
  """
 return sum(model.q[om]*sum(model.a[ij]*model.x[ij,om] for ij in model.IJ) for om in model.Sm)
model.maxNPV = Expression(rule=maxNPV)

def cvar(model):
  """
    CVaR function
    @ In, model, pyomo instance, model instance of pyomo
    @ Out, cvar, pyomo expression, expression of cvar function
  """
  return  model.u + 1./(1.-model._alpha)*summation(model.q, model.nu)
model.computeCVAR = Expression(rule=cvar)

model.z = Objective(rule=obj_rule, sense=maximize)

# Constraints
def NodesIn_init(model, project_i): # here we create the set of options j for given project i
  """
    Method that we use to create the set of options j for given project i
    @ In, model, pyomo instance, model instance of pyomo
    @ In, project_i, str, index for model set model.I
    @ Out, retval, list, list of options j for given project i
  """
  retval = []
  for (i,j) in model.IJ:
    if i == project_i:
      retval.append(j)
  return retval
model.NodesIn = Set(model.I, initialize=NodesIn_init)

# new cvar constraint
def cvarcon(model, om):
  """
    CVaR constraint function
    @ In, model, pyomo instance, model instance of pyomo
    @ In, om, str, index for model set model.Sm
    @ Out, cvarcon, pyomo expression, expression of cvar constraint function
  """
  return  model.nu[om] + sum(model.a[ij]*model.x[ij,om] for ij in model.IJ) + model.u >= 0
model.cvarcon = Constraint(model.Sm, rule=cvarcon)

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
def constraint_1c(model, i, iprime, om):
  """
    Method that we use to create the constraint 1c
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.I
    @ In, iprime, str, index for model set model.I
    @ In, om, str, index for model set model.Sm
    @ Out, constraint_1c, pyomo expression, expression of constraint 1c
  """
  if i != iprime:
    return model.y[iprime,om] + model.s[i,iprime] -1 <= model.y[i,om]
  else:
    return Constraint.Skip
model.constraint_1c = Constraint(model.I, model.I, model.Sm,rule=constraint_1c)

# equation 1d
def ax_constraint_rule(model, t, om):
  return sum(model.c[ij,t]*model.x[ij,om] for ij in model.IJ) <= model.b[t,om]
model.ax_constraint_rule = Constraint(model.T, model.Sm,rule=ax_constraint_rule)

#equation 1e
def constraintX(model, i,om): #sum of all x[i,j]=y[i,om] for all j
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
def must_constraint(model,i,om):
  """
    Method that we use to create must-do constraint
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.IM
    @ In, om, str, index for model set model.Sm
    @ Out, must_constraint, pyomo expression, expression of must-do constraint
  """
  return model.y[i,om]==1
model.must_constraint = Constraint(model.IM,model.Sm,rule=must_constraint)

#new 1i
def constraint_1i(model,i,iprime,idprime):
  """
    Method that we use to create constraint 1i
    @ In, model, pyomo instance, model instance of pyomo
    @ In, i, str, index for model set model.I
    @ In, iprime, str, index for model set model.I
    @ In, idprime, str, index for model set model.I
    @ Out, constraint_1i, pyomo expression, expression of constraint 1i
  """
  if i!=iprime and iprime!=idprime and idprime !=i:
   return model.s[i,iprime]+model.s[iprime,idprime]+model.s[idprime,i]<=2
  else:
   return Constraint.Skip

model.constraint_1i=Constraint(model.I,model.I,model.I,rule=constraint_1i)

#equation 1j
def consistentConstraint(model, i,i_prime,j, om):
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


# #new 1k
# def constraint_1k(model,i,j):
#   if (i,j) in model.IJ:
#       return sum(model.x[i,j,om] for om in model.Sm) <= model.zz[i,j]*len(model.Sm)
#   else:
#       return Constraint.Skip

# model.constraint_1k=Constraint(model.I,model.J,rule=constraint_1k)

# #new 1l
# def constraint_1l(model,i):
#  return sum(model.zz[i,j] for j in model.NodesIn[i]) <=1
# model.constraint_1l=Constraint(model.I,rule=constraint_1l)


# Create a model instance and optimize
instance = model.create_instance('inputs.dat')
instance.pprint()

results = opt.solve(instance, tee=True)

print('MaxNPV:', value(instance.maxNPV))
print('CVaR:', value(instance.computeCVAR))
print('Model Obj', instance.z())

with open('Results-lambda_100-alpha_95.txt', 'w') as f:
  f.write('MaxNPV: {}\n'.format(value(instance.maxNPV)))
  f.write('CVaR: {}\n'.format(value(instance.computeCVAR)))
  f.write('Model Obj: {} \n'.format(instance.z()))
  for v in instance.component_objects(Var, active=True):
    f.write ('{} {}\n'.format("; Variable; ", v))
    varobject = getattr(instance, str(v))
    for index in varobject:
      f.write ('{} {}\n'.format(index, varobject[index].value))
