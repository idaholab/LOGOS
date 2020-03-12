#
#
#
"""
  Created on Feb. 20, 2020
  @author: wangc, mandd
"""

#for future compatibility with Python 3--------------------------------------------------------------
from __future__ import division, print_function, unicode_literals, absolute_import
#End compatibility block for Python 3----------------------------------------------------------------

#External Modules------------------------------------------------------------------------------------
import abc
import numpy as np
import logging
import pyomo.environ as pyomo
#External Modules End--------------------------------------------------------------------------------

#Internal Modules------------------------------------------------------------------------------------
try:
  from LOGOS.src.CapitalInvestments.investment_utils import investmentUtils as utils
except ImportError:
  from CapitalInvestments.investment_utils import investmentUtils as utils
#Internal Modules End--------------------------------------------------------------------------------

variableKinds = dict(
    Reals=pyomo.Reals,
    Binary=pyomo.Boolean,
    Boolean=pyomo.Boolean,
    PositiveReals=pyomo.PositiveReals,
    NonPositiveReals=pyomo.NonPositiveReals,
    NegativeReals=pyomo.NegativeReals,
    NonNegativeReals=pyomo.NonNegativeReals,
    PercentFraction=pyomo.PercentFraction,
    Integers=pyomo.Integers,
    PositiveIntegers=pyomo.PositiveIntegers,
    NonPositiveIntegers=pyomo.NonPositiveIntegers,
    NegativeIntegers=pyomo.NegativeIntegers,
    NonNegativeIntegers=pyomo.NonNegativeIntegers)

senseKinds = dict(
    minimize=pyomo.minimize,
    maximize=pyomo.maximize)

suffixDirections = dict(Local=pyomo.Suffix.LOCAL,
    Import=pyomo.Suffix.IMPORT,
    Export=pyomo.Suffix.EXPORT,
    ImportAndExport=pyomo.Suffix.IMPORT_EXPORT)

suffixDataTypes = dict(Float=pyomo.Suffix.FLOAT,
    Int=pyomo.Suffix.INT,
    Non=None)

class PyomoWrapper:
  """
    Pyomo Wrapper
  """
  def __init__(self, model):
    """
      Constructor
      @ In, model, pyomo model instance, model used for optimization
      @ Out, None
    """
    # pyomo model instance
    self._model = model

  def addComponent(self, component):
    """
      Add a optimization component, i.e. Pyomo Set, Param, Var, Constraint, to pyomo the model
      @ In, component, instance of Pyomo component, the component that will be added to pyomo model
      @ Out, None
    """
    if ':' in component.name:
        raise ValueError('no colons allowed in optimization object names')
    self._model.add_component(component.name, component)

  def addObjective(self, rule, name='objective', sense='minimize'):
    """
      Add an objective to the optimization problem
      @ In, name, str, name used to define Pyomo.Objective parameter, self._model.name
        can be used to retrieve this parameter
      @ In, rule, function object, a function that is used to construct objective expresssions
      @ In, sense, str, indicate whether minimizing or maximizing
      @ Out, None
    """
    self._model.add_component(name, pyomo.Objective(name='objective', rule=rule, sense=senseKinds[sense]))

  def addSet(self, name, values=None, ordered=True):
    """
      Add a :class:`pyomo.Set` to the optimization problem
      @ In, name, str, name used to define Pyomo.Set parameter, self._model.name
        can be used to retrieve this parameter
      @ In, values, list/function object, An iterable containing the initial members of the Pyomo.Set,
        or function that returns an iterable of the initial members the set.
      @ In, ordered, bool, the set will be in order if True
      @ Out, None
    """
    if values is None:
      raise IOError('"values" for set "{}" need to be provided in order to add it to optimization model!'.format(name))
    self._model.add_component(name, pyomo.Set(initialize=values, name=name, ordered=ordered))

  def addVariable(self, name, low=None, high=None, domain='Binary'):
    """
      Create a new variable and add it to the optimization problem
      @ In, name, str, name used to define Pyomo.Var parameter, self._model.name
        can be used to retrieve this parameter
      @ In, low, float, lower bound
      @ In, high, float, upper bound
      @ In, domain, str, key from variableKinds, indicate a set that is a super-set of the values the variables
        can take on
      @ Out, None
    """
    kwargs = dict(bounds=(low, high), domain=variableKinds[domain])
    var = pyomo.Var(name=name, **kwargs)
    self._model.add_component(name, var)

  def addConstraint(self, name, expression):
    """
      Create a new constraint and add it to the optimization problem
      @ In, name, str, name used to define Pyomo.Constraint parameter, self._model.name
        can be used to retrieve this constraint
      @ In, expression, function object, a function that is used to construct constraint
      @ Out, None
    """
    self._model.add_component(name, pyomo.Constraint(name=name, rule=expression))

  def addConstraintSet(self, name, expression, index):
    """
      Create a new set of constraints and add it to the optimization problem
      @ In, name, str, name used to define Pyomo.Constraint parameter, self._model.name
        can be used to retrieve this constraint
      @ In, index, list-like object, index for the variables
      @ In, expression, function object, a function that is used to construct constraint
      @ Out, None
    """
    self._model.add_component(name, pyomo.Constraint(*index, name=name, rule=expression))

  def addParameter(self, name, index=None, values=None, mutable=True, default=None, **kwargs):
    """
      Create a new Parameter and add it to the optimization problem
      @ In, name, str, name used to define Pyomo.Param parameter, self._model.name
        can be used to retrieve this parameter
      @ In, index, list-like, index for the parameter
      @ In, values, dict, {index:value}, the index will be tuple for 2 or high dimensions
      @ In, mutable, boolean, True if the parameter is mutable
      @ In, default, float, the value absent any other specification
      @ In, kwargs, dict, other related parameters
      @ Out, None
    """
    self._model.add_component(name, pyomo.Param(index, name=name, mutable=mutable, default=default, **kwargs))
    if mutable:
      if values is not None:
        var = self.getComponent(name)
        if index is not None:
          for i in index:
            var[i] = values[i]
        else:
          var[index] = values[index]
      else:
        raise IOError('"values" should be provided when trying to add new paramter "{}"'.format(name))
    else:
      raise IOError('Parameter "{}" need to be defined as mutable in order to change the value of this paramter dynamically.'.format(name))

  def addSuffix(self, name, direction, datatype):
    """
      Create a new suffix and add it to the optimization problem
      @ In, name, str, name used to define Pyomo.Suffix parameter, self._model.name
        can be used to retrieve this suffix
      @ In, direction, str, this trait defines the direction of information flow for the suffix.
        Local - suffix data stays local to the modeling framework and will not be imported or exported by a solver plugin (default)
        Import - suffix data will be imported from the solver by its respective solver plugin
        Export - suffix data will be exported to a solver by its respective solver plugin
        ImportAndExport - suffix data flows in both directions between the model and the solver or algorithm
      @ In, datatype, str, this trait advertises the type of data held on the suffix for those interfaces
        where it matters (e.g., the NL file interface). A suffix datatype can be assigned one of three possible
      @ Out, None
    """
    self._model.add_component(name, pyomo.Suffix(direction=suffixDirections[direction],
                              datatype=suffixDataTypes[datatype]))

  def getComponent(self, name):
    """
      Get an optimization component
      @ In, name, str, the component name
      @ Out, getComponent, return the required component
    """
    try:
      return getattr(self._model, name)
    except (AttributeError, KeyError):
      raise AttributeError('error getting {}'.format(name))

  def getVariable(self, name):
    """
      Get an optimization variable
      @ In, name, str, the variable name
      @ Out, getVariable, return the required variable
    """
    return self.getComponent(name)

  def getParameter(self, name):
    """
      Get an optimization Parameter
      @ In, name, str, the Parameter name
      @ Out, getParameter, pyomo.Param, return the required Parameter
    """
    return self.getComponent(name)

  def getSet(self, name):
    """
      Get an optimization Set parameter
      @ In, name, str, the Set name
      @ Out, getSet, pyomo.Set, return the required Set parameter
    """
    return self.getComponent(name)

  def getAllParameters(self, paramsList):
    """
      Get an optimization Parameter
      @ In, paramsList, list, the list of Parameter names
      @ Out, paramsDict, dict, return all parameters and their associated Pyomo.Param object
    """
    paramsDict = {}
    for paramName in paramsList:
      paramsDict[paramName] = self.getParameter(paramName)
    return paramsDict

  def getAllSets(self, setsList):
    """
      Get an optimization Set parameter
      @ In, setsList, list, the list of Set names
      @ Out, setsDict, dict, return all sets and their associated Pyomo.Set object
    """
    setsDict = {}
    for setName in setsList:
      setsDict[setName] = self.getSet(setName)
    return setsDict

  def updateParam(self, paramName, updateDict):
    """
      A Pyomo Param value can be updated without the user directly accessing the pyomo model.
      Value(s) will be updated in-place, requiring the user to run the model again to
      see the effect on results.
      @ In, paramName, str, name of the parameter to update
      @ In, updateDict, dict, keys are parameter indeces (either strings or tuples of strings,
        depending on whether there is one or more than one dimension). Values
        are the new values being assigned to the parameter at the given indeces.
      @ Out, None
    """
    modelParam = self.getComponent(paramName)
    if not isinstance(modelParam, pyomo.base.param.IndexedParam):
      raise IOError(
          '`{}` not a Parameter in the optimization model. Sets and decision variables '
          'cannot be updated by the user'.format(paramName)
      )
    elif not isinstance(updateDict, dict):
      raise TypeError('`updateDict` must be a dictionary')
    else:
      print(
          'WARNING: we currently do not check that the updated value is the '
          'correct data type for this Optimization Parameter, this is your '
          'responsibility to check!'
      )
      modelParam.store_values(updateDict)

  def updateParams(self, updateDict):
    """
      Update Pyomo Parameters values without the user directly accessing the pyomo model.
      Value(s) will be updated in-place, requiring the user to run the model again to
      see the effect on results.
      @ In, updateDict, dict, {paramName:paramInfoDict},  where paramInfoDict contains {Indices:Values}
        Indices are parameter indices (either strings or tuples of strings, depending on whether there is one or more than one dimension).
        Values are the new values being assigned to the parameter at the given indeces.
      @ Out, None
    """
    for paramName, paramDict in updateDict.items():
      self.updateParam(paramName, paramDict)


  def activateConstraint(constraint, active=True):
    """
      Takes a constraint or objective name, finds it in the optimization model and sets
      its status to either active or deactive.
      @ In, constraint, str, Name of the constraint/objective to activate/deactivate
        Built-in constraints include '_constraint'
      @ In, activate, bool, default is True, status to set the constraint/objective
      Parameters
    """
    modelConstraint = self.getComponent(constraint)
    if not isinstance(modelConstraint, pyomo.base.Constraint):
      raise exceptions.ModelError(
          '`{}` not a constraint in the optimization model!'.format(constraint)
      )
    elif active is True:
      modelConstraint.activate()
    elif active is False:
      modelConstraint.deactivate()
    else:
      raise ValueError('Argument `active` must be True or False')

  def writeModel(self, filename):
    """
      Dump the pyomo optimization model
      @ In, filename, str, the filename that the model info will be dumped.
      @ Out, None
    """
    try:
      self._model.write(filename, symbolic_solver_labels=True)
    except:
      self._model.pprint(filename)

  def removeComponent(self, name):
    """
      Remove an optimization component
      @ In, name, str, the component name
      @ Out, None
    """
    delattr(self._model, name)

  def resetObjective(self, name):
    """
      Reset an optimization objective
      @ In, name, str, the objective name
      @ Out, None
    """
    delattr(self._model, name)

  def getModel(self):
    """
      Return the Pyomo model
      @ In, None
      @ Out, getModel, Pyomo model instance, model used for optimization
    """
    return self._model
