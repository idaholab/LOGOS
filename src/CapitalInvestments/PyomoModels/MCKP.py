#
#
#
"""
  Created on April. 11, 2019
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
  from LOGOS.src.CapitalInvestments.PyomoModels.PyomoWrapper import PyomoWrapper
except ImportError:
  from .KnapsackBase import KnapsackBase
  from .PyomoWrapper import PyomoWrapper
#Internal Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

class MCKP(KnapsackBase):
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
    self.optionalConstraints = {'consistentConstraintI':True, 'consistentConstraintII':False}
    self.paramsAuxInfo['available_capitals'] = {'maxDim':2,
      'options': [[None], ['resources'], ['time_periods'], ['resources','time_periods']]
    }
    self.paramsAuxInfo['net_present_values'] = {'maxDim':1,
      'options': [['options']]
    }
    self.paramsAuxInfo['costs'] = {'maxDim':3,
      'options': [['options'],['options','resources'],['options','time_periods'],['options','resources','time_periods']]
    }

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
    # uncertainties for multiple parameters
    logger.info('Initialize Uncertainties for Optimization Instance: %s', self.name)
    self.scenarios = {}
    scenarioList = []
    scenarioNameList = []
    scenarioProbList = []
    paramList = []
    for paramName, scenarioDict in self.uncertainties.items():
      if paramName == 'available_capitals':
        options = [[None], ['resources'], ['time_periods'], ['resources','time_periods']]
        sdKeys = list(self.meta['Parameters'][paramName].keys())
        print(sdKeys)
        if len(sdKeys) == self.paramsAuxInfo['available_capitals']['maxDim']:
          scenarioList.append(list(scenarioDict['scenarios'].values()))
        elif len(sdKeys) == 1 and sdKeys in options:
          pos = options.index(sdKeys)
          dataList = []
          for scenarioData in scenarioDict['scenarios'].values():
            indices = [list(scenarioData.keys()), ['None']] if pos == 1 else [['None'], list(scenarioData.keys())]
            indices = list(itertools.product(*indices))
            paramDict = dict(zip(indices, list(scenarioData.values())))
            dataList.append(paramDict)
          scenarioList.append(dataList)
        else:
          raise IOError('Not supported index: ' + ','.join(sdKeys))
      else:
        scenarioList.append(list(scenarioDict['scenarios'].values()))
      scenarioNameList.append(list(scenarioDict['scenarios'].keys()))
      scenarioProbList.append(scenarioDict['probabilities'])
      paramList.append(paramName)
    self.scenarios['params'] = paramList
    self.scenarios['scenario_name'] = dict(('scenario_' + str(i), name) for i, name in enumerate(list(itertools.product(*scenarioNameList)), 1))
    self.scenarios['probabilities'] = dict(('scenario_' + str(i), float(np.product(list(prob)))) for i, prob in enumerate(list(itertools.product(*scenarioProbList)), 1))
    self.scenarios['scenario_data'] = dict(('scenario_' + str(i), dict(zip(paramList, data))) for i, data in enumerate(list(itertools.product(*scenarioList)), 1))

  @staticmethod
  def optionsOutInit(model, option):
    """
      Method to initialize the given option
      @ In, model, instance, pyomo abstract model instance
      @ In, option, str, name of given option
      @ Out, retval, list, list of values for given option
    """
    retval = []
    for (i,j) in model.options:
      if i == option:
        retval.append(j)
    return retval

  @staticmethod
  def investmentOptionInit(model):
    """
      Method to initialize the option of investment
      @ In, model, instance, pyomo abstract model instance
      @ Out, investmentOptionInit, tuple, tuple of investment-option pairs
    """
    return ((i,j) for i in model.investments for j in model.optionsOut[i])

  @staticmethod
  def constraintCapacity(model, k, t):
    """
      Knapsacks capacity constraints
      This constraint requires that we be within budget in each time period,
      for each resource type, under each scenario
      @ In, model, instance, pyomo abstract model instance
      @ In, k, str, resource index
      @ In, t, str, time index
      @ Out, constraintCapacity, pyomo.expression, capacity constraint
    """
    expr = sum(sum(model.costs[i,j,k,t]*model.x[i,j] for j in model.optionsOut[i]) for i in model.investments)
    return expr <= model.available_capitals[k,t]

  @staticmethod
  def constraintRegulatory(model, i):
    """
      Regulatory constraints, always required projects/investments
      @ In, model, instance, pyomo abstract model instance
      @ In, i, str, regulatory index
      @ Out, constraintRegulatory, pyomo.expression, regulatory constraint
    """
    # When Non-Selection is included, the following constraint should be used.
    # return sum(model.x[i,j] for j in model.optionsOut[i]) - model.x[i,model.optionsOut[i].last()] == 1
    return sum(model.x[i,j] for j in model.optionsOut[i]) == 1

  @staticmethod
  def constraintXNonSelection(model,i):
    """
      sum of investments over knapsacks should less or equal than bounds
      constraint to handle 'DoNothing' options --> (1f)
      @ In, model, instance, pyomo abstract model instance
      @ In, i, str, investment index
      @ Out, constraintXNonSelection, pyomo.expression, constraint on variable X
    """
    expr = sum(model.x[i,j] for j in model.optionsOut[i])
    # When Non-Selection is included, the following constraint should be used.
    return sum(model.x[i,j] for j in model.optionsOut[i]) == 1

  @staticmethod
  def constraintX(model,i):
    """
      sum of investments over knapsacks should less or equal than bounds
      @ In, model, instance, pyomo abstract model instance
      @ In, i, str, investment index
      @ Out, constraintX, pyomo.expression, constraint on variable X
    """
    expr = sum(model.x[i,j] for j in model.optionsOut[i])
    return sum(model.x[i,j] for j in model.optionsOut[i]) <= 1

  @staticmethod
  def consistentConstraintINonSelection(model, i, ip):
    """
      Constraint for variable y if priority project selection is required
      @ In, model, instance, pyomo abstract model instance
      @ In, i, str, investment index
      @ In, ip, str, investment index
      @ Out, consistentConstraintINonSelection, pyomo.expression, consistent constraint
    """
    if i == ip:
      return model.y[i,ip] == model.y[ip,i]
    else:
      # When Non-Selection is included, the following constraint should be used.
      lastIndexI = model.optionsOut[i].last()
      lastIndexIP = model.optionsOut[ip].last()
      try:
        mandatory = getattr(model, "mandatory")
        if ip in mandatory:
          expr1 = sum(model.x[ip,j] for j in model.optionsOut[ip]) + model.y[i,ip] - 1
        else:
          expr1 = sum(model.x[ip,j] for j in model.optionsOut[ip]) - model.x[ip,lastIndexIP] + model.y[i,ip] - 1
        if i in mandatory:
          expr2 = sum(model.x[i,j] for j in model.optionsOut[i])
        else:
          expr2 = sum(model.x[i,j] for j in model.optionsOut[i]) - model.x[i,lastIndexI]
      except (AttributeError, KeyError):
        expr1 = sum(model.x[ip,j] for j in model.optionsOut[ip]) - model.x[ip,lastIndexIP] + model.y[i,ip] - 1
        expr2 = sum(model.x[i,j] for j in model.optionsOut[i]) - model.x[i,lastIndexI]
      return expr1 <= expr2

  @staticmethod
  def consistentConstraintI(model, i, ip):
    """
      Constraint for variable y if priority project selection is required
      @ In, model, instance, pyomo abstract model instance
      @ In, i, str, investment index
      @ In, ip, str, investment index
      @ Out, consistentConstraintI, pyomo.expression, consistent constraint
    """
    if i == ip:
      return model.y[i,ip] == model.y[ip,i]
    else:
      expr1 = sum(model.x[ip,j] for j in model.optionsOut[ip]) + model.y[i,ip] - 1
      expr2 = sum(model.x[i,j] for j in model.optionsOut[i])
      return expr1 <= expr2

  @staticmethod
  def consistentConstraintII(model, i, ip, j):
    """
      This constraint is a type of consistency constraint with respect to the notion of options.
      @ In, model, instance, pyomo abstract model instance
      @ In, i, str, investment index
      @ In, ip, str, investment index
      @ In, j, str, option index
      @ Out, consistentConstraintII, pyomo.expression, consistent constraintII
    """
    if i != ip:
      expr1 = model.x[ip,j] + model.y[i,ip]-1
      expr2 = 0
      # we assume options are in order and their priority are also in order, i.e. from high to low
      for jp in model.optionsOut[i]:
        expr2 = expr2 + model.x[i,jp]
        if jp != j:
          continue
        else:
          break
      return expr1 <= expr2
    else:
      return pyomo.Constraint.Skip

  @staticmethod
  def consistentConstraintIINoSelection(model, i, ip, j):
    """
      This constraint is a type of consistency constraint with respect to the notion of options,
      including both non-selection and regulatory mandated options
      # The constraint matters only when project i is higher priority than project ip. In this case,
      # if we select Plan A for lower priority project then we must select plan A for the higher priority
      # project. If we select Plan B for the lower priority project then we can select Plan A and Plan B
      # for the higher priority project. And, if we select Plan C for the lower priority project then we
      # can select Plan A, B, C for the hight priority project. Inclusion of this constraint is "optional"
      # and reflects how the decision maker prefers to interpret the notion of priorities.
    """
    def consistentConstraintIIPart(model, i, ip, j):
      """
        This constraint is a type of consistency constraint with respect to the notion of options.
        @ In, model, instance, pyomo abstract model instance
        @ In, i, str, investment index
        @ In, ip, str, investment index
        @ In, j, str, option index
        @ Out, consistentConstraintIIPart, pyomo.expression, consistent constraintII
      """
      if i != ip:
        expr1 = model.x[ip,j] + model.y[i,ip]-1
        expr2 = 0
        # we assume options are in order and their priority are also in order, i.e. from high to low
        for jp in model.optionsOut[i]:
          expr2 = expr2 + model.x[i,jp]
          if jp != j:
            continue
          else:
            break
        return expr1 <= expr2
      else:
        return pyomo.Constraint.Skip
    lastIndexIP = model.optionsOut[ip].last()
    lastIndexI = model.optionsOut[i].last()
    try:
      mandatory = getattr(model, "mandatory")
      if ip in mandatory:
        return consistentConstraintIIPart(model, i, ip, j)
      else:
        if j == lastIndexIP:
          return pyomo.Constraint.Skip
        else:
          return consistentConstraintIIPart(model, i, ip, j)
    except (AttributeError, KeyError):
      if j == lastIndexIP:
        return pyomo.Constraint.Skip
      else:
        return consistentConstraintIIPart(model, i, ip, j)

  def initializeModel(self):
    """
      Initialize the pyomo model parameters for Knapsack problem (MCKP)
      @ In, None
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.initializeModel(self)
    model.options = pyomo.Set(dimen=2, ordered=True)
    model.resources = pyomo.Set()
    model.optionsOut = pyomo.Set(model.investments, initialize=self.optionsOutInit, ordered=True)
    # Set used for constraint (1j)
    model.investmentOption = pyomo.Set(dimen=2, initialize=self.investmentOptionInit, ordered=True)
    model.net_present_values = pyomo.Param(model.options, mutable=True)
    model.available_capitals = pyomo.Param(model.resources, model.time_periods, mutable=True)
    model.costs = pyomo.Param(model.options, model.resources, model.time_periods, mutable=True)
    return model

  def addConstraints(self, model):
    """
      Add specific constraints for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.addConstraints(self, model)
    # constraint (1d)
    model.constraintCapacity = pyomo.Constraint(model.resources, model.time_periods, rule=self.constraintCapacity)
    # constraint (1e) and (1f)
    # last option of any project will be denoted as "non-selection" option
    if self.mandatory is not None:
      model.constraintRegulatory = pyomo.Constraint(model.mandatory, rule=self.constraintRegulatory)
    # Special handles for required and DoNothing options
    # The options in input file can include DoNothing, but not required to be selected by optimization
    # Only the investment added to mandatory will be selected.
    # constraint to handle 'DoNothing' options --> (1f)
    if self.nonSelection:
      model.constraintX = pyomo.Constraint(model.investments, rule=self.constraintXNonSelection)
    else:
      model.constraintX = pyomo.Constraint(model.investments, rule=self.constraintX)
    # constraint for scenario analysis
    if self.uncertainties is not None:
      if self.nonSelection:
        # constraint (1c) --> optional
        model.consistentConstraintI = pyomo.Constraint(model.investments, model.investments, rule=self.consistentConstraintINonSelection)
        # constraint (1j) including both non-selection and regulatory mandated options
        model.consistentConstraintII = pyomo.Constraint(model.investments, model.investmentOption, rule=self.consistentConstraintIINoSelection)
      else:
        # constraint (1c) --> optional
        model.consistentConstraintI = pyomo.Constraint(model.investments, model.investments, rule=self.consistentConstraintI)
        # constraint (1j)
        model.consistentConstraintII = pyomo.Constraint(model.investments, model.investmentOption, rule=self.consistentConstraintII)
    return model

  def addVariables(self, model):
    """
      Add variables for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.addVariables(self, model)
    model.x = pyomo.Var(model.options, domain=pyomo.Binary)
    return model

  def addAdditionalSets(self, model):
    """
      Add specific Sets for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.addAdditionalSets(self, model)
    return model

  def addAdditionalParams(self, model):
    """
      Add specific Params for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.addAdditionalParams(self, model)
    return model

  def addExpressions(self, model):
    """
      Add specific expressions for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = KnapsackBase.addExpressions(self, model)
    return model

  def addAdditionalConstraints(self, model):
    """
      Add specific constraints for DROMCKP problems
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
    # second Stage
    secondStage = treeModel.Stages.last()
    treeModel.StageCost[secondStage] = 'secondStageCost'
    treeModel.StageVariables[secondStage].add('x[*,*]')
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
    for item in model.investments:
      for opt in model.optionsOut[item]:
        outputName = '__'.join([item,opt])
        numSelected = pyomo.value(model.x[item, opt])
        outputDict[outputName] = [numSelected]
        if numSelected == 1:
          msg = "Investment: " + str(item) + " with option: " + str(opt) + " is selected!"
          logger.info(msg)
    outputDict['MaxNPV'] = model.obj()
    logger.info("Maximum NPV: %16.4f" %(outputDict['MaxNPV']))
    # Accessing Duals
    # In some cases, a solver plugin will raise an exception if it encounters a Suffix type that it does not handle
    # One should be careful in verifying that Suffix declarations are being handled as expected when switching
    # to a different solver or solver interface.
    if self.solver == 'cbc':
      logger.info("Duals Information for Constraint Capacity:")
      print("Resources|Time_Periods      Capacity_Margin")
      for const in model.component_objects(pyomo.Constraint, active=True):
        if const.name == 'constraintCapacity':
          for index in const:
            print("{0:20s} {1:10.1f}".format(str(index), model.dual[const[index]]))
    return outputDict
