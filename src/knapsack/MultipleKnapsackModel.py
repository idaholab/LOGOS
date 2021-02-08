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


class MultipleKnapsackModel(ExternalModelPluginBase):
  """
    This class is designed to create the MultipleKnapsack model
  """
  
  @classmethod
  def getInputSpecs(cls):
    """
      Collects input specifications for this class.
      @ In,  None
      @ Out, inputSpecs, InputData, input specifications
    """
    inputSpecs = InputData.parameterInputFactory('ExternalModel')
    inputSpecs.addParam('name'   , param_type=InputTypes.StringType, required=True)
    inputSpecs.addParam('subType', param_type=InputTypes.StringType, required=True)
    
    inputSpecs.addSub(InputData.parameterInputFactory('penaltyFactor', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('outcome'      , contentType=InputTypes.StringType), required=True)
    inputSpecs.addSub(InputData.parameterInputFactory('choiceValue'  , contentType=InputTypes.StringType), required=True)
    
    knapsack = InputData.parameterInputFactory('knapsack', contentType=InputTypes.StringType)
    knapsack.addParam('ID', param_type=InputTypes.StringType, required=True)
    inputSpecs.addSub(knapsack)
    
    mapping = InputData.parameterInputFactory('map', contentType=InputTypes.StringType)
    mapping.addParam('value', param_type=InputTypes.StringType, required=True)
    mapping.addParam('cost',  param_type=InputTypes.StringType, required=True)
    inputSpecs.addSub(mapping)
    
    return inputSpecs
    
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
    self.N             = None    # number of knapsacks
    
  def _readMoreXML(self, container, xmlNode):
    """
      Method to read the portion of the XML that belongs to the MultipleKnapsack model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, xmlNode, xml.etree.ElementTree.Element, XML node that needs to be read
      @ Out, None
    """
    container.mapping = {}
    self.knapsackSet  = {}
    
    for child in xmlNode:
      if child.tag == 'penaltyFactor':
        self.penaltyFactor = float(child.text.strip())
      elif child.tag == 'outcome':
        self.outcome = child.text.strip()
      elif child.tag == 'choiceValue':
        self.choiceValue = child.text.strip()
      elif child.tag == 'knapsack':
        self.knapsackSet[child.get('ID')] = child.text.strip()
      elif child.tag == 'map':
        container.mapping[child.text.strip()] = [child.get('value'),child.get('cost')]
      elif child.tag == 'variables':
        variables = [str(var.strip()) for var in child.text.split(",")]
      else:
        raise IOError("MultipleKnapsackModel: xml node " + str (child.tag) + " is not allowed")
      
      
  def initialize(self, container, runInfoDict, inputFiles):
    """
      Method to initialize the MultipleKnapsack model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, runInfoDict, dict, dictionary containing all the RunInfo parameters (XML node <RunInfo>)
      @ In, inputFiles, list, list of input files (if any)
      @ Out, None
    """
    pass      
      
      
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
      knapsackSetValues[knapsack] = inputDict[self.knapsackSet[knapsack]][0]
    
    # List of allowed knapsack IDs
    elementAllowedValues = list(map(float, self.knapsackSet.keys()))
    # Add 0.0 which implies that the element has not been assigned to any knapsack
    elementAllowedValues.append(0.0)
    
    for key in container.mapping:
      if key in inputDict.keys() and inputDict[key] in elementAllowedValues:
        if inputDict[key] > 0.0:
          knapsackChosen = str(int(inputDict[key][0]))
          testValue = knapsackSetValues[knapsackChosen] - inputDict[container.mapping[key][1]]
          if testValue >= 0:
            knapsackSetValues[knapsackChosen] = knapsackSetValues[knapsackChosen] - inputDict[container.mapping[key][1]][0]
            totalValue = totalValue + inputDict[container.mapping[key][0]]
          else:
            knapsackSetValues[knapsackChosen] = knapsackSetValues[knapsackChosen] - inputDict[container.mapping[key][1]][0]
            totalValue = totalValue - inputDict[container.mapping[key][0]] * self.penaltyFactor
      else:
        raise IOError("MultipleKnapsackModel: variable " + str(key) + " is either not found in the set of input variables or its values is not allowed.")
      
    numberUnsatConstraints = 0.0
    for knapsack in knapsackSetValues.keys():    
      if knapsackSetValues[knapsack] < 0:
        numberUnsatConstraints = numberUnsatConstraints + 1.
    
    if numberUnsatConstraints > 0.0 :
      container.__dict__[self.outcome] = 1.
    else:
      container.__dict__[self.outcome] = 0.       

    print(knapsackSetValues)      
    container.__dict__[self.choiceValue] = totalValue