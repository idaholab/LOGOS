# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
Created on February 7, 2021

@author: mandd
"""

#External Modules---------------------------------------------------------------

#External Modules End-----------------------------------------------------------

#Internal Modules---------------------------------------------------------------
from ravenframework.PluginBaseClasses.ExternalModelPluginBase import ExternalModelPluginBase
from ravenframework.utils import InputData, InputTypes
from LOGOS.src.knapsack.BaseKnapsackModel import BaseKnapsackModel
#Internal Modules End-----------------------------------------------------------


class MultipleKnapsackModel(BaseKnapsackModel):
  """
    This class is designed to create the MultipleKnapsack model
  """

  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    BaseKnapsackModel.__init__(self)
    self.knapsackSet  = {}

  @classmethod
  def getInputSpecs(cls):
    """
      Collects input specifications for this class.
      @ In,  None
      @ Out, inputSpecs, InputData, input specifications
    """
    inputSpecs= BaseKnapsackModel.getInputSpecs()

    knapsack = InputData.parameterInputFactory('knapsack', contentType=InputTypes.StringType)
    knapsack.addParam('ID', param_type=InputTypes.StringType, required=True)
    inputSpecs.addSub(knapsack)
    return inputSpecs


  def _readMoreXML(self, container, xmlNode):
    """
      Method to read the portion of the XML that belongs to the MultipleKnapsack model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, xmlNode, xml.etree.ElementTree.Element, XML node that needs to be read
      @ Out, None
    """
    BaseKnapsackModel._readMoreXML(self, container, xmlNode)
    specs = self.getInputSpecs()()
    specs.parseNode(xmlNode)

    for node in specs.subparts:
      name = node.getName()
      val = node.value
      if name == 'knapsack':
        self.knapsackSet[node.parameterValues['ID']] = val


  def run(self, container, inputDict):
    """
      This method calculates the sum of the chosen element values and check if the capacity constraints
      for all knapsacks are satisfied
      @ In, container, object, self-like object where all the variables can be stored
      @ In, inputDict, dict, dictionary of inputs from RAVEN
    """
    totalValue = 0.0
    knapsackSetValues={}

    # knapsackSetValues is a dictionary in the form {knapsackID: knapsackValue}
    for knapsack in self.knapsackSet.keys():
      if self.knapsackSet[knapsack] in inputDict.keys():
        knapsackSetValues[knapsack] = inputDict[self.knapsackSet[knapsack]][0]
      else:
        raise IOError("MultipleKnapsackModel: variable " + str(self.knapsackSet[knapsack]) + " has not been found in the input dataObject.")

    # List of allowed knapsack IDs
    elementAllowedValues = list(map(float, self.knapsackSet.keys()))
    # Add 0.0 which implies that the element has not been assigned to any knapsack
    elementAllowedValues.append(0.0)

    numberUnsatConstraints = 0.0

    for key in container.mapping:
      if key in inputDict.keys() and inputDict[key] in elementAllowedValues:
        if inputDict[key] > 0.0:
          knapsackChosen = str(int(inputDict[key][0]))
          knapsackSetValues[knapsackChosen] = knapsackSetValues[knapsackChosen] - inputDict[container.mapping[key][1]][0]
          if knapsackSetValues[knapsackChosen] >= 0:
            totalValue = totalValue + inputDict[container.mapping[key][0]]
          else:
            totalValue = totalValue - inputDict[container.mapping[key][0]] * self.penaltyFactor
            numberUnsatConstraints = numberUnsatConstraints + 1.
      else:
        raise IOError("MultipleKnapsackModel: variable " + str(key) + " is either not found in the set of input variables or its values is not allowed.")

    if numberUnsatConstraints > 0.0 :
      container.__dict__[self.outcome] = 1.
    else:
      container.__dict__[self.outcome] = 0.

    container.__dict__[self.choiceValue] = totalValue
