#
#
#
"""
  Created on March. 19, 2019
  @author: wangc, mandd
"""

#for future compatibility with Python 3--------------------------------------------------------------
from __future__ import division, print_function, unicode_literals, absolute_import
import warnings
warnings.simplefilter('default',DeprecationWarning)
#End compatibility block for Python 3----------------------------------------------------------------

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
  from LOGOS.src.CapitalInvestments.PyomoModels.ModelBase import ModelBase
except ImportError:
  from .ModelBase import ModelBase
#Internal Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

class KnapsackBase(ModelBase):
  """
    Base class for methods used to solving knapsack problem
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    ModelBase.__init__(self)

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
    ModelBase.initialize(self, initDict)
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
    if self.regulatoryMandated is not None:
      data['regulatoryMandated'] = {None: self.regulatoryMandated}

    # set the Parameters with the extended indices
    for paramName, paramInfo in self.paramsAuxInfo.items():
      options = paramInfo['options']
      maxDim = paramInfo['maxDim']
      if paramName not in self.params.keys():
        raise IOError('Required node ' + paramName + ' is not found in input file, please specify it under node "Parameters"!')
      else:
        data[paramName] = self.setParameters(paramName, options, maxDim, self.params[paramName])

    ## used for DRO model
    if self.uncertainties is not None:
      logger.info('Generate additional model inputs for DRO optimization')
      data['sigma'] = {None:list(self.scenarios['probabilities'].keys())}
      data['prob'] = copy.copy(self.scenarios['probabilities'])
      data['epsilon'] = {None:self.epsilon}
      distData = copy.copy(self.distData[0,:])
      smIndices = list(self.scenarios['probabilities'].keys())
      data['dist'] = dict(zip(smIndices,np.ravel(distData)))

      # The following will not be used
      # indices = list(itertools.product(*[smIndices, smIndices]))
      # data['dist'] = dict(zip(indices,np.ravel(distData)))

    data = {None:data}
    return data

  def processInputSets(self, indexName):
    """
      Method to generate Set input for pyomo model
      @ In, indexName, str, name of index
      @ Out, dict, {None:[indexValue]}
    """
    if indexName not in self.sets.keys():
      return {None:['None']}
    else:
      return {None:self.sets[indexName]}

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
    for var, val in solutionGenerator:
      # value is stored as y[('i','j')], gamma, nu[]
      if var == 'gamma':
        logger.info('Variable "gamma": {}'.format(val))
      elif var.split('[')[0] == 'nu':
        logger.info('Variable "nu" at scenario "{}": {}'.format(var, val))
      else:
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
    ModelBase.printScenarioSolution(self, stsolver)
    self.collectPriorityOutputs(stsolver.root_Var_solution())
    obj = stsolver.root_E_obj()
    logger.info("Expecatation of NPV take over scenarios = %16.4f" %(obj))

  def writeOutput(self, filename):
    """
      Method used to output the optimization results
      @ In, filename, string, filename of output file
      @ Out, None
    """
    ModelBase.writeOutput(self,filename)
