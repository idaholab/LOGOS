#
#
#
"""
  Created on April. 29, 2020
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
  from LOGOS.src.CapitalInvestments.PyomoModels.SingleKnapsack import SingleKnapsack
except ImportError:
  from .SingleKnapsack import SingleKnapsack
#Internal Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

class DROSKP(SingleKnapsack):
  """
    Class model for distributionally robust optimization of SingleKnapsack problem
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    SingleKnapsack.__init__(self)

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
    SingleKnapsack.initialize(self, initDict)
    if self.uncertainties is None:
      raise IOError('Uncertainty data is required by "{}", but not provided!'.format(self.name))

  def setScenarioData(self):
    """
      Method to setup the scenario data for scenario tree construction
      @ In, None
      @ Out, None
    """
    SingleKnapsack.setScenarioData(self)

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

  def addAdditionalSets(self, model):
    """
      Add specific Sets for SingleKnapsack problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = SingleKnapsack.addAdditionalSets(self, model)
    model.sigma = pyomo.Set(ordered=True)
    return model

  def addAdditionalParams(self, model):
    """
      Add specific Params for SingleKnapsack problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = SingleKnapsack.addAdditionalParams(self, model)
    model.epsilon = pyomo.Param(within=pyomo.NonNegativeReals, mutable=True)
    model.prob = pyomo.Param(model.sigma, within=pyomo.UnitInterval, mutable=True)
    # model.dist will be changed on the fly via scenario callback functions
    model.dist = pyomo.Param(model.sigma, mutable=True)
    return model

  def addVariables(self, model):
    """
      Add variables for DROSKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = SingleKnapsack.addVariables(self, model)
    # variables for robust optimization
    model.gamma = pyomo.Var(within=pyomo.NonNegativeReals)
    model.nu = pyomo.Var(model.sigma)
    return model

  def addAdditionalConstraints(self, model):
    """
      Add specific constraints for DROSKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = SingleKnapsack.addAdditionalConstraints(self, model)
    ## model.dist will be changed on the fly
    def constraintWasserstein(m, i):
      expr = pyomo.summation(m.net_present_values, m.x)
      return -m.gamma * m.dist[i] + m.nu[i] <= expr
    model.constraintWassersteinDistance = pyomo.Constraint(model.sigma, rule=constraintWasserstein)
    return model

  def createModel(self):
    """
      This method is used to create pyomo model.
      @ In, None
      @ Out, model, pyomo.AbstractModel, abstract pyomo model
    """
    model = SingleKnapsack.createModel(self)
    return model

  def pysp_scenario_tree_model_callback(self):
    """
      scenario tree instance creation callback
      @ In, None
      @ Out, treeModel, Instance, pyomo scenario tree model for two stage stochastic programming,
        extra variables 'y[*,*]' is used to define the priorities of investments,
        'gamma' and 'nu[*]' is used for the distributionally robust optimization counter-part.
    """
    treeModel = SingleKnapsack.pysp_scenario_tree_model_callback(self)
    # additional variables:
    firstStage = treeModel.Stages.first()
    treeModel.StageVariables[firstStage].add('gamma')
    treeModel.StageVariables[firstStage].add('nu[*]')
    return treeModel
