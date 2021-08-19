# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
  Created on March. 19, 2019
  @author: wangc, mandd
"""

#External Modules------------------------------------------------------------------------------------
import abc
import copy
import itertools
import numpy as np
import logging
import pandas as pd
import collections
from ast import literal_eval
import pyomo.environ as pyomo
#External Modules End--------------------------------------------------------------------------------

#Internal Modules------------------------------------------------------------------------------------
try:
  from LOGOS.src.CapitalInvestments.PyomoModels.PySPBase import PySPBase
  from LOGOS.src.CapitalInvestments.investment_utils import investmentUtils as utils
except ImportError:
  from .PySPBase import PySPBase
  from CapitalInvestments.investment_utils import investmentUtils as utils
#Internal Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

class KnapsackBase(PySPBase):
  """
    Base class for methods used to solving knapsack problem
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    super().__init__()
    self.mandatory = None # regulatory mandated projects
    self.nonSelection = False   # options DoNothing should be included for each projects if True, otherwise should not be included

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
    super().initialize(initDict)
    ## update lower and upper bounds for variables
    indices = list(self.sets['investments'])
    self.lowerBounds = self.setBounds(self.lowerBounds, indices, 'lowerBounds')
    self.upperBounds = self.setBounds(self.upperBounds, indices, 'upperBounds')

  def setBounds(self, bounds, indices, boundName):
    """
      Method to setup the bounds of decision variables
      @ In, bounds, list, list of user provided bounds
      @ In, indices, list, the indices corresponding to the provided bounds
      @ In, boundName, str, i.e. lowerBounds or upperBounds
      @ Out, fullBounds, dict, {index:bound}
    """
    fullBound = None
    if len(bounds) == 1:
      fullBounds = bounds * len(self.sets['investments'])
      fullBounds = dict(zip(indices, fullBounds))
    elif len(bounds) == len(self.sets['investments']):
      fullBounds = dict(zip(indices, bounds))
    else:
      msg = boundName + ' should be either scalar or comma separated list with number of elements equal to ' + \
            ' the number of provided investments!'
      raise IOError(msg)
    return fullBounds

  def setScenarioData(self):
    """
      Method to setup the scenario data for scenario tree construction
      @ In, None
      @ Out, None
    """
    super().setScenarioData()

  def setSettings(self):
    """
      Method to process the settings of pyomo solver
      @ In, None
      @ Out, None
    """
    super().setSettings()
    self.nonSelection = utils.convertStringToBool(self.settings.pop('nonSelection', 'False'))
    mandatory = self.settings.pop('mandatory',None)
    if mandatory is not None:
      self.mandatory = utils.convertNodeTextToList(mandatory)
      if not set(self.mandatory).issubset(self.sets['investments']):
        raise IOError('"mandatory" list should be a subset of "investments"!')

  def generateModelInputData(self):
    """
      This method is used to generate input data for pyomo model
      @ In, None
      @ Out, data, dict, input data for pyomo model
    """
    data = {}
    # Generate input set data
    indexName = 'investments'
    if indexName not in self.sets.keys():
      raise IOError('Required node ' + indexName + ' is not found in input file, please specify it under node "Sets"!')
    else:
      data[indexName] = {None:self.sets[indexName]}
    indexList = ['time_periods','capitals','options','resources']
    for indexName in indexList:
      data[indexName] = self.processInputSets(indexName)
    # Generate input regulatory mandated data
    if self.mandatory is not None:
      data['mandatory'] = {None: self.mandatory}

    # set the Parameters with the extended indices
    for paramName, paramInfo in self.paramsAuxInfo.items():
      options = paramInfo['options']
      maxDim = paramInfo['maxDim']
      if paramName not in self.params.keys():
        raise IOError('Required node ' + paramName + ' is not found in input file, please specify it under node "Parameters"!')
      else:
        data[paramName] = self.setParameters(paramName, options, maxDim, self.params[paramName])

    ## used for DRO model
    if self.uncertainties is not None and 'DRO' in self.name:
      # logger.info('Generate additional model inputs for DRO optimization')
      data['sigma'] = {None:list(self.scenarios['probabilities'].keys())}
      data['prob'] = copy.copy(self.scenarios['probabilities'])
      data['epsilon'] = {None:self.epsilon}
      distData = copy.copy(self.distData[0,:])
      smIndices = list(self.scenarios['probabilities'].keys())
      data['dist'] = dict(zip(smIndices,np.ravel(distData)))
    # used for CVaR model
    if self.uncertainties is not None and 'CVaR' in self.name:
      # logger.info('Generate additional model inputs for CVaR optimization')
      data['_lambda'] = {None: self._lambda}
      data['alpha'] = {None: self.alpha}
    data = {None:data}
    return data

  def setParameters(self, paramName, options, maxDim, initParamDict):
    """
      Method to generate Parameter input for pyomo model
      @ In, paramName, str, name of parameter
      @ In, options, list, list of all optional list of the corresponding parameter index
      @ In, maxDim, int, the max number of indices that the parameter can take
      @ In, initParamDict, dict, initial parameter dictionary {paramName:{originalIndex:paramVal}
      @ Out, paramDict, dict, {extendedIndex:paramVal}, dictionary of parameters in the format that is
        used by Pyomo model.
    """
    paramDict = None
    if maxDim == 1:
      indexList = list(self.meta['Parameters'][paramName].keys())
      if len(indexList) == 1 and indexList in options:
        paramDict = initParamDict
    elif maxDim == 2:
      indexList = list(self.meta['Parameters'][paramName].keys())
      if len(indexList) == 1 and indexList in options:
        pos = options.index(indexList)
        indices = [list(initParamDict.keys()),['None']] if pos == 1 else [['None'], list(initParamDict.keys())]
        indices = list(itertools.product(*indices))
        paramDict = dict(zip(indices,initParamDict.values()))
      elif len(indexList) == 2 and indexList in options:
        paramDict = initParamDict
    elif maxDim == 3:
      indexList = list(self.meta['Parameters'][paramName].keys())
      if len(indexList) == 1 and indexList in options:
        indices = [list(initParamDict.keys()), ['None'], ['None']]
        indices = list(itertools.product(*indices))
        paramDict = dict(zip(indices,initParamDict.values()))
      elif len(indexList) == 2 and indexList in options:
        paramDict = collections.OrderedDict()
        pos = options.index(indexList)
        for key, val in initParamDict.items():
          newKey = key + ('None',) if pos == 1 else (key[0],'None',key[1])
          paramDict[newKey] = val
      elif len(indexList) == 3 and indexList in options:
        paramDict = initParamDict
    else:
      raise IOError('Not implemented for parameters with indices more than three!')
    if paramDict is None:
      msg = 'Please check the index for node ' + paramName + '! Options include: '
      msg += ','.join([str(ind) for ind in options]) + '\n'
      msg += 'Please make sure that they in the correct order!'
      msg += 'The following indices is provided by user: ' + ','.join(indexList)
      raise IOError(msg)
    return paramDict

  def collectPriorityOutputs(self, solutionGenerator):
    """
      collect priority outputs
      @ In, solutionGenerator, Instance, pyomo solution generator
      @ Out, None
    """
    dim = len(self.sets['investments'])
    ysol = pd.DataFrame(np.zeros((dim,dim)), index=self.sets['investments'], columns=self.sets['investments'])

    if 'DRO' in self.name:
      for var, val in solutionGenerator:
        # value is stored as y[('i','j')], gamma, nu[]
        if var == 'gamma':
          logger.info('Variable "gamma": {}'.format(val))
        elif var.split('[')[0] == 'nu':
          logger.info('Variable "nu" at scenario "{}": {}'.format(var, val))
        else:
          ind = literal_eval(var[var.index('[') + 1 : var.index(']')])
          ysol.at[ind] = val
    elif 'CVaR' in self.name:
       # value is stored as y[('i','j')], u, nu
       for var, val in solutionGenerator:
         if var == 'u':
           logger.info('Variable "u" or "Value at Risk" for loss: {}'.format(val))
         else:
           ind = literal_eval(var[var.index('[') + 1 : var.index(']')])
           ysol.at[ind] = val
    else:
      for var, val in solutionGenerator:
        # value is stored as y[('i','j')]
        ind = literal_eval(var[var.index('[') + 1 : var.index(']')])
        ysol.at[ind] = val
    priorityList = ysol.sum(axis=0) + 1
    priorityList = priorityList.sort_values()
    msg = "Priorities of investments:"
    logger.info(msg)
    priorityLevel = 0
    for order, ind in enumerate(priorityList.index):
      if order == 0 or priorityList.iloc[order] > priorityList.iloc[order - 1]:
        priorityLevel += 1
      msg = 'Investment ' + str(ind).ljust(4) + ' is assigned priority level: ' + str(priorityLevel)
      logger.info(msg)

  def printScenarioSolution(self, stsolver):
    """
      Output optimization solution to screen
      @ In, stsolver, instance, pyomo stochastic programming solver instance
      @ Out, None
    """
    super().printScenarioSolution(stsolver)
    self.collectPriorityOutputs(stsolver.root_Var_solution())
    obj = stsolver.root_E_obj()
    logger.info("Expecatation of NPV take over scenarios = %16.4f" %(obj))

  def writeOutput(self, filename):
    """
      Method used to output the optimization results
      @ In, filename, string, filename of output file
      @ Out, None
    """
    super().writeOutput(filename)

  @staticmethod
  def computeFirstStageCost(model):
    """"
      Method to compute first stage cost of stochastic programming
      @ In, model, instance, pyomo abstract model instance
      @ Out, computeFirstStageCost, float, first stage cost
    """
    return 0.0

  @staticmethod
  def computeSecondStageCost(model):
    """
      Method to compute second stage cost of stochastic programming, i.e. maximum NPVs
      @ In, model, instance, pyomo abstract model instance
      @ Out, expr, pyomo.expression, second stage cost
    """
    expr = pyomo.summation(model.net_present_values, model.x)
    return expr

  @staticmethod
  def objExpression(model):
    """
      Method to compute objective expression
      @ In, model, instance, pyomo abstract model instance
      @ Out, objExpression, pyomo.expression, objective expression
    """
    return model.firstStageCost + model.secondStageCost

  @staticmethod
  def orderConstraintI(model, i, j):
    """
      Constraint for variable y if priority project selection is required
      @ In, model, instance, pyomo abstract model instance
      @ i, str, investment index
      @ j, str, investment index
      @ orderConstraintI, pyomo.expression, expression about orderConstraintI
    """
    if i < j:
      return model.y[i,j] + model.y[j,i] == 1
    else:
      return pyomo.Constraint.Skip

  @staticmethod
  def constraintY(model, i):
    """
      Constraint for variable y if priority project selection is required
      @ In, model, instance, pyomo abstract model instance
      @ i, str, investment index
      @ constraintY, pyomo.expression, expression about constraint on variable Y
    """
    return model.y[i,i] == 0

  @staticmethod
  def constraintNoTie(model, i, ip, idp):
    """
      Help to produce a total ordering of the projects rather than allowing ties. This constraint
      will not change the optimal NPV
      @ In, model, instance, pyomo abstract model instance
      @ In, i, str, investment index
      @ In, ip, str, investment index
      @ In, idp, str, investment index
      @ Out, constraintNoTie, pyomo.expression, constraint to remove ties
    """
    if i != ip and ip != idp and idp != i:
      return model.y[i,ip] + model.y[ip,idp] + model.y[idp,i] <= 2
    else:
      return pyomo.Constraint.Skip

  def initializeModel(self):
    """
      Initialize the pyomo model parameters for Knapsack problem (MCKP)
      @ In, None
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = pyomo.AbstractModel()
    model.time_periods = pyomo.Set()
    model.investments = pyomo.Set(ordered=True)
    if self.mandatory is not None:
      model.mandatory = pyomo.Set()
    return model

  def addObjective(self, model):
    """
      Add objective for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model.obj = pyomo.Objective(rule=self.objExpression, sense=self.sense)
    return model

  def addVariables(self, model):
    """
      Add variables for the problem
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    if self.uncertainties is not None:
      model.y = pyomo.Var(model.investments, model.investments, domain=self.solutionVariableType)
    return model

  def addConstraints(self, model):
    """
      Add specific constraints for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    # constraint for scenario analysis
    if self.uncertainties is not None:
      # constraint (1b) and (1h)
      model.orderConstraintI = pyomo.Constraint(model.investments, model.investments, rule=self.orderConstraintI)
      # constraint (1b) extension
      model.constraintY = pyomo.Constraint(model.investments, rule=self.constraintY)
      # constraint (1i) helps to remove ties
      model.constraintNoTie = pyomo.Constraint(model.investments, model.investments, model.investments, rule=self.constraintNoTie)
    return model

  def addAdditionalSets(self, model):
    """
      Add specific Sets for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = super().addAdditionalSets(model)
    return model

  def addAdditionalParams(self, model):
    """
      Add specific Params for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = super().addAdditionalParams(model)
    return model

  def addExpressions(self, model):
    """
      Add specific expressions for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = super().addExpressions(model)
    model.firstStageCost = pyomo.Expression(rule=self.computeFirstStageCost)
    model.secondStageCost = pyomo.Expression(rule=self.computeSecondStageCost)
    return model

  def addAdditionalConstraints(self, model):
    """
      Add specific constraints for DROMCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = super().addAdditionalConstraints(model)
    return model

  def createModel(self):
    """
      This method is used to create pyomo model.
      @ In, None
      @ Out, model, pyomo.AbstractModel, abstract pyomo model
    """
    model = super().createModel()
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
    # first Stage
    treeModel.StageCost[firstStage] = 'firstStageCost'
    treeModel.StageVariables[firstStage].add('y[*,*]')
    # second Stage added by specific model
    return treeModel

  def printSolution(self, model):
    """
      Output optimization solution to screen
      @ In, model, instance, pyomo optimization model
      @ Out, outputDict, dict, dictionary stores the outputs
    """
    outputDict = super().printSolution(model)
    return outputDict
