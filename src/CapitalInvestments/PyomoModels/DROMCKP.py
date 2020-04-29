#
#
#
"""
  Created on April. 22, 2020
  @author: wangc, mandd
"""

#External Modules------------------------------------------------------------------------------------
import numpy as np
import itertools
import logging
import pyomo.environ as pyomo
#External Modules End--------------------------------------------------------------------------------

#Internal Modules------------------------------------------------------------------------------------
try:
  from LOGOS.src.CapitalInvestments.PyomoModels.MCKP import MCKP
except ImportError:
  from .MCKP import MCKP
#Internal Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

class DROMCKP(MCKP):
  """
    Class model for distributional robust optimization of MCKP problem
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    MCKP.__init__(self)
    self.epsilon = 0.1

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
    MCKP.initialize(self, initDict)
    if self.uncertainties is None:
      raise IOError('Uncertainty data is required by "{}", but not provided!'.format(self.name))

  def setScenarioData(self):
    """
      Method to setup the scenario data for scenario tree construction
      @ In, None
      @ Out, None
    """
    MCKP.setScenarioData(self)

  @staticmethod
  def computeFirstStageCost(model):
    """"
      Method to compute frist stage cost of stochastic programming
      @ In, model, instance, pyomo abstract model instance
      @ Out, expr, float, frist stage cost
    """

    expr = -model.epsilon * model.gamma + pyomo.summation(model.prob, model.nu)
    return expr

  @staticmethod
  def computeSecondStageCost(model):
    """
      Method to compute second stage cost of stochastic programming, i.e. maximum NPVs
      @ In, model, instance, pyomo abstract model instance
      @ Out, expr, pyomo.expression, second stage cost
    """
    expr = 0.0
    return expr

  @staticmethod
  def objExpression(model):
    """
      Method to compute objective expression
      @ In, model, instance, pyomo abstract model instance
      @ Out, objExpression, pyomo.expression, objective expression
    """
    return model.firstStageCost + model.secondStageCost

  def addAdditionalSets(self, model):
    """
      Add specific Sets for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model.sigma = pyomo.Set(ordered=True)
    return model

  def addAdditionalParams(self, model):
    """
      Add specific Params for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model.epsilon = pyomo.Param(within=pyomo.NonNegativeReals, default=0.0, mutable=True)
    model.prob = pyomo.Param(model.sigma, within=pyomo.UnitInterval, mutable=True)
    # model.dist will be changed on the fly via scenario callback functions
    # model.dist = pyomo.Param(model.sigma, model.sigma,  mutable=True)
    model.dist = pyomo.Param(model.sigma, mutable=True)
    return model

  def addVariables(self, model):
    """
      Add variables for DROMCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = MCKP.addVariables(self, model)
    # variables for robust optimization
    model.gamma = pyomo.Var(within=pyomo.NonNegativeReals)
    model.nu = pyomo.Var(model.sigma, domain=pyomo.NonNegativeReals)
    return model

  def addAdditionalConstraints(self, model):
    """
      Add specific constraints for DROMCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    # TODO: define model.sigma, find a way to implement the following constraint, calculate distance
    # def constraintWasserstein(model, i, j):
    #   expr = pyomo.summation(model.net_present_values, model.x)
    #   return -model.gamma * model.dist[i,j] + model.nu[i] <= expr
    # model.constraintWassersteinDistance = pyomo.Constraint(model.sigma, model.sigma, rule=constraintWasserstein)
    # return model
    ## model.dist will be changed on the fly
    def constraintWasserstein(model, i):
      expr = pyomo.summation(model.net_present_values, model.x)
      return -model.gamma * model.dist[i] + model.nu[i] <= expr
    model.constraintWassersteinDistance = pyomo.Constraint(model.sigma, rule=constraintWasserstein)
    return model

  def multipleChoiceKnapsackModel(self):
    """
      This method is used to create pyomo model.
      @ In, None
      @ Out, model, pyomo.AbstractModel, abstract pyomo model
    """
    model = MCKP.multipleChoiceKnapsackModel(self)
    model = self.addAdditionalConstraints(model)
    return model

  def pysp_scenario_tree_model_callback(self):
    """
      scenario tree instance creation callback
      @ In, None
      @ Out, treeModel, Instance, pyomo scenario tree model for two stage stochastic programming,
        extra variables 'y[*,*]' is used to define the priorities of investments
    """
    treeModel = self.createScenarioTreeModel()
    firstStage = treeModel.Stages.first()
    secondStage = treeModel.Stages.last()
    # first Stage
    treeModel.StageCost[firstStage] = 'firstStageCost'
    treeModel.StageVariables[firstStage].add('y[*,*]')
    # additional variables:
    treeModel.StageVariables[firstStage].add('gamma')
    treeModel.StageVariables[firstStage].add('nu[*]')
    # second Stage
    treeModel.StageCost[secondStage] = 'secondStageCost'
    treeModel.StageVariables[secondStage].add('x[*,*]')
    # treeModel.pprint()
    return treeModel
