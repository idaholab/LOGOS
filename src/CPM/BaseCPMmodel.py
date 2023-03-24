# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
Created on March 14, 2023

@author: mandd
"""

#External Modules---------------------------------------------------------------
import abc
import numpy as np
import importlib.util
import sys
import os
#External Modules End-----------------------------------------------------------

#Internal Modules---------------------------------------------------------------
from ravenframework.PluginBaseClasses.ExternalModelPluginBase import ExternalModelPluginBase
from ravenframework.utils import InputData, InputTypes
from LOGOS.src.CPM.PertMain2 import Pert
from LOGOS.src.CPM.PertMain2 import Activity
#Internal Modules End-----------------------------------------------------------


class BaseCPMmodel(ExternalModelPluginBase):
  """
    This class is designed to create the base class for the CPM models
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    ExternalModelPluginBase.__init__(self)

    self.graph = None

  def _readMoreXML(self, container, xmlNode):
    """
      Method to read the portion of the XML that belongs to the CPM model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, xmlNode, xml.etree.ElementTree.Element, XML node that needs to be read
      @ Out, None
    """
    container.mapping = {}
    container.invMapping = {}

    for child in xmlNode:
      if child.tag == 'CPtime':
        container.CPtime = child.text.strip()
      elif child.tag == 'variables':
        variables = [str(var.strip()) for var in child.text.split(",")]
      elif child.tag == 'map':
        container.mapping[child.get('var')]      = child.text.strip()
        container.invMapping[child.text.strip()] = child.get('var')
      else:
        raise IOError("CMPmodel: xml node " + str(child.tag) + " is not allowed")

  def initialize(self, container, runInfoDict, inputFiles):
    """
      Method to initialize the CPM model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, runInfoDict, dict, dictionary containing all the RunInfo parameters (XML node <RunInfo>)
      @ In, inputFiles, list, list of input files (if any)
      @ Out, None
    """
    pass

  def createNewInput(self, container, inputs, samplerType, **Kwargs):
    """
      This function has been added for this model in order to be able to initialize a schedule project from file
      @ In, container, object, self-like object where all the variables can be stored
      @ In, myInput, list, the inputs (list) to start from to generate the new one
      @ In, samplerType, string, is the type of sampler that is calling to generate a new input
      @ In, **kwargs, dict,  is a dictionary that contains the information coming from the sampler,
           a mandatory key is the sampledVars'that contains a dictionary {'name variable':value}
      @ Out, ([(inputDict)],copy.deepcopy(kwargs)), tuple, return the new input in a tuple form
    """
    file2open = inputs[0].getFilename()
    spec = importlib.util.spec_from_file_location("project", str(file2open))
    importedModule = importlib.util.module_from_spec(spec)
    sys.modules["project"] = importedModule
    spec.loader.exec_module(importedModule)
    self.graph = importedModule.project.graph
    return Kwargs

  @abc.abstractmethod
  def run(self, container, inputDict):
    """
      This method calculates the CP of the schedule project and its end time
      @ In, container, object, self-like object where all the variables can be stored
      @ In, inputDict, dict, dictionary of inputs from RAVEN
    """
    inputDict = inputDict['SampledVars']
    for input in inputDict.keys():
      for act in self.graph.keys():
        if act.returnName()==container.mapping[input]:
          act.updateDuration(inputDict[input])
    
    self.pert = Pert(self.graph)
    
    end_time = self.pert.info_dict[self.pert.end_activity]['ef']
    container.__dict__[container.CPtime] = np.asarray(float(end_time))
    #CP       = self.pert.get_critical_path()
