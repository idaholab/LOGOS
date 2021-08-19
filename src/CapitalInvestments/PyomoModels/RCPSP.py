# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
  Created on August 18, 2021
  @author: wangc, mandd
"""

#External Modules------------------------------------------------------------------------------------
import abc
import copy
import itertools
import numpy as np
import logging
import collections
from ast import literal_eval
import pyomo.environ as pyomo
#External Modules End--------------------------------------------------------------------------------

#Internal Modules------------------------------------------------------------------------------------
try:
  from LOGOS.src.CapitalInvestments.PyomoModels.ModelBase import ModelBase
except ImportError:
  from .ModelBase import ModelBase
#Internal Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

class RCPSP(ModelBase):
  """
    Resource-constrained projects scheduling problem (RCPSP)
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    super().__init__()
    self.paramsAuxInfo['available_resources'] = {'maxDim':1, 'options':[['None'], ['resources']]}
    self.paramsAuxInfo['task_resource_consumption'] = {'maxDim':2, 'options':[['tasks', 'resources']]}
    self.paramsAuxInfo['task_duration'] = {'maxDim':1, 'options':[['tasks']]}
    self.paramsAuxInfo['task_successors'] = {'maxDim':1, 'options':[['successors']]}
    self.paramsAuxInfo['makespan_upperbound'] = {'maxDim':1, 'options':[['None']]}

  def initialize(self, initDict):
    """
      Mehod to initialize
      @ In, initDict, dict, dictionary of preprocessed input data
        {
          'Sets':{setName: list of setValues},
          'Parameters':{paramName:{setsIndex:paramValue}} or {paramName:{'None':paramValue}},
          'Settings':{xmlTag:xmlVal},
          'Meta':{paramName:{setIndexName:indexDim}} or {paramName:None},
        }
      @ Out, None
    """
    super().initialize(initDict)

  def setSettings(self):
    """
      Method to process the settings of pyomo solver
      @ In, None
      @ Out, None
    """
    super().setSettings()

  def generateModelInputData(self):
    """
      This method is used to generate input data for pyomo model
      @ In, None
      @ Out, data, dict, input data for pyomo model
    """
    data = {}
    # Generate input set data
    indexList = ['tasks', 'resources', 'predecessors', 'successors']
    for indexName in indexList:
      data[indexName] = self.processInputSets(indexName, required=True)

    # set the Parameters with the extended indices
    for paramName, paramInfo in self.paramsAuxInfo.items():
      options = paramInfo['options']
      maxDim = paramInfo['maxDim']
      if paramName not in self.params.keys():
        raise IOError('Required node ' + paramName + ' is not found in input file, please specify it under node "Parameters"!')
      else:
        data[paramName] = self.setParameters(paramName, options, maxDim, self.params[paramName])
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
      else:
        raise IOError(f'Index {indexList} is not valid for parameter {paramName}, available indices are {options}!')
    elif maxDim == 2:
      indexList = list(self.meta['Parameters'][paramName].keys())
      if len(indexList) == 1 and indexList in options:
        pos = options.index(indexList)
        indices = [list(initParamDict.keys()),['None']] if pos == 1 else [['None'], list(initParamDict.keys())]
        indices = list(itertools.product(*indices))
        paramDict = dict(zip(indices,initParamDict.values()))
      elif len(indexList) == 2 and indexList in options:
        paramDict = initParamDict
      else:
        raise IOError(f'Index {indexList} is not valid for parameter {paramName}, available indices are {options}!')
    else:
      raise IOError('Not implemented for parameters with indices more than three!')
    if paramDict is None:
      msg = 'Please check the index for node ' + paramName + '! Options include: '
      msg += ','.join([str(ind) for ind in options]) + '\n'
      msg += 'Please make sure that they in the correct order!'
      msg += 'The following indices is provided by user: ' + ','.join(indexList)
      raise IOError(msg)
    return paramDict

  def writeOutput(self, filename):
    """
      Method used to output the optimization results
      @ In, filename, string, filename of output file
      @ Out, None
    """
    super().writeOutput(filename)

  def initializeModel(self):
    """
      Initialize the pyomo model parameters
      @ In, None
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = pyomo.AbstractModel()
    ### Sets
    model.tasks = pyomo.Set()
    model.resources = pyomo.Set()
    model.predecessors = pyomo.Set()
    model.successors = pyomo.Set(dimen=2)
    ### Params
    model.makespan_upperbound = pyomo.Param(within=NonNegativeIntegers)
    model.available_resources = pyomo.Param(model.resources)
    model.task_resource_consumption = pyomo.Param(model.tasks, model.resources)
    model.task_duration = pyomo.Param(model.tasks)
    model.task_successors = pyomo.Param(model.successors)
    model.time_periods = pyomo.RangeSet(1, model.makespan_upperbound)
    return model

  def addVariables(self, model):
    """
      Add variables for the problem
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model.x = pyomo.Var(model.tasks,model.time_periods, domain=NonNegativeIntegers, bounds=(0,1))

  @staticmethod
  def objExpression(model):
    """
      Method to compute objective expression
      @ In, model, instance, pyomo abstract model instance
      @ Out, objExpression, pyomo.expression, objective expression
    """
    return sum((t-1)*model.x[model.tasks.last(),t] for t in model.time_periods)

  def addObjective(self, model):
    """
      Add objective for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model.obj = pyomo.Objective(rule=self.objExpression, sense=self.sense)
    return model

  ########### start constraint functions
  @staticmethod
  def constraintX(model, j):
    return sum(model.x[j, t]  for t in model.time_periods) - 1 == 0

  # Constraint on predecessors
  @staticmethod
  def predecessorsConstraint(model, jp, p, t):
    return sum(model.x[model.successors[(jp,p)], tprime] for tprime in model.time_periods if tprime <= t) <= sum(model.x[jp, tprime] for tprime in model.time_periods if tprime <= t - model.task_duration[model.successors[(jp,p)]])

  #Constraint on resources
  @staticmethod
  def resourcesConstraint(model, r, t):
    return (0, sum(sum(model.task_resource_consumption[j, r] * model.x[j, tprime] for tprime in model.time_periods if tprime >= t and tprime <= t + model.task_duration[j]-1) for j in model.tasks), model.available_resources[r])

  ########### end constraint functions


  def addConstraints(self, model):
    """
      Add specific constraints for the problem
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model.constraintX = pyomo.Constraint(model.tasks, rule=constraintX)
    model.resourcesConstraint = pyomo.Constraint(model.resources, model.time_periods, rule=resourcesConstraint)
    model.predecessorsConstraint = pyomo.Constraint(model.successors, model.time_periods, rule=predecessorsConstraint)
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

  def printSolution(self, model):
    """
      Output optimization solution to screen
      @ In, model, instance, pyomo optimization model
      @ Out, outputDict, dict, dictionary stores the outputs
    """
    outputDict = super().printSolution(model)
    return outputDict
