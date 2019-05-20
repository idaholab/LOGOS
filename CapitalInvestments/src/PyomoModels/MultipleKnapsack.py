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
from PyomoModels.KnapsackBase import KnapsackBase
#Internal Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

class MultipleKnapsack(KnapsackBase):
  """
    Class model for multiple knapsack problem
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    KnapsackBase.__init__(self)

  def initialize(self, initDict):
    """
      Mehod to initialize
      @ In, initDict, dict, dictionary of preprocessed input data
        {'Sets':{}, 'Parameters':{}, 'Settings':{}, 'Meta':{}, 'Uncertainties':{}}
      @ Out, None
    """
    KnapsackBase.initialize(self, initDict)
    if 'capitals' not in self.sets.keys():
      raise IOError('Set capitals is required for %s problem' %self.name)

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
        dataList = []
        for scenarioData in scenarioDict['scenarios'].values():
          indices = [list(scenarioData.keys()), ['None']]
          indices = list(itertools.product(*indices))
          paramDict = dict(zip(indices, list(scenarioData.values())))
          dataList.append(paramDict)
        scenarioList.append(dataList)
      else:
        scenarioList.append(list(scenarioDict['scenarios'].values()))
      scenarioNameList.append(list(scenarioDict['scenarios'].keys()))
      scenarioProbList.append(scenarioDict['probabilities'])
      paramList.append(paramName)
    self.scenarios['params'] = paramList
    self.scenarios['scenario_name'] = dict(('scenario_' + str(i), name) for i, name in enumerate(list(itertools.product(*scenarioNameList)), 1))
    self.scenarios['probabilities'] = dict(('scenario_' + str(i), float(np.product(list(prob)))) for i, prob in enumerate(list(itertools.product(*scenarioProbList)), 1))
    self.scenarios['scenario_data'] = dict(('scenario_' + str(i), dict(zip(paramList, data))) for i, data in enumerate(list(itertools.product(*scenarioList)), 1))

  def generateModelInputData(self):
    """
      This method is used to generate input data for pyomo model
      @ In, None
      @ Out, data, dict, input data for pyomo model
    """
    data = KnapsackBase.generateModelInputData(self)
    paramName = 'available_capitals'
    options = [[None], ['capitals'], ['capitals','time_periods']]
    maxDim = 2
    data[paramName] = self.setParameters(paramName, options, maxDim)

    paramName = 'net_present_values'
    options = [[None], ['investments']]
    maxDim = 1
    data[paramName] = self.setParameters(paramName, options, maxDim)

    paramName = 'costs'
    options = [[None], ['investments'], ['investments', 'time_periods']]
    maxDim = 2
    data[paramName] = self.setParameters(paramName, options, maxDim)
    data = {None:data}
    return data

  def multipleKnapsackModel(self):
    """
      This method is used to create pyomo model.
      @ In, None
      @ Out, model, pyomo.AbstractModel, abstract pyomo model
    """
    model = pyomo.AbstractModel()
    model.time_periods = pyomo.Set()
    model.investments = pyomo.Set()
    model.capitals = pyomo.Set()
    model.net_present_values = pyomo.Param(model.investments, mutable=True)
    model.available_capitals = pyomo.Param(model.capitals, model.time_periods, mutable=True)
    model.costs = pyomo.Param(model.investments, model.time_periods, mutable=True)

    def boundsExpression(model, i, j):
      """ set the bounds for soluion variable x using lowerBounds and upperBounds"""
      return (self.lowerBounds[i], self.upperBounds[i])
    model.x = pyomo.Var(model.investments, model.capitals, domain=pyomo.NonNegativeIntegers, bounds=boundsExpression)

    def constraintX(model,i):
      """ sum of investments over knapsacks should less or equal than bounds """
      return (self.lowerBounds[i], sum(model.x[i,m] for m in model.capitals), self.upperBounds[i])
    model.constraintX = pyomo.Constraint(model.investments, rule=constraintX)

    def constraintCapacity(model, m, t):
      """Knapsacks capacity constraints"""
      return sum(model.costs[i,t]*model.x[i,m] for i in model.investments) <= model.available_capitals[m,t]
    model.constraintCapacity = pyomo.Constraint(model.capitals, model.time_periods, rule=constraintCapacity)

    if self.regulatoryMandated is not None:
      model.regulatoryMandated = pyomo.Set()
      def constraintRegulatory(model, i):
        """Regulatory constraints, always required projects/investments"""
        return sum(model.x[i,m] for m in model.capitals) == 1
      model.constraintRegulatory = pyomo.Constraint(model.regulatoryMandated, rule=constraintRegulatory)

    def computeFirstStageCost(model):
      """"Frist stage cost of stochastic programming"""
      return 0.0
    model.firstStageCost = pyomo.Expression(rule=computeFirstStageCost)

    def computeSecondStageCost(model):
      """Second stage cost of stochastic programming, i.e. maximum NPVs"""
      expr = sum(sum(model.net_present_values[i] * model.x[i,m] for i in model.investments) for m in model.capitals)
      return expr
    model.secondStageCost = pyomo.Expression(rule=computeSecondStageCost)

    if self.uncertainties is not None:
      model.y = pyomo.Var(model.investments, model.investments, domain=self.solutionVariableType)
      def orderConstraintI(model, i, j):
        """Constraint for variable y if priority project selection is required"""
        if i == j:
          return model.y[i,j] == model.y[j,i]
        else:
          return model.y[i,j] + model.y[j,i] >= 1
      model.orderConstraintI = pyomo.Constraint(model.investments, model.investments, rule=orderConstraintI)

      def consistentConstraint(model, i, j):
        """Constraint for variable y if priority project selection is required"""
        if i == j:
          return model.y[i,j] == model.y[j,i]
        else:
          return sum(model.x[j,m] for m in model.capitals) + model.y[i,j] - 1 <= sum(model.x[i,m] for m in model.capitals)
      model.consistentConstraint = pyomo.Constraint(model.investments, model.investments, rule=consistentConstraint)

      def constraintY(model, i):
        """Constraint for variable y if priority project selection is required"""
        return model.y[i,i] == 0
      model.constraintY = pyomo.Constraint(model.investments)

    def objExpression(model):
      """objective expression"""
      return model.firstStageCost + model.secondStageCost
    model.obj = pyomo.Objective(rule=objExpression, sense=self.sense)

    return model

  def createModel(self):
    """
      This method is used to create pyomo model.
      @ In, None
      @ Out, model, pyomo.AbstractModel, abstract pyomo model
    """
    model = self.multipleKnapsackModel()
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
    # second Stage
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
    outputDict.update({item:list() for item in model.investments})
    outputDict['capitals'] = list(cap for cap in model.capitals)
    for cap in model.capitals:
      for item in model.investments:
        numSelected = pyomo.value(model.x[item, cap])
        outputDict[item].append(numSelected)
        if numSelected == 1:
          msg = "Investment: " + str(item) + " is selected for capitals: " + str(cap)
          logger.info(msg)
        elif numSelected > 1:
          msg = "Investment: " + str(item) + " is selected with limit " + str(int(numSelected)) + " for capitals " + str(cap)
          logger.info(msg)
    logger.info("Maximum NPV: %16.4f" %(model.obj()))
    outputDict['MaxNPV'] = model.obj()

    # Accessing Duals
    # In some cases, a solver plugin will raise an exception if it encounters a Suffix type that it does not handle
    # One should be careful in verifying that Suffix declarations are being handled as expected when switching
    # to a different solver or solver interface.
    if self.solver == 'cbc':
      logger.info("Duals Information for Constraint Capacity:")
      print("Capitals|Time_Periods      Capacity_Margin")
      for const in model.component_objects(pyomo.Constraint, active=True):
        if const.name == 'constraintCapacity':
          for index in const:
            print("{0:20s} {1:10.1f}".format(str(index), model.dual[const[index]]))
    print(outputDict)
    return outputDict
