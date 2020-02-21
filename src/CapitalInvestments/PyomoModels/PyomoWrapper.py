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
  from Logos.src.CapitalInvestments.investment_utils import investmentUtils as utils
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
    Non=pyomo.Suffix.None)

class PyomoWrapper:
  """
    Pyomo Wrapper
  """
  def __init__(self, model):
    """
      Constructor
      @ In, None
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

  def addObjective(self, name='objective', rule, sense='minimize'):
    """
      Add an objective to the optimization problem
      @ In, name, str, name used to define Pyomo.Objective parameter, self._model.name
        can be used to retrieve this parameter
      @ In, rule, function object, a function that is used to construct objective expresssions
      @ In, sense, str, indicate whether minimizing or maximizing
      @ Out, None
    """
    self._model.add_component(name, pyomo.Objective(name='objective', rule=rule, sense=senseKinds[sense]))

  def addSet(self, name, items, ordered=False):
    """
      Add a :class:`pyomo.Set` to the optimization problem
      @ In, name, str, name used to define Pyomo.Set parameter, self._model.name
        can be used to retrieve this parameter
      @ In, items, list/function object, An iterable containing the initial members of the Pyomo.Set,
        or function that returns an iterable of the initial members the set.
      @ In, ordered, bool, the set will be in order if True
      @ Out, None
    """
    self._model.add_component(name, pyomo.Set(initialize=items, name=name, ordered=ordered))

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

  def addConstraint(self, name, rule):
    """
      Create a new constraint and add it to the optimization problem
      @ In, name, str, name used to define Pyomo.Constraint parameter, self._model.name
        can be used to retrieve this constraint
      @ In, rule, function object, a function that is used to construct constraint
      @ Out, None
    """
    self._model.add_component(name, pyomo.Constraint(name=name, rule=rule))

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
      @ In, name,
      @ Out, getComponent,
    """
    try:
      return getattr(self._model, name)
    except (AttributeError, KeyError):
      raise AttributeError('error getting {}'.format(name))

  def writeModel(self, filename):
    """
      Dump the pyomo optimization model
      @ In, filename,
      @ Out, None
    """
    try:
      self._model.write(filename, symbolic_solver_labels=True)
    except:
      self._model.pprint(filename)

  def _removeComponent(self, name):
    """
      Remove an optimization component
      @ In, name,
      @ Out, None
    """
    delattr(self._model, key)

  def resetObjective(self):
    """
      Reset an optimization objective
      @ In, None
      @ Out, None
    """
    delattr(self._model, 'objective')
