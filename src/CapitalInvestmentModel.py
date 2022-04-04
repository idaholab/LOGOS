# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
  Created on April. 30, 2019
  @author: wangc, mandd
"""

#External Modules------------------------------------------------------------------------------------
import os
import sys
import logging
import copy
import numpy as np
import xml.etree.ElementTree as ET
#External Modules End--------------------------------------------------------------------------------

#Internal Modules------------------------------------------------------------------------------------
from LOGOS.src.CapitalInvestments import PyomoModels
from LOGOS.src.CapitalInvestments.investment_utils import inputReader
#Internal Modules End--------------------------------------------------------------------------------

try:
  from ravenframework.PluginBaseClasses.ExternalModelPluginBase import ExternalModelPluginBase
except:
  raise IOError("ERROR (Initialization): RAVEN needs to be installed in order to use this External Model!'")

logging.basicConfig(format='%(asctime)s %(name)-20s %(levelname)-8s %(message)s', datefmt='%d-%b-%y %H:%M:%S', level=logging.DEBUG)
logger = logging.getLogger(__name__)

class CapitalInvestmentModel(ExternalModelPluginBase):
  """
    This class contains the plugin class for capital investments analysis within the RAVEN framework.
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    ExternalModelPluginBase.__init__(self)
    self.initDict = None
    self.xmlModelData = None
    self.modelInstance = None
    self.type = None
    self.workingDir = None
    self.name = self.__class__.__name__

  def _readMoreXML(self, container, xmlNode):
    """
      Method to read the portion of the XML that belongs to this plugin
      @ In, container, object, self-like object where all the variables can be stored
      @ In, xmlNode, xml.etree.ElementTree.Element, XML node that needs to be read
      @ Out, None
    """
    logger.info('Starting to process %s input', self.name)
    for child in xmlNode:
      if child.tag == 'ModelData':
        self.xmlModelData = copy.deepcopy(child)
      elif child.tag == 'variables':
        variables = [str(var.strip()) for var in child.text.split(",")]
      else:
        raise IOError(self.name + ": xml node " + child.tag + " is not allowed!")
    logger.info('End of process %s input', self.name)

  def initialize(self, container,runInfoDict,inputFiles):
    """
      Method to initialize this plugin
      @ In, container, object, self-like object where all the variables can be stored
      @ In, runInfoDict, dict, dictionary containing all the RunInfo parameters (XML node <RunInfo>)
      @ In, inputFiles, list, list of input files (if any)
      @ Out, None
    """
    settings = self.xmlModelData.find('Settings')
    if settings is None:
      problemType = 'SingleKnapsack'
      logger.info('Set problem type to default: %s', problemType)
    else:
      problemType = settings.find('problem_type')
      problemType = problemType.text.strip() if problemType is not None else 'SingleKnapsack'
      logger.info('Set problem type to: %s', problemType)
    logger.info('Starting to create Optimization Instance')
    self.modelInstance = PyomoModels.returnInstance(problemType)
    self.workingDir = runInfoDict['WorkingDir']

  def createNewInput(self, container, inputs, samplerType, **Kwargs):
    """
      This function has been added for this model in order to be able to create an ETStructure from multiple files
      @ In, container, object, self-like object where all the variables can be stored
      @ In, inputs, list, the inputs (list) to start from to generate the new one
      @ In, samplerType, string, is the type of sampler that is calling to generate a new input
      @ In, **kwargs, dict,  is a dictionary that contains the information coming from the sampler,
           a mandatory key is the sampledVars'that contains a dictionary {'name variable':value}
      @ Out, inputDict, dict, return the new input in a dict form
    """
    newXml = copy.deepcopy(self.xmlModelData)
    paramNode = newXml.find('Parameters')
    for child in paramNode:
      if child.tag in Kwargs['SampledVars']:
        if isinstance(Kwargs['SampledVars'][child.tag],(list, np.ndarray)):
          child.text = ','.join(str(var) for var in Kwargs['SampledVars'][child.tag])
        else:
          child.text = str(Kwargs['SampledVars'][child.tag])
      # check the text of each child
      else:
        pertData = []
        if ',' in child.text:
          listData = list(elem.strip() for elem in child.text.split(','))
        else:
          listData = list(elem.strip() for elem in child.text.split())
        for var in listData:
          if var in Kwargs['SampledVars']:
            pertData.append(Kwargs['SampledVars'][var])
          else:
            pertData.append(var)
        if len(pertData) > 1:
          child.text = ','.join(str(var) for var in pertData)
        elif len(pertData) == 1:
          child.text = str(pertData[0])
    inputDict = inputReader.readInput(newXml, self.workingDir)
    return inputDict

  def run(self, container, inputDict):
    """
      This is a simple example of the run method in a plugin.
      @ In, container, object, self-like object where all the variables can be stored
      @ In, inputDict, dict, dictionary of inputs from createNewInput
    """
    logger.info('Optimization Instance: %s is successfully created', self.modelInstance.name)
    logger.info('Starting to initialize Optimizer Instance: %s', self.modelInstance.name)
    self.modelInstance.initialize(inputDict)

    logger.info('Optimization Instance: %s is successfully intialized',self.modelInstance.name)
    logger.info('Starting to run Optimization Instance: %s', self.modelInstance.name)
    # TODO: run method should be modified to return output dictionary for raven to retrieve information
    outputDict = self.modelInstance.run()
    logger.info('Optimization Instance: %s is successfully optimized', self.modelInstance.name)
    # store requested information into container
    for key, val in outputDict.items():
      container.__dict__[key] = np.atleast_1d(val)
    logger.info("Run complete!")
