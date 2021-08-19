# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
  Created on March. 15, 2019
  @author: wangc, mandd
"""
#for future compatibility with Python 3--------------------------------------------------------------
from __future__ import division, print_function, unicode_literals, absolute_import
import warnings
warnings.simplefilter('default',DeprecationWarning)
#End compatibility block for Python 3----------------------------------------------------------------

#External Modules------------------------------------------------------------------------------------
import xml.etree.ElementTree as ET
import itertools
import numpy as np
import collections
import logging
import os
import pandas as pd
#External Modules End--------------------------------------------------------------------------------

#Internal Modules------------------------------------------------------------------------------------
try:
  from LOGOS.src.CapitalInvestments.investment_utils import investmentUtils as utils
except ImportError:
  from CapitalInvestments.investment_utils import investmentUtils as utils
#Internal Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def findRequiredNode(xmlNode, nodeTag):
  """
    Find the required xml node
    @ In, xmlNode, xml.etree.ElementTree.Element, xml element node
    @ In, nodeTag, str, node tag that is used to find the node
    @ Out, subnode, xml.etree.ElementTree.Element, xml element node
  """
  subnode = xmlNode.find(nodeTag)
  if subnode is None:
    raise IOError('Required node ' + nodeTag + ' is not found in the input file!')
  return subnode

def computeIndices(indexNameList, setsDict):
  """
    compute the required index that is used as input for pyomo model
    @ In, indexNameList, list, list of index name
    @ Out, indices, list, list of indices
  """
  indices = None
  if len(indexNameList) == 1:
    indices = setsDict[indexNameList[0]]
  else:
    indices = list(setsDict[indexName] for indexName in indexNameList)
    indices = list(itertools.product(*indices))
  return indices

def readSets(root, nodeTag):
  """
    Read xml node "Sets" in the input file
    @ In, root, xml.etree.ElementTree.Element, root xml element node
    @ In, nodeTag, str, node tag that is used to find the node
    @ Out, setsDict, dict, dictionary of input Sets, i.e. {setName: list of setValues}
  """
  logger.info('Read Sets information')
  sets = findRequiredNode(root, nodeTag)
  setsDict = {}
  metaDict = {}
  # read Sets information
  for subnode in sets:
    if subnode.get('index') is None:
      setsDict[subnode.tag] = utils.convertNodeTextToList(subnode.text)
    else:
      setsDict[subnode.tag] = utils.convertNodeTextToList(subnode.text,sep=';')
      metaDict[subnode.tag] = utils.convertNodeTextToList(subnode.get('index'))
  # process dependent Sets
  for setName, indexNameList in metaDict.items():
    indices = computeIndices(indexNameList, setsDict)
    if len(indices) != len(setsDict[setName]):
      msg = 'Provided data for node ' + setName + ' with length ' + str(len(setsDict[setName])) \
      + ' is not consistent with index ' + indexAttribName + ' with length ' + str(len(indices))
      raise IOError(msg)
    requiredIndex = []
    for i, subIndex in enumerate(setsDict[setName]):
      depIndex = indices[i]
      subIndices = utils.convertNodeTextToList(subIndex)
      if type(depIndex) == str:
        subIndices = list((depIndex, sub) for sub in subIndices)
      else:
        subIndices = list(depIndex + (sub,) for sub in subIndices)
      requiredIndex.extend(subIndices)
    setsDict[setName] = requiredIndex
  return setsDict

def readParameters(root, nodeTag, setsDict):
  """
    Read xml node "Parameters" in the input file
    @ In, root, xml.etree.ElementTree.Element, root xml element node
    @ In, nodeTag, str, node tag that is used to find the node
    @ In, setsDict, dict, dictionary store the sets info
    @ Out, (paramsDict, metaDict), tuple, paramsDict: {paramName:{setsIndex:paramValue}} or {paramName:{'None':paramValue}}
      metaDict: {paramName:{setIndexName:indexDim}} or {paramName:None}
  """
  logger.info('Read Parameters information')
  params = findRequiredNode(root, nodeTag)
  paramsDict = collections.OrderedDict()
  metaDict = collections.OrderedDict()
  for subnode in params:
    paramName = subnode.tag
    indexAttribName = subnode.get('index')
    contents = utils.convertNodeTextToFloatList(subnode.text)
    if indexAttribName is not None:
      indexNameList = utils.convertNodeTextToList(indexAttribName)
      indexDimList = list(len(setsDict[indexName]) for indexName in indexNameList)
      metaDict[paramName] = collections.OrderedDict(zip(indexNameList, indexDimList))
      indices = computeIndices(indexNameList, setsDict)
      if len(indices) != len(contents):
        msg = 'Provided data for node ' + subnode.tag + ' with length ' + str(len(contents)) \
        + ' is not consistent with index ' + indexAttribName + ' with length ' + str(len(indices))
        raise IOError(msg)
      paramsDict[paramName] = collections.OrderedDict(zip(indices,contents))
    else:
      if len(contents) == 1:
        paramsDict[paramName] = {'None':contents[0]}
        metaDict[paramName] = {None:1}
      ## TODO: make the index be required
      if 'investments' in setsDict:
        if len(contents) == len(setsDict['investments']) and paramName != 'available_capitals':
          paramsDict[paramName] = collections.OrderedDict(zip(setsDict['investments'], contents))
          metaDict[paramName] = {'investments':len(setsDict['investments'])}
        elif 'capitals' in setsDict.keys() and len(contents) == len(setsDict['capitals']):
          if paramName == 'available_capitals':
            paramsDict[paramName] = collections.OrderedDict(zip(setsDict['capitals'], contents))
            metaDict[paramName] = {'capitals':len(setsDict['capitals'])}
      else:
        raise IOError('Index is not provided, the text in node ' + tag + ' should be scalar!')
  return paramsDict, metaDict

def readUncertainties(root, nodeTag, paramsDict):
  """
    Read xml node "Uncertainties" in the input file
    @ In, root, xml.etree.ElementTree.Element, root xml element node
    @ In, nodeTag, str, node tag that is used to find the node
    @ In, paramsDict, dict, paramsDict returned by readParameters() method
    @ Out, uncertaintiesDict, dict, dictionary store uncertainties' information
      uncertaintiesDict: {paramName:{'scenarios':{scenarioName:{setIndex:uncertaintyVal}}, 'probabilities': [ProbVals]}}
  """
  logger.info('Read Uncertainties information')
  uncertainties = root.find(nodeTag)
  if uncertainties is not None:
    uncertaintiesDict = {}
    for subnode in uncertainties:
      paramName = subnode.tag
      if paramName not in paramsDict:
        raise IOError('Parameter ' + paramName + ' is not found in the Parameters list!')

      uncertaintiesDict[paramName] = {}
      totalScenarios = int(findRequiredNode(subnode, 'totalScenarios').text)
      probabilities = subnode.find('probabilities')
      if probabilities is None:
        uncertaintiesDict[paramName]['probabilities'] = [1.0/float(totalScenarios)] * totalScenarios
      else:
        uncertaintiesDict[paramName]['probabilities'] = utils.convertNodeTextToFloatList(probabilities.text)
      scenarios = findRequiredNode(subnode, 'scenarios')
      scenariosData = utils.convertNodeTextToFloatList(scenarios.text)
      indices = paramsDict[paramName].keys()
      if len(scenariosData) != len(indices) * totalScenarios:
        raise IOError('Provided length of scenarios data for ' + paramName + ': ' + str(len(scenariosData)) + \
         ' != "totalScenarios * dim(' + paramName + ')"' + ': ' + str(len(indices) * totalScenarios))
      indexDim = len(indices)
      scenariosNameList = []
      uncertaintiesDict[paramName]['scenarios'] = collections.OrderedDict()
      for i in range(1, totalScenarios+1):
        scenarioName = 'scenario_' + paramName + '_' + str(i)
        scenariosNameList.append(scenarioName)
        data = scenariosData[indexDim * (i - 1):indexDim * i]
        uncertaintiesDict[paramName]['scenarios'][scenarioName] = collections.OrderedDict(zip(indices, data))
    return uncertaintiesDict
  else:
    return None

def readSettings(root, nodeTag, workingDir):
  """
    Read xml node "Settings" in the input file
    @ In, root, xml.etree.ElementTree.Element, root xml element node
    @ In, nodeTag, str, node tag that is used to find the node
    @ Out, settingDict, dict, dictionary of settings {tag:val}
  """
  logger.info('Read Settings information')
  settings = findRequiredNode(root, nodeTag)
  settingDict = {}
  for subnode in settings:
    if subnode.tag == 'mandatory':
      settingDict[subnode.tag] = subnode.text.strip()
    elif subnode.tag == 'solverOptions':
      settingDict[subnode.tag] = {}
      for child in subnode:
        settingDict[subnode.tag][child.tag] = child.text.strip()
    elif subnode.tag == 'workingDir':
      settingDict[subnode.tag] = subnode.text.strip()
    else:
      settingDict[subnode.tag] = subnode.text.strip().lower()
  # set working dir
  if 'workingDir' in settingDict.keys():
    if settingDict['workingDir'] is None:
      raise IOError('"workingDir" is empty! Use "." to indicate "inputfile directory" or specify a directory!')
    tempDir = settingDict['workingDir']
    if '~' in tempDir:
      tempDir = os.path.expanduser(tempDir)
    if os.path.isabs(tempDir):
      settingDict['workingDir'] = tempDir
    else:
      settingDict['workingDir'] = os.path.join(workingDir, tempDir)
    utils.makeDir(settingDict['workingDir'])
  else:
    settingDict['workingDir'] = workingDir
  return settingDict

def readExternalConstraints(root, nodeTag):
  """
    Read xml node "ExternalConstraints" in the input file
    @ In, root, xml.etree.ElementTree.Element, root xml element node
    @ In, nodeTag, str, node tag that is used to find the node
    @ Out, constraintDict, dict, dictionary of settings {tag:val}
  """
  logger.info('Read ExternalConstraints information')
  constraints = root.find(nodeTag)
  constraintDict = {}
  if constraints is not None:
    for subnode in constraints:
      if subnode.tag == 'constraint':
        name = subnode.get('name')
        if name is not None and name not in constraintDict:
          constraintDict[name] = subnode.text.strip()
        elif name in constraintDict:
          raise IOError('Constraints with the same name "{}" are provided!'.format(name))
        else:
          raise IOError('Required attribute "name" for node "constraint" is not provided!')
  return constraintDict

def readEconomics(root, nodeTag, setsDict):
  """
    Read xml node "Economics" in the input file
    @ In, root, xml.etree.ElementTree.Element, root xml element node
    @ In, nodeTag, str, node tag that is used to find the node
    @ In, setsDict, dict, dictionary store the sets info
    @ Out, economicsDict, dict, dictionary of economics
      {paramName:{'CashFlow':pd.DataFrame, 'DiscountRate':float, 'tax':float, 'inflation':float}}
  """
  logger.info('Read Economics information')
  economics = root.find(nodeTag)
  if economics is not None:
    economicsDict = {}
    for paramNode in economics:
      paramDict = {}
      for nodeTag in ['DiscountRate', 'tax', 'inflation']:
        node = paramNode.find(nodeTag)
        paramDict[nodeTag] = utils.convertStringToFloat(node) if node is not None else 0.0
      cashFlow = findRequiredNode(paramNode, 'CashFlow')
      cashFlowVal = utils.convertNodeTextToFloatList(cashFlow.text)
      indexAttribName = cashFlow.get('index')
      transposing = False
      if indexAttribName is not None:
        indexNameList = utils.convertNodeTextToList(indexAttribName)
        if len(indexNameList) != 2:
          raise IOError('CashFlow node can only accepts two indices')
        elif indexNameList == ['investments','time_periods']:
          transposing = False
        elif indexNameList == ['time_periods','investments']:
          transposing = True
        else:
          raise IOError('CashFlow node can only accepts two indices, i.e. "time_periods" and "investments"')
      else:
        indexNameList = ['investments','time_periods']
      indexDimList = list(len(setsDict[indexName])+1 if indexName != 'investments' else len(setsDict[indexName]) \
                     for indexName in indexNameList)
      totDim = indexDimList[0] * indexDimList[1]
      if totDim != len(cashFlowVal):
        msg = 'Provided data for node ' + cashFlow.tag + ' with length ' + str(len(cashFlowVal)) \
        + ' is correct, should be ' + str(totDim)
        raise IOError(msg)
      cashFlowVal = np.asarray(cashFlowVal).reshape(indexDimList)
      columns = ['inflow'] + setsDict[indexNameList[1]]
      cashFlowDF = pd.DataFrame(cashFlowVal, index=setsDict[indexNameList[0]], columns=columns)
      paramDict['CashFlow'] = cashFlowDF if not transposing else cashFlowDF.T
      economicsDict[paramNode.tag] = paramDict
    return economicsDict
  else:
    return None

def computeNPVs(economicsDict):
  """
    Compute Net Present Values
    @ In, dict, dictionary of economics data
    @ Out, optionalParamDict, dict, store the calculated NPVs
  """
  logger.info('Compute NPVs')
  optionalParamDict = {}
  for paramName, paramDict in economicsDict.items():
    discounted = paramDict['DiscountRate']
    tax = paramDict['tax']
    inflation = paramDict['inflation']
    cashFlow = paramDict['CashFlow']
    ## compute cashflow after tax and inflation
    for ind, column in enumerate(cashFlow.columns):
      cashFlow[column] = cashFlow[column] * (1-tax) * (1+inflation)**(-ind)
    ## compute NPV
    npv = [np.npv(discounted, cashFlow.loc[ind]) for ind in cashFlow.index]
    optionalParamDict[paramName] = collections.OrderedDict(zip(list(cashFlow.index), npv))
    logger.debug('Computed npv for param %s: %s' %(paramName, str(npv)))

  return optionalParamDict

#####################################
# Input XML Reader
#####################################
def readInput(filename, workingDir='.'):
  """
    process input file
    @ In, filename, str or xml.etree.ElementTree.ElementTree, input filename
    @ In, workingDir, str, the working directory, '.' indicate current input file directory
    @ Out, initDict, dict, dictionary of inputs
    {
      'Sets':{setName: list of setValues},
      'Parameters':{paramName:{setsIndex:paramValue}} or {paramName:{'None':paramValue}},
      'Settings':{xmlTag:xmlVal},
      'Meta':{paramName:{setIndexName:indexDim}} or {paramName:None},
      'Uncertainties':{paramName:{'scenarios':{scenarioName:{setIndex:uncertaintyVal}}, 'probabilities': [ProbVals]}}
    }
  """

  if type(filename) == str:
    tree = ET.parse(filename)
    root = tree.getroot()
  elif isinstance(filename, ET.ElementTree):
    root = filename.getroot()
  elif isinstance(filename, ET.Element):
    root = filename
  else:
    root = filename
    #raise IOError('Unsupported type of input is provided: ' + str(type(filename)))
  initDict = {'Sets':None, 'Parameters':None, 'Settings':None, 'Meta': None,
              'Uncertainties': None, 'ExternalConstraints': None}
  metaData = {'Parameters':None}

  initDict['Sets'] = readSets(root, 'Sets')
  initDict['Parameters'], metaData['Parameters'] = readParameters(root, 'Parameters', initDict['Sets'])
  initDict['Uncertainties'] = readUncertainties(root, 'Uncertainties', initDict['Parameters'])
  initDict['Settings'] = readSettings(root, 'Settings', workingDir)
  initDict['ExternalConstraints'] = readExternalConstraints(root, 'ExternalConstraints')
  initDict['Meta'] = metaData

  economicsDict = readEconomics(root, 'Economics', initDict['Sets'])
  if economicsDict is not None:
    logger.info('Update NPVs from CashFlows listed in node Economics')
    optionalParamDict = computeNPVs(economicsDict)
    initDict['Parameters'].update(optionalParamDict)
    logger.debug('Information for parameters %s is updated' %(','.join(optionalParamDict.keys())))

  return initDict
