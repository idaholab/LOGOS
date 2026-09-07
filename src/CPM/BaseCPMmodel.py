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
    # Resolve project_file and schema against the RAVEN working directory.
    # RAVEN launches the input deck from the directory that contains it (e.g.
    # tests/), not from <WorkingDir>, so bare filenames must be anchored to the
    # working dir where the schedule JSON and schema are staged. Absolute paths
    # are honored as-is.
    workingDir = runInfoDict['WorkingDir']
    projectFile = self.project_file
    if projectFile is not None and not os.path.isabs(projectFile):
      projectFile = os.path.join(workingDir, projectFile)
    schemaPath = getattr(self, 'schema', None)
    if schemaPath is not None and not os.path.isabs(schemaPath):
      schemaPath = os.path.join(workingDir, schemaPath)

    #Initialized once
    # 1) Load data & build schedule graph
    self.pert = Pert.from_json_file(projectFile, schema_path=schemaPath)

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
        If a mapped RAVEN variable is missing from ``inputDict``.
    """
    # Translate each RAVEN-sampled variable into an {activity_id: value} entry
    # using the <map> table (self.mapping: {raven_var: (act_id, attr)}). The
    # dicts are keyed by activity ID -- as expected by set_durations() /
    # set_priorities() -- and each realization is coerced to a scalar float
    # (RAVEN delivers sampled values as ndarrays).
    def _scalar(raven_var):
        if raven_var not in inputDict:
            raise IOError(f"CPM Model: mapped variable not found: {raven_var}")
        return float(np.ravel(inputDict[raven_var])[0])

    durations, priorities = {}, {}
    for raven_var, (act_id, attribute) in self.mapping.items():
        if attribute == 'duration':
            durations[act_id] = _scalar(raven_var)
        elif attribute == 'priority':
            priorities[act_id] = _scalar(raven_var)

    # set_durations() also calls _sync_infodict_durations() + generateInfo()
    if durations:
        self.pert.set_durations(durations)
    if priorities:
        self.pert.set_priorities(priorities, 'replace')

    # ↓ _reset_scheduling_state() is called as the first thing inside here
    self.pert.calculateScheduleWithResources(self.sgs)

    endTime = self.pert.getProjectDuration()
    container.__dict__[self.CPtime] = np.asarray(float(endTime))

