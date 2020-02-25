
# External constraint function
import numpy as np

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

def constraint(inputs):
  """
  """
  pass
