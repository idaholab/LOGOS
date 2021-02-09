# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
Created on February 2, 2021

@author: mandd
"""

#External Modules---------------------------------------------------------------
import numpy as np
import math
import copy
#External Modules End-----------------------------------------------------------

#Internal Modules---------------------------------------------------------------
from PluginsBaseClasses.ExternalModelPluginBase import ExternalModelPluginBase
from utils import InputData, InputTypes
#Internal Modules End-----------------------------------------------------------


class BaseKnapsackModel(ExternalModelPluginBase):
  """
    This class is designed to create the BaseKnapsack model
  """

  @classmethod
  def getInputSpecs(cls):
    """
      Collects input specifications for this class.
      @ In, None
      @ Out, inputSpecs, InputData, input specifications
    """
    inputSpecs = InputData.parameterInputFactory('ExternalModel')
    inputSpecs.addParam('name', param_type=InputTypes.StringType, required=True)
    inputSpecs.addParam('subType', param_type=InputTypes.StringType, required=True)
    inputSpecs.addSub(InputData.parameterInputFactory('capacity', contentType=InputTypes.StringType))
    inputSpecs.addSub(InputData.parameterInputFactory('penaltyFactor', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('outcome', contentType=InputTypes.StringType))
    inputSpecs.addSub(InputData.parameterInputFactory('choiceValue', contentType=InputTypes.StringType))
    inputSpecs.addSub(InputData.parameterInputFactory('variables', contentType=InputTypes.StringListType))
    map = InputData.parameterInputFactory('map', contentType=InputTypes.StringType)
    map.addParam('value', param_type=InputTypes.StringType, required=True)
    map.addParam('cost', param_type=InputTypes.StringType, required=True)
    inputSpecs.addSub(map)
    alias = InputData.parameterInputFactory('alias', contentType=InputTypes.StringType)
    alias.addParam('variable', param_type=InputTypes.StringType, required=True)
    alias.addParam('type', param_type=InputTypes.StringType, required=True)
    inputSpecs.addSub(alias)

    return inputSpecs

  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    ExternalModelPluginBase.__init__(self)

    self.capacity      = None    # capacity value of the knapsack
    self.penaltyFactor = 1.0     # penalty factor that is used when the capacity constraint is not satisfied
    self.outcome       = None    # ID of the variable which indicates if the chosen elements satisfy the capacity constraint
    self.choiceValue   = None    # ID of the variable which indicates the sum of the values of the chosen project elements

  def _readMoreXML(self, container, xmlNode):
    """
      Method to read the portion of the XML that belongs to the BaseKnapsack model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, xmlNode, xml.etree.ElementTree.Element, XML node that needs to be read
      @ Out, None
    """
    container.mapping    = {}

    specs = self.getInputSpecs()()
    specs.parseNode(xmlNode)
    for node in specs.subparts:
      name = node.getName()
      val = node.value
      if name == 'capacity':
        self.capacity = val
      elif name == 'penaltyFactor':
        self.penaltyFactor = val
      elif name == 'outcome':
        self.outcome = val
      elif name == 'choiceValue':
        self.choiceValue = val
      elif name == 'map':
        container.mapping[val] = [node.parameterValues['value'],node.parameterValues['cost']]
      elif name == 'variables':
        variables = val
      else:
        raise IOError("BaseKnapsackModel: xml node " + name + " is not allowed")

  def initialize(self, container, runInfoDict, inputFiles):
    """
      Method to initialize the BaseKnapsack model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, runInfoDict, dict, dictionary containing all the RunInfo parameters (XML node <RunInfo>)
      @ In, inputFiles, list, list of input files (if any)
      @ Out, None
    """
    pass


  def run(self, container, inputDict):
    """
      This method calculates the sum of the chosen element values and check if the capacity constraint
      is satisfied
      @ In, container, object, self-like object where all the variables can be stored
      @ In, inputDict, dict, dictionary of inputs from RAVEN
    """
    totalValue = 0.0
    capacity = inputDict[self.capacity][0]

    for key in container.mapping:
      if key in inputDict.keys() and inputDict[key] in [0.0,1.0]:
        if inputDict[key] == 1.0:
          capacity = capacity - inputDict[container.mapping[key][1]][0]
          if capacity >= 0:
            totalValue = totalValue + inputDict[container.mapping[key][0]]
          else:
            totalValue = totalValue - inputDict[container.mapping[key][0]] * self.penaltyFactor
        elif inputDict[key] == 0.0:
          pass
        else:
          raise IOError("BaseKnapsackModel: variable " + str(key) + " does not have a 0/1 value.")
      else:
        raise IOError("BaseKnapsackModel: variable " + str(key) + " is not found in the set of input variables.")
      
    if capacity>=0:
      container.__dict__[self.outcome] =  0.
    else:
      container.__dict__[self.outcome] = 1.

    container.__dict__[self.choiceValue] = totalValue
