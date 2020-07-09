#
#
#
"""
  Created on July. 09, 2020
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
  from LOGOS.src.CapitalInvestments.PyomoModels.MultipleKnapsack import MultipleKnapsack
except ImportError:
  from .MultipleKnapsack import MultipleKnapsack
#Internal Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

class CVaRMKP(MultipleKnapsack):
  """
    Class model for multiple knapsack problem with conditional value at risk as objective function
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    MultipleKnapsack.__init__(self)

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
    MultipleKnapsack.initialize(self, initDict)
    if self.uncertainties is None:
      raise IOError('Uncertainty data is required by "{}", but not provided!'.format(self.name))

  def setScenarioData(self):
    """
      Method to setup the scenario data for scenario tree construction
      @ In, None
      @ Out, None
    """
    MultipleKnapsack.setScenarioData(self)

  @staticmethod
  def computeFirstStageCost(model):
    """"
      Method to compute first stage cost of stochastic programming
      @ In, model, instance, pyomo abstract model instance
      @ Out, expr, float, first stage cost
    """
    expr = -model._lambda * model.u
    return expr

  @staticmethod
  def computeSecondStageCost(model):
    """
      Method to compute second stage cost of stochastic programming, i.e. maximum NPVs
      @ In, model, instance, pyomo abstract model instance
      @ Out, expr, pyomo.expression, second stage cost
    """
    expr = sum(sum(model.net_present_values[i] * model.x[i,m] for i in model.investments) for m in model.capitals) \
           * (1.-model._lambda) - model._lambda/(1.0-model.alpha)*model.nu
    return expr

  def addAdditionalSets(self, model):
    """
      Add specific Sets for DROMKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = MultipleKnapsack.addAdditionalSets(self, model)
    return model

  def addAdditionalParams(self, model):
    """
      Add specific Params for DROMKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = MultipleKnapsack.addAdditionalParams(self, model)
    model._lambda = pyomo.Param(within=pyomo.UnitInterval, mutable=True)
    model.alpha = pyomo.Param(within=pyomo.UnitInterval, mutable=True)
    return model

  def addVariables(self, model):
    """
      Add variables for DROMKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = MultipleKnapsack.addVariables(self, model)
    # variables for CVaR optimization
    model.nu = pyomo.Var(domain=pyomo.NonNegativeReals)
    model.u = pyomo.Var(domain=pyomo.Reals)
    # variables to retrieve additional information
    model.cvar = pyomo.Var(domain=pyomo.Reals)
    model.expectProfit = pyomo.Var(domain=pyomo.Reals)
    return model

  def addAdditionalConstraints(self, model):
    """
      Add specific constraints for DROMKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = MultipleKnapsack.addAdditionalConstraints(self, model)
    def cvarConstraint(model):
      return  model.nu + sum(sum(model.net_present_values[i] * model.x[i,m] for i in model.investments) for m in model.capitals) + model.u >= 0
    model.cvarConstraint = pyomo.Constraint(rule=cvarConstraint)

    ## used to retrieve additional information from the optimization
    def expectProfit(model):
      """
        Method to compute the expect profit of stochastic programming
        @ In, model, instance, pyomo abstract model instance
        @ Out, expr, pyomo.expression, constraint to compute the expect profit
      """
      return model.expectProfit - sum(sum(model.net_present_values[i] * model.x[i,m] for i in model.investments) for m in model.capitals) == 0
    model.computeExpectProfit = pyomo.Constraint(rule=expectProfit)

    def cvar(model):
      """
        Method to compute the conditional value at risk
        @ In, model, instance, pyomo abstract model instance
        @ Out, expr, pyomo.expression, constraint to compute the CVaR
      """
      return model.cvar - model.u + 1./(1.-model.alpha)*model.nu == 0
    model.computeCVAR = pyomo.Constraint(rule=cvar)

    return model

  def createModel(self):
    """
      This method is used to create pyomo model.
      @ In, None
      @ Out, model, pyomo.AbstractModel, abstract pyomo model
    """
    model = MultipleKnapsack.createModel(self)
    return model

  def pysp_scenario_tree_model_callback(self):
    """
      scenario tree instance creation callback
      @ In, None
      @ Out, treeModel, Instance, pyomo scenario tree model for two stage stochastic programming,
        extra variables 'y[*,*]' is used to define the priorities of investments,
        'u' and 'nu' is used for CVaR optimization counter-part.
        'cvar' is used to retrieve the calculated CVaR value
        'expectProfit' is used to retrieve the expect profit under CVaR optimization
    """
    treeModel = MultipleKnapsack.pysp_scenario_tree_model_callback(self)
    # additional variables:
    firstStage = treeModel.Stages.first()
    secondStage = treeModel.Stages.last()
    treeModel.StageVariables[firstStage].add('u')
    treeModel.StageVariables[secondStage].add('nu')
    treeModel.StageVariables[secondStage].add('cvar')
    treeModel.StageVariables[secondStage].add('expectProfit')
    return treeModel
