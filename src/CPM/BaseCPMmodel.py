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
from operator import itemgetter

#External Modules End-----------------------------------------------------------

#Internal Modules---------------------------------------------------------------
from ravenframework.PluginBaseClasses.ExternalModelPluginBase import ExternalModelPluginBase
from ravenframework.utils import InputData, InputTypes
from LOGOS.src.CPM.pert import Pert
#Internal Modules End-----------------------------------------------------------


class BaseCPMmodel(ExternalModelPluginBase):
  """
    Base class for the critical path (CPM) model.

    A RAVEN ``ExternalModel`` plugin that loads a project schedule from a JSON
    file, applies RAVEN-sampled activity durations or priorities, computes the
    resource-constrained schedule, and reports the project completion time.
  """
  def __init__(self):
    ExternalModelPluginBase.__init__(self)

    self.project_file = None

    self.analysis = None # type of analysis to be performed in raven:
                         # 1) activity_duration: RAVEN sample acitivty duration values
                         # 2) activity_priority: RAVEN sample acitivty priority values
    self.sgs = None

    self.startTime = None # time when project schedule will start (datetime)
    self.resources = None # pandas dataframe of resource availability

    self.analysis = None # type of analysis to be performed in raven:
                         # 1) activity_duration: RAVEN sample acitivty duration values
                         # 2) activity_priority: RAVEN sample acitivty priority values
    self.sgs = None

  def _readMoreXML(self, container, xmlNode):
    """
      Read the portion of the XML input that belongs to the CPM model.

      Parses the ``project_file``, ``CPtime``, ``sgs`` and ``schema`` tags and
      each ``map`` element, which binds a RAVEN variable to an activity
      duration or priority.

      Parameters
      ----------
      container : object
        Self-like object where all the variables can be stored.
      xmlNode : xml.etree.ElementTree.Element
        XML node that needs to be read.

      Raises
      ------
      IOError
        If a ``map`` attribute is neither ``'duration'`` nor ``'priority'``, or
        if an unrecognised XML child node is encountered.
    """
    self.mapping = {}
    self.duration_vars = []
    self.priority_vars = []

    for child in xmlNode:
      if child.tag == 'project_file':
        self.project_file = child.text.strip()
      elif child.tag == 'CPtime':
        self.CPtime = child.text.strip()
      elif child.tag == 'sgs':
        self.sgs = child.text.strip()
      elif child.tag == 'schema':
        self.schema = child.text.strip()
      elif child.tag == 'map':
        # <map activity='activity_ID' attribute='duration/priority'>raven_var_ID</map>
        raven_var_ID = child.text.strip()
        act_ID       = child.get('act')
        attribute    = child.get('attr')
        self.mapping[raven_var_ID] = (act_ID,attribute)
        if attribute=='duration':
          self.duration_vars.append(raven_var_ID)
        elif attribute=='priority':
          self.priority_vars.append(raven_var_ID)
        else:
          raise IOError("CMPmodel: attribute " + str(attribute) + " is not allowed")

      elif child.tag.lower() in ['variables', 'inputs', 'outputs']:
        continue
      else:
        raise IOError("CMPmodel: xml node " + str(child.tag) + " is not allowed")


  def initialize(self, container, runInfoDict, inputFiles):
    """
      Initialize the CPM model.

      Loads the project schedule from ``project_file``, runs the connectivity
      and capacity debug checks, and generates the CPM information for the
      schedule graph.

      Parameters
      ----------
      container : object
        Self-like object where all the variables can be stored.
      runInfoDict : dict
        Dictionary containing all the RunInfo parameters (XML node ``<RunInfo>``).
      inputFiles : list
        List of input files (if any).
    """
    #Initialized once
    # 1) Load data & build schedule graph
    self.pert = Pert.from_json_file(self.project_file, schema_path=self.schema)

    # 1.1) debug situations with schedule
    self.pert.debug_connectivity_and_es()
    self.pert.debug_candidates_and_capacity(hours_ahead=48)

    self.pert.generateInfo()

  def run(self, container, inputDict):
    """
      Calculate the critical path of the scheduled project and its end time.

      Applies the RAVEN-sampled activity durations and priorities from
      ``inputDict``, computes the resource-constrained schedule, and stores the
      resulting project duration into ``container`` under the ``CPtime`` name.

      Parameters
      ----------
      container : object
        Self-like object where all the variables can be stored.
      inputDict : dict
        Dictionary of inputs from RAVEN.

      Raises
      ------
      IOError
        If a sampled duration or priority variable is missing from ``inputDict``.
    """
    try:
        inputDict_durations = dict(
            zip(self.duration_vars, itemgetter(*self.duration_vars)(inputDict))
        )
        # ↓ now also calls _sync_infodict_durations() + generateInfo() internally
        self.pert.set_durations(inputDict_durations)
    except KeyError as e:
        raise IOError(f"CPM Model: duration variable not found: {e}")

    try:
        inputDict_priorities = dict(
            zip(self.priority_vars, itemgetter(*self.priority_vars)(inputDict))
        )
        self.pert.set_priorities(inputDict_priorities, 'replace')
    except KeyError as e:
        raise IOError(f"CPM Model: priority variable not found: {e}")

    # ↓ _reset_scheduling_state() is called as the first thing inside here
    self.pert.calculateScheduleWithResources(self.sgs)

    endTime = self.pert.getProjectDuration()
    container.__dict__[self.CPtime] = np.asarray(float(endTime))

