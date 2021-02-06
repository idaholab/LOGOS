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
#Internal Modules End-----------------------------------------------------------


class MultipleKnapsackModel(ExternalModelPluginBase):
  """
    This class is designed to create the MultipleKnapsack model
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
    print(self.knapsackSet)
    knapsackSetValues={}
    
    # knapsackSetValues is a dictionary in the form {knapsackID: knapsackValue}
    for knapsack in self.knapsackSet.keys():
      knapsackSetValues[knapsack] = inputDict[self.knapsackSet[knapsack]][0]
    
    # List of allowed knapsack IDs
    elementAllowedValues = list(map(float, self.knapsackSet.keys()))
    # Add 0.0 which implies that element has not been assign to any knapsack
    elementAllowedValues.append(0.0)
    
    for key in container.mapping:
      if key in inputDict.keys() and inputDict[key] in elementAllowedValues:
        if inputDict[key] > 0.0:
          knapsackChosen = str(int(inputDict[key][0]))
          testValue = knapsackSetValues[knapsackChosen] - inputDict[container.mapping[key][1]]
          if testValue >= 0:
            knapsackSetValues[knapsackChosen] = knapsackSetValues[knapsackChosen] - inputDict[container.mapping[key][1]]
            totalValue = totalValue + inputDict[container.mapping[key][0]]
          else:
            knapsackSetValues[knapsackChosen] = knapsackSetValues[knapsackChosen] - inputDict[container.mapping[key][1]]
            totalValue = totalValue - inputDict[container.mapping[key][0]] * self.penaltyFactor
      else:
        raise IOError("MultipleKnapsackModel: variable " + str(key) + " is either not found in the set of input variables or its values is not allowed.")
      
    numberUnsatConstraints = 0.0
    for knapsack in container.mapping.keys():    
      if knapsackSetValues[knapsackChosen] < 0:
        numberUnsatConstraints = numberUnsatConstraints + 1.
    
    if numberUnsatConstraints > 0.0 :
      container.__dict__[self.outcome] = 1.
    else:
      container.__dict__[self.outcome] = 0.       
      
    container.__dict__[self.choiceValue] = totalValue