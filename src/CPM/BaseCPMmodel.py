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
from datetime import datetime
import pandas as pd
import numpy as np

#External Modules End-----------------------------------------------------------

#Internal Modules---------------------------------------------------------------
from ravenframework.PluginBaseClasses.ExternalModelPluginBase import ExternalModelPluginBase
from ravenframework.utils import InputData, InputTypes
from LOGOS.src.CPM.pert import Pert
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

    self.project_file = None 

    self.analysis = None # type of analysis to be performed in raven:
                         # 1) activity_duration: RAVEN sample acitivty duration values
                         # 2) activity_priority: RAVEN sample acitivty priority values
    self.sgs = None

  def _readMoreXML(self, container, xmlNode):
    """
      Method to read the portion of the XML that belongs to the CPM model
      @ In, container, object, self-like object where all the variables can be stored
      @ In, xmlNode, xml.etree.ElementTree.Element, XML node that needs to be read
      @ Out, None
    """
    for child in xmlNode:
      if child.tag == 'project_file':
        self.project_file = child.text.strip()
      if child.tag == 'CPtime':
        self.CPtime = child.text.strip()
      elif child.tag == 'sgs':
        self.sgs = child.text.strip()
      elif child.tag == 'schema':
        self.schema = child.text.strip()
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
    # 1) Load data & build schedule graph
    self.pert = Pert.from_json_file(self.project_file, schema_path=self.schema)

    # 1.1) debug situations with schedule
    self.pert.debug_connectivity_and_es()
    self.pert.debug_candidates_and_capacity(hours_ahead=48)

    self.pert.generateInfo()


  def run(self, container, inputDict):
    """
      This method calculates the CP of the schedule project and its end time
      @ In, container, object, self-like object where all the variables can be stored
      @ In, inputDict, dict, dictionary of inputs from RAVEN
    """
    if self.analysis == 'activity_duration':
      pass
    elif self.analysis == 'activity_priority':
      priorityDict = self.parsePriorityValues(container, inputDict)
      self.pert.set_priorities(priorityDict, mode= "replace")
      self.pert.calculateScheduleWithResources(self.sgs)
      endTime = self.pert.getProjectDuration()
      container.__dict__[self.CPtime] = np.asarray(float(endTime))
    else:
      raise IOError("CPMmodel: Only activity_duration or activity_priority can be specified in the analysis node")

  def updateGraphValues(self, container, inputDict):
    """
      This method updates the duration value of a subset of activities in self.graph when
      the schedule graph is specified in a python class located in a separate file
      @ In, container, object, self-like object where all the variables can be stored
      @ In, inputDict, dict, dictionary of inputs from RAVEN
    """
    # To be modified

  def parsePriorityValues(self, container, inputDict):
    """
      This method updates the piority value of a subset of activities in self.graph when
      the schedule graph is specified in a python class located in a separate file
      @ In, container, object, self-like object where all the variables can be stored
      @ In, inputDict, dict, dictionary of inputs from RAVEN
    """
    priorityDict = {}
    inputDict = inputDict['SampledVars']
    for key in self.pert.returnGraphSymbolic.keys():
      if key in inputDict.keys():
        priorityDict[key] = inputDict[key.returnName()]
    return priorityDict
