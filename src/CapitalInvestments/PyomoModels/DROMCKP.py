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
    Class model for distributionally robust optimization of MCKP problem
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    MCKP.__init__(self)

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
      Method to compute first stage cost of stochastic programming
      @ In, model, instance, pyomo abstract model instance
      @ Out, expr, float, first stage cost
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
      Add specific Sets for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = MCKP.addAdditionalSets(self, model)
    model.sigma = pyomo.Set(ordered=True)
    return model

  def addAdditionalParams(self, model):
    """
      Add specific Params for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = MCKP.addAdditionalParams(self, model)
    model.epsilon = pyomo.Param(within=pyomo.NonNegativeReals, mutable=True)
    model.prob = pyomo.Param(model.sigma, within=pyomo.UnitInterval, mutable=True)
    # model.dist will be changed on the fly via scenario callback functions
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
    ## the following two definitions are not equivalent for mckp, not sure why?
    ## From the definition of dual problem, nu should be non-negative
    model.nu = pyomo.Var(model.sigma, within=pyomo.NonNegativeReals)
    # model.nu = pyomo.Var(model.sigma)
    return model

  def addAdditionalConstraints(self, model):
    """
      Add specific constraints for DROMCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = MCKP.addAdditionalConstraints(self, model)
    ## model.dist will be changed on the fly
    def constraintWasserstein(model, i):
      expr = pyomo.summation(model.net_present_values, model.x)
      return -model.gamma * model.dist[i] + model.nu[i] <= expr
    model.constraintWassersteinDistance = pyomo.Constraint(model.sigma, rule=constraintWasserstein)
    return model

  def createModel(self):
    """
      This method is used to create pyomo model.
      @ In, None
      @ Out, model, pyomo.AbstractModel, abstract pyomo model
    """
    model = MCKP.createModel(self)
    return model

  def pysp_scenario_tree_model_callback(self):
    """
      scenario tree instance creation callback
      @ In, None
      @ Out, treeModel, Instance, pyomo scenario tree model for two stage stochastic programming,
        extra variables 'y[*,*]' is used to define the priorities of investments,
        'gamma' and 'nu[*]' is used for the distributionally robust optimization counter-part.
    """
    treeModel = MCKP.pysp_scenario_tree_model_callback(self)
    # additional variables:
    firstStage = treeModel.Stages.first()
    treeModel.StageVariables[firstStage].add('gamma')
    treeModel.StageVariables[firstStage].add('nu[*]')
    return treeModel
