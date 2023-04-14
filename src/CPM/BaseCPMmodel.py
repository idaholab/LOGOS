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
import copy
import inspect
#External Modules End-----------------------------------------------------------

#Internal Modules---------------------------------------------------------------
from ravenframework.PluginBaseClasses.ExternalModelPluginBase import ExternalModelPluginBase
from ravenframework.utils import InputData, InputTypes
from LOGOS.src.CPM.PertMain2 import Pert
from LOGOS.src.CPM.PertMain2 import Activity
#Internal Modules End-----------------------------------------------------------


class BaseCPMmodel(ExternalModelPluginBase):
  """
    This class is designed to create the base class for the critical path model (CPM)
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    ExternalModelPluginBase.__init__(self)

    self.graph  = None  # graph of the input schedule
    self.CPtime = None  # ID of the variable that indicates the time asscoated with the critical path (CP)
    self.CPid   = None  # ID of the variable that indicates the CP as sequence of activities/tasks
    self.mapping = {}   # dictionary containing the schedule graph from RAVEN xml input file
    self.pert = None    # graph of the imported schedule

  def _readMoreXML(self, container, xmlNode):
    """
      Method to read the portion of the XML that belongs to the CPM model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, xmlNode, xml.etree.ElementTree.Element, XML node that needs to be read
      @ Out, None
    """
    for child in xmlNode:
      if child.tag == 'CPtime':
        self.CPtime = child.text.strip()
      elif child.tag == 'CPid':
        self.CPid = child.text.strip()
      elif child.tag == 'variables':
        variables = [str(var.strip()) for var in child.text.split(",")]
      elif child.tag == 'map':
        if child.text is None:
          self.mapping[child.get('act')] = [child.get('dur'),[]]
        else:
          self.mapping[child.get('act')] = [child.get('dur'),child.text.split(",")]
      else:
        raise IOError("CMPmodel: xml node " + str(child.tag) + " is not allowed")

    if self.CPtime is None:
      raise IOError("CMPmodel: xml node CPtime has not been specified")
    if self.CPid is None:
      raise IOError("CMPmodel: xml node CPid has not been specified")

    # construction of the schedule graph from the RAVEN xml block
    actDict = {}
    if self.mapping:
      self.graph = {}
      for key in self.mapping.keys():
        actDict[key] = Activity(key,self.mapping[key][0])
        self.graph[actDict[key]] = []
      for key in self.mapping.keys():
        for elem in self.mapping[key][1]:
          self.graph[actDict[key]].append(actDict[elem])

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
    if self.mapping: # if the schedule graph is specified in the the RAVEN xml block
      return Kwargs
    else:            # if the schedule graph is specified in a python class located in a separate file
      file2open = inputs[0].getFilename()
      spec = importlib.util.spec_from_file_location("projectClass", str(file2open))
      importedModule = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(importedModule)

      if 'project' in list(zip(*inspect.getmembers(importedModule, inspect.isclass)))[0]:
        self.graph = importedModule.project.graph
      else:
        raise IOError("CPMmodel: class project has not been found in " +  str(file2open))
      return Kwargs

  def run(self, container, inputDict):
    """
      This method calculates the CP of the schedule project and its end time
      @ In, container, object, self-like object where all the variables can be stored
      @ In, inputDict, dict, dictionary of inputs from RAVEN
    """
    self.updateGraphValues(container, inputDict)

    self.pert = Pert(self.graph)  # initialize PERT class and perform numeric calculations
    endTime = self.pert.infoDict[self.pert.endActivity]['ef']

    # return CP time
    container.__dict__[self.CPtime] = np.asarray(float(endTime))
    # return CP (format string) as a sequence of activities separated by "_": start_act1_act2_act3_end
    container.__dict__[self.CPid]   = "_".join (map (str, self.pert.getCriticalPathSymbolic()))

  def updateGraphValues(self, container, inputDict):
    """
      This method updates the duration value of a subset of activities in self.graph when
      the schedule graph is specified in a python class located in a separate file
      @ In, container, object, self-like object where all the variables can be stored
      @ In, inputDict, dict, dictionary of inputs from RAVEN
    """
    inputDict = inputDict['SampledVars']
    for key in self.graph.keys():
      if key.returnName() in inputDict.keys():
        key.updateDuration(inputDict[key.returnName()])
      for elem in self.graph[key]:
        if elem.returnName() in inputDict.keys():
          elem.updateDuration(inputDict[key.returnName()])
