#File name: Model_Deterministic_options_resources.py
from pyomo.environ import *
from pyomo.core import * 
from pyomo.opt import SolverFactory

# Create a solver
opt = SolverFactory('cbc')
model = AbstractModel()

# Input data
model.T = Set() # parameter for time
model.I = Set(ordered=True) # parameter for projects 
model.J = Set() # parameter for options for selecting project i
model.K = Set() # parameter for types of resources
model.IJ = Set(dimen=2) # parameter for possible combinations between projects and options

model.r = Param(model.IJ,default=0) # parameter for NPVs
model.b = Param(model.K,model.T,default=0) # parameter for budget 
model.c = Param(model.IJ,model.K,model.T,default=0) # parameter for costs


# Variables
model.x = Var(model.IJ, domain=NonNegativeIntegers, bounds=(0,1)) # variable x, 1 if project i is selected via option j

#Objective function
def obj_rule(model): #objective function max the overall NPV
  return sum(model.r[ij]*model.x[ij] for ij in model.IJ)
model.z = Objective(rule=obj_rule, sense=maximize)

# Constraints
def NodesIn_init(model, project_i): # here we create the set of options j for given project i
    retval = []
    for (i,j) in model.IJ:
        if i == project_i:
            retval.append(j)
    return retval
model.NodesIn = Set(model.I, initialize=NodesIn_init)

def constraintX(model, i): #sum of all x[i,j]=1 for all j
  return sum(model.x[i,j]  for j in model.NodesIn[i]) == 1

model.constraintX = Constraint(model.I,rule=constraintX)

def constraintCX(model, k,t): #budget constraint
  return sum(model.c[ij,k,t]*model.x[ij] for ij in model.IJ) <=model.b[k,t]

model.constraintCX = Constraint(model.K,model.T,rule=constraintCX)

# Create a model instance and optimize
instance = model.create_instance('Deterministic_input_Case2.dat')
results = opt.solve(instance, tee=True)

# Printing the solution
results.write()
instance.solutions.load_from(results)

for v in instance.component_objects(Var, active=True):
    print ("Variable",v)
    varobject = getattr(instance, str(v))
    for index in varobject:
        print ("   ",index, varobject[index].value)





