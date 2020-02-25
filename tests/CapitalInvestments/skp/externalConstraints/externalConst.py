
# External constraint function
import numpy as np
import pyomo.environ as pyomo

def initialize():
  """
    Optimization model parameter value can be updated without the user directly accessing the optimization model.
    Value(s) will be updated in-place.
    @ In, None
    @ Out, updateDict, dict, {paramName:paramInfoDict},  where paramInfoDict contains {Indices:Values}
      Indices are parameter indices (either strings or tuples of strings, depending on whether there is one or more than one dimension).
      Values are the new values being assigned to the parameter at the given indeces.
  """
  # updateDict = {}
  updateDict = {'available_capitals':{'None':16},
  'costs':{'1':1,'2':3,'3':7,'4':4,'5':8,'6':9,'7':6,'8':10,'9':2,'10':5}
  }
  return updateDict

def constraint(var, sets, params):
  """
    External constraint provided by users that will be added to optimization problem
    @ In, sets, dict, all "Sets" provided in the Logos input file will be stored and
      available in this dictionary, i.e. {setName: setObject}
    @ In, params, dict, all "Parameters" provided in the Logos input file will be stored and
      available in this dictionary, i.e. {paramName: paramObject}
    @ In, var, object, the internally used decision variable, the dimensions/indices of this
      variable depend the type of optimization problems (i.e. "<problem_type>" from Logos input file).
      Currently, we will accept the following problem types:

      1. "singleknapsack": in this case, "var" will be var[:], where the index will be the element from
        xml node of "investment" in Logos input file.

      2. "multipleknapsack": in this case, "var" will be var[:,:], where the indices are the combinations
        element from set "investment" and element from set "capitals" in Logos input file

      3. "mckp": in this case, "var" will be var[:,:], where the indices are the combinations
        element from set "investment" and element from set "options" in Logos input file

      (Note that all element that is used as index will be converted to string even if
      a number is provided in the Logos input file).

    @ Out,

    (Note that any modifications in provided sets and params will only have impact on this local module,
    i.e. the external constraint. In other words, the Sets and Params used in the internal constraints and
    objective will be kept unchanged!)
  """

  investments = sets['investments']

  def constraintRule(self, i):
    return var[i] <= 1

  return (constraintRule, investments)
