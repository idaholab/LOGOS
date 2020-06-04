#
#
#
"""
  Created on March. 20, 2019
  @author: wangc, mandd
"""

#for future compatibility with Python 3--------------------------------------------------------------
from __future__ import division, print_function, unicode_literals, absolute_import
import warnings
warnings.simplefilter('default',DeprecationWarning)
#End compatibility block for Python 3----------------------------------------------------------------

#External Modules------------------------------------------------------------------------------------
import numpy as np
import itertools
import logging
import pyomo.environ as pyomo
#External Modules End--------------------------------------------------------------------------------

#Internal Modules------------------------------------------------------------------------------------
try:
  from LOGOS.src.CapitalInvestments.PyomoModels.KnapsackBase import KnapsackBase
except ImportError:
  from .KnapsackBase import KnapsackBase
#Internal Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

class SingleKnapsack(KnapsackBase):
  """
    Class model for single knapsack problems, including multi-dimensional knapsack problem
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    KnapsackBase.__init__(self)
    self.optionalConstraints = {'consistentConstraintI':True}
    self.paramsAuxInfo['available_capitals'] = {'maxDim':1, 'options': [[None], ['time_periods']]}
    self.paramsAuxInfo['net_present_values'] = {'maxDim':1, 'options': [[None], ['investments']]}
    self.paramsAuxInfo['costs'] = {'maxDim':2, 'options': [[None], ['investments'], ['investments', 'time_periods']]}

  def initialize(self, initDict):
    """
      Mehod to initialize
      @ In, initDict, dict, dictionary of preprocessed input data
        {
          'Sets':{setName: list of setValues},
          'Parameters':{paramName:{setsIndex:paramValue}} or {paramName:{'None':paramValue}},
          'Settings':{xmlTag:xmlVal},
          'Meta':{paramName:{setIndexName:indexDim}} or {paramName:None},
          'Uncertainties':{paramName:{'scenarios':{scenarioName:{setIndex:uncertaintyVal}}, 'probabilities': [ProbVals]}}
        }
      @ Out, None
    """
    KnapsackBase.initialize(self, initDict)

  def setScenarioData(self):
    """
      Method to setup the scenario data for scenario tree construction
      @ In, None
      @ Out, None
    """
    KnapsackBase.setScenarioData(self)

  def initializeModel(self):
    """
      Initialize the pyomo model parameters for Knapsack problem (SingleKnapsack)
      @ In, None
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.initializeModel(self)
    model.net_present_values = pyomo.Param(model.investments, mutable=True)
    model.available_capitals = pyomo.Param(model.time_periods, mutable=True)
    model.costs = pyomo.Param(model.investments, model.time_periods, mutable=True)
    return model

  def addConstraints(self, model):
    """
      Add specific constraints for SingleKnapsack problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.addConstraints(self, model)
    def constraintCapacity(model, t):
      """Knapsacks capacity constraints"""
      return sum(model.costs[i,t]*model.x[i] for i in model.investments) <= model.available_capitals[t]
    model.constraintCapacity = pyomo.Constraint(model.time_periods, rule=constraintCapacity)

    if self.mandatory is not None:
      def constraintRegulatory(model, i):
        """Regulatory constraints, always required projects/investments"""
        return model.x[i] == 1
      model.constraintRegulatory = pyomo.Constraint(model.mandatory, rule=constraintRegulatory)

    if self.uncertainties is not None:
      def consistentConstraintI(model, i, j):
        """Constraint for variable y if priority project selection is required"""
        if i == j:
          return model.y[i,j] == model.y[j,i]
        else:
          return model.x[j] + model.y[i,j] - 1 <= model.x[i]
      model.consistentConstraintI = pyomo.Constraint(model.investments, model.investments, rule=consistentConstraintI)

    return model

  def addVariables(self, model):
    """
      Add variables for SingleKnapsack problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.addVariables(self, model)
    def boundsExpression(model, i):
      """ set the bounds for soluion variable x using lowerBounds and upperBounds"""
      return (self.lowerBounds[i], self.upperBounds[i])
    model.x = pyomo.Var(model.investments, domain=pyomo.NonNegativeIntegers, bounds=boundsExpression)
    return model

  def addAdditionalSets(self, model):
    """
      Add specific Sets for SingleKnapsack problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.addAdditionalSets(self, model)
    return model

  def addAdditionalParams(self, model):
    """
      Add specific Params for SingleKnapsack problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.addAdditionalParams(self, model)
    return model

  def addExpressions(self, model):
    """
      Add specific expressions for SingleKnapsack problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.addExpressions(self, model)
    return model

  def addAdditionalConstraints(self, model):
    """
      Add specific constraints for SingleKnapsack problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.addAdditionalConstraints(self, model)
    return model

  def createModel(self):
    """
      This method is used to create pyomo model.
      @ In, None
      @ Out, model, pyomo.AbstractModel, abstract pyomo model
    """
    model = KnapsackBase.createModel(self)
    return model

  def pysp_scenario_tree_model_callback(self):
    """
      scenario tree instance creation callback
      @ In, None
      @ Out, treeModel, Instance, pyomo scenario tree model for two stage stochastic programming,
        extra variables 'y[*,*]' is used to define the priorities of investments
    """
    treeModel = KnapsackBase.pysp_scenario_tree_model_callback(self)
    secondStage = treeModel.Stages.last()
    # second Stage
    treeModel.StageCost[secondStage] = 'secondStageCost'
    treeModel.StageVariables[secondStage].add('x[*]')
    # treeModel.pprint()
    return treeModel

  def printSolution(self, model):
    """
      Output optimization solution to screen
      @ In, model, instance, pyomo optimization model
      @ Out, outputDict, dict, dictionary stores the outputs
    """
    outputDict = KnapsackBase.printSolution(self, model)
    msg = "Selected investments include:"
    logger.info(msg)
    for var in model.component_objects(pyomo.Var, active=True):
      if var.name == 'x':
        for index in var:
          numSelected = pyomo.value(var[index])
          outputDict[str(index)] = [numSelected]
          if numSelected == 1:
            msg = "Investment: " + str(index) + " is selected"
            logger.info(msg)
          elif numSelected > 1:
            msg = "Investment: " + str(index) + " is selected with limit " + str(int(numSelected))
            logger.info(msg)
    # for item in model.investments:
    #   numSelected = pyomo.value(model.x[item])
    #   if numSelected == 1:
    #     msg = "Investment: " + str(item) + " is selected"
    #     logger.info(msg)
    #   elif numSelected > 1:
    #     msg = "Investment: " + str(item) + " is selected with limit " + str(int(numSelected))
    #     logger.info(msg)
    logger.info("Maximum NPV: %16.4f" %(model.obj()))
    outputDict['MaxNPV'] = model.obj()
    # Accessing Duals
    # In some cases, a solver plugin will raise an exception if it encounters a Suffix type that it does not handle
    # One should be careful in verifying that Suffix declarations are being handled as expected when switching
    # to a different solver or solver interface.
    if self.solver == 'cbc':
      logger.info("Duals Information for Constraint Capacity:")
      print("Time_Periods      Capacity_Margin")
      for const in model.component_objects(pyomo.Constraint, active=True):
        if const.name == 'constraintCapacity':
          for index in const:
            print("{0:10s} {1:10.1f}".format(index, model.dual[const[index]]))

    return outputDict
