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

  def writeOutput(self, filename):
    """
      Method used to output the optimization results
      @ In, filename, string, filename of output file
      @ Out, None
    """
    super().writeOutput(filename)



  @staticmethod
  def objExpression(model):
    """
      Method to compute objective expression
      @ In, model, instance, pyomo abstract model instance
      @ Out, objExpression, pyomo.expression, objective expression
    """
    return



  def initializeModel(self):
    """
      Initialize the pyomo model parameters
      @ In, None
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model = pyomo.AbstractModel()
    model.time_periods = pyomo.Set()
    model.tasks = pyomo.Set(ordered=True)
    return model

  def addObjective(self, model):
    """
      Add objective for MCKP problems
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    model.obj = pyomo.Objective(rule=self.objExpression, sense=self.sense)
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
