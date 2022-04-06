# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
Created on February 2, 2021

@author: mandd
"""

#External Modules---------------------------------------------------------------
import abc
#External Modules End-----------------------------------------------------------

#Internal Modules---------------------------------------------------------------
from ravenframework.PluginBaseClasses.ExternalModelPluginBase import ExternalModelPluginBase
from ravenframework.utils import InputData, InputTypes
#Internal Modules End-----------------------------------------------------------


class BaseKnapsackModel(ExternalModelPluginBase):
  """
    This class is designed to create the base class for the knapsack models
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    ExternalModelPluginBase.__init__(self)

    self.penaltyFactor = 1.0     # penalty factor that is used when the capacity constraint is not satisfied
    self.outcome       = None    # ID of the variable which indicates if the chosen elements satisfy the capacity constraint
    self.choiceValue   = None    # ID of the variable which indicates the sum of the values of the chosen project elements


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

    inputSpecs.addSub(InputData.parameterInputFactory('penaltyFactor', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('outcome', contentType=InputTypes.StringType))
    inputSpecs.addSub(InputData.parameterInputFactory('choiceValue', contentType=InputTypes.StringType))
    inputSpecs.addSub(InputData.parameterInputFactory('variables', contentType=InputTypes.StringListType))

    mapping = InputData.parameterInputFactory('map', contentType=InputTypes.StringType)
    mapping.addParam('value', param_type=InputTypes.StringType, required=True)
    mapping.addParam('cost', param_type=InputTypes.StringType, required=True)

    alias = InputData.parameterInputFactory('alias', contentType=InputTypes.StringType)
    alias.addParam('variable', param_type=InputTypes.StringType, required=True)
    alias.addParam('type', param_type=InputTypes.StringType, required=True)

    inputSpecs.addSub(mapping)

    return inputSpecs


  def _readMoreXML(self, container, xmlNode):
    """
      Method to read the portion of the XML that belongs to the BaseKnapsack model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, xmlNode, xml.etree.ElementTree.Element, XML node that needs to be read
      @ Out, None
    """
    container.mapping = {}

    specs = self.getInputSpecs()()
    specs.parseNode(xmlNode)
    for node in specs.subparts:
      name = node.getName()
      val = node.value
      if name == 'penaltyFactor':
        self.penaltyFactor = val
      elif name == 'outcome':
        self.outcome = val
      elif name == 'choiceValue':
        self.choiceValue = val
      elif name == 'map':
        container.mapping[val] = [node.parameterValues['value'],node.parameterValues['cost']]
      elif name == 'variables':
        variables = val


  def initialize(self, container, runInfoDict, inputFiles):
    """
      Method to initialize the BaseKnapsack model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, runInfoDict, dict, dictionary containing all the RunInfo parameters (XML node <RunInfo>)
      @ In, inputFiles, list, list of input files (if any)
      @ Out, None
    """
    pass

  @abc.abstractmethod
  def run(self, container, inputDict):
    """
      This method calculates the sum of the chosen element values and check if the capacity constraint
      is satisfied
      @ In, container, object, self-like object where all the variables can be stored
      @ In, inputDict, dict, dictionary of inputs from RAVEN
    """
    pass
