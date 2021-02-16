# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
Created on February 2, 2021

@author: mandd
"""

#External Modules---------------------------------------------------------------

#External Modules End-----------------------------------------------------------

#Internal Modules---------------------------------------------------------------
from PluginsBaseClasses.ExternalModelPluginBase import ExternalModelPluginBase
from utils import InputData, InputTypes
from BaseKnapsackModel import BaseKnapsackModel
#Internal Modules End-----------------------------------------------------------


class SimpleKnapsackModel(BaseKnapsackModel):
  """
    This class is designed to create the simple Knapsack model
  """
  
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    BaseKnapsackModel.__init__(self)
    self.capacity      = None    # capacity value of the knapsack


  @classmethod
  def getInputSpecs(cls):
    """
      Collects input specifications for this class.
      @ In, None
      @ Out, inputSpecs, InputData, input specifications
    """
    BaseKnapsackModel.getInputSpecs(self)
    inputSpecs = InputData.parameterInputFactory('ExternalModel')
    inputSpecs.addSub(InputData.parameterInputFactory('capacity', contentType=InputTypes.StringType))

    return inputSpecs


  def _readMoreXML(self, container, xmlNode):
    """
      Method to read the portion of the XML that belongs to the BaseKnapsack model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, xmlNode, xml.etree.ElementTree.Element, XML node that needs to be read
      @ Out, None
    """
    BaseKnapsackModel._readMoreXML(self, container, xmlNode)
    container.mapping = {}

    specs = self.getInputSpecs()()
    specs.parseNode(xmlNode)
    for node in specs.subparts:
      name = node.getName()
      val = node.value
      if name == 'capacity':
        self.capacity = val
 

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
