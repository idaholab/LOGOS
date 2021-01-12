
# External constraint function
import numpy as np
import pyomo.environ as pyomo

def initialize():
  """
    Optional Method
    Optimization model parameters values can be updated/modified without directly accessing the optimization model.
    Value(s) will be updated in-place.
    @ In, None
    @ Out, updateDict, dict, {paramName:paramInfoDict},  where paramInfoDict contains {Indices:Values}
      Indices are parameter indices (either strings or tuples of strings, depending on whether there is one or
      more than one dimension). Values are the new values being assigned to the parameter at the given indeces.
  """
  updateDict = {'available_capitals':{'None':16},
  'costs':{'1':1,'2':3,'3':7,'4':4,'5':8,'6':9,'7':6,'8':10,'9':2,'10':5}
  }
  return updateDict

def constraint(var, sets, params):
  """
    Required Method
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

    @ Out, constraint, tuple, either (constraintRule,) or (constraintRule, indices)

    (Note that any modifications in provided sets and params will only have impact on this local module,
    i.e. the external constraint. In other words, the Sets and Params used in the internal constraints and
    objective will be kept unchanged!)
  """
  # All sets and parameters can be retrieved from dictionary "sets" and "params"
  investments = sets['investments']

  def constraintRule(self, i):
    """
      Expression for user provided external constraint
      @ In, self, object, required to present, but not used
      @ In, i, str, element for the index set
      @ Out, constraintRule, function expression, expression to define user provided constraint

      Note that: Constraints can be indexed by lists or sets. When the return of function "constraint" contains
      lists or sets except the "constraintRule", the elements are iteratively passed to the rule function. If there
      is more than one, then the cross product is sent.
      For example, this constraint could be interpreted as placing limit on "ith" decision variable "var".
      A list of constraints for all "ith" decision variable "var" will be added to the optimization model
    """
    return var[i] <= 1

  # A tuple is required for the return, the first element should be always the "constraintRule",
  # while the rest of elements are the lists or sets if the user wants to construct the constraints
  # iteratively (See the docstring in "constraintRule"), otherwise, keep it empty
  return (constraintRule, investments)
