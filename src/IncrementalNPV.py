"""
  Author:  C. Wang and D. Mandelli
  Date  :  09/04/2019
"""
from __future__ import division, print_function , unicode_literals, absolute_import
import warnings
warnings.simplefilter('default', DeprecationWarning)

#External Modules---------------------------------------------------------------
import numpy as np
import math
import os
import sys
#External Modules End-----------------------------------------------------------

#Internal Modules---------------------------------------------------------------
from PluginsBaseClasses.ExternalModelPluginBase import ExternalModelPluginBase
#Internal Modules End-----------------------------------------------------------

#TEAL CashFlow modules----------------------------------------------------------
from TEAL.src import main
from TEAL.src import CashFlows
#TEAL CashFlow modules End------------------------------------------------------

class IncrementalNPV(ExternalModelPluginBase):
  ############################################################################
  #### Incremental NPV calculations for Nuclear Power Plant ####
  ############################################################################
  def _readMoreXML(self, container, xmlNode):
    """
      Method to read the portion of the XML that belongs to this plugin
      @ In, container, object, self-like object where all the variables can be stored
      @ In, xmlNode, xml.etree.ElementTree.Element, XML node that needs to be read
      @ Out, None
    """
    container.cp = None # planned cost of replacement
    container.cu = None # unplanned cost of replacement
    container.fp = None # probability of failure
    container.count = 1 # number of component
    container.cd = 1 # cost of shutdown per day
    container.d = None # days in shutdown
    container.td = None # number of years delayed for the planned cost of replacement
    container.hardSavings = 0 #TODO, how to include the hard saving into NPV calculations
    container.output = None # user defined output names of calculated npvs
    container.lifetime = 20
    container.startTime = 2019
    container.inflation = 0.
    container.discountRate = None
    container.tax = 0.
    container.paramList = ["cp", "cu", "fp", "cd", "inflation", "tax"] # list of params that can be perturbed by raven
    # container.contributionFactor = {"hardSavings":1., "projectedSavings":0.9, "reliabilitySavings":0.8, "efficientSavings":0.65, "otherSavings":0.5}
    self.name = xmlNode.attrib['name']
    for child in xmlNode:
      if child.tag.strip() == "variables":
        container.variables = [var.strip() for var in child.text.split(",")]
      elif child.tag.strip() == "Cp":
        container.cp = float(child.text)
      elif child.tag.strip() == "Cu":
        container.cu = float(child.text)
      elif child.tag.strip() == "fp":
        container.fp = float(child.text)
      elif child.tag.strip() == "count":
        container.count = int(child.text)
      elif child.tag.strip() == "Cd":
        container.cd = float(child.text)
      elif child.tag.strip() == "D":
        container.d = int(child.text)
      elif child.tag.strip() == "HardSavings":
        container.hardSavings = float(child.text)
      elif child.tag.strip() == "options":
        for elem in child:
          if elem.tag.strip() == "Td":
            container.td = list(int(val) for val in elem.text.split(','))
          elif elem.tag.strip() == "output":
            container.output = list(val.strip() for val in elem.text.split(','))
          else:
            raise IOError("Unrecognized input " + elem.tag)
      elif child.tag.strip() == "inflation":
        container.inflation = float(child.text)
      elif child.tag.strip() == "discountRate":
        container.discountRate = float(child.text)
      elif child.tag.strip() == "tax":
        container.tax = float(child.text)
      elif child.tag.strip() == "lifetime":
        container.lifetime = int(child.text)
      elif child.tag.strip() == "startTime":
        container.startTime = int(child.text)

  def initialize(self, container,runInfoDict,inputFiles):
    """
      Method to initialize this plugin
      @ In, container, object, self-like object where all the variables can be stored
      @ In, runInfoDict, dict, dictionary containing all the RunInfo parameters (XML node <RunInfo>)
      @ In, inputFiles, list, list of input files (if any)
      @ Out, None
    """
    container.endTime = container.startTime + container.lifetime
    container.time = list(range(container.startTime, container.endTime + 1))
    if container.td is None:
      container.td = [0]
      container.output = ['NPV']
    # output variables
    container.cashflows = np.zeros((len(container.td), len(container.time)))
    container.survivalProbability = {}
    container.failureProbabilityAtTime = {}
    container.failureProbability = {}

  def run(self, container, Inputs):
    """
      This method compute the cashflows of battery replacement case.
      @ In, container, object, self-like object where all the variables can be stored
      @ In, Inputs, dict, dictionary of inputs from RAVEN
    """
    componentName = self.name
    cashflowName = 'incremental'
    projectName = ['|'.join([componentName, cashflowName])]
    # construct cashflow related objects
    #GlobalSettings
    verbosity = 100
    settings = CashFlows.GlobalSettings(verbosity=verbosity)
    paramDict = {}
    paramDict['DiscountRate'] = container.discountRate
    paramDict['tax'] = container.tax
    paramDict['inflation'] = container.inflation
    paramDict['projectTime'] = container.lifetime
    paramDict['Indicator'] = {'name':['NPV'], 'target':None, 'active':projectName}
    settings.setParams(paramDict)

    #Cashflow, using Capex
    cashflow = CashFlows.Recurring(component=componentName, verbosity=verbosity)
    paramDict = {}
    paramDict['name'] = cashflowName
    paramDict['tax'] = container.tax
    paramDict['inflation'] = container.inflation
    paramDict['alpha'] = 1
    paramDict['X'] = 1.
    paramDict['reference'] = 1.
    # paramDict['multiply'] = 1.
    paramDict['driver'] = 1.
    cashflow.setParams(paramDict)
    cashflow._yearlyCashflow = None # reset using the calculated cashflow

    #Component
    component = CashFlows.Component(verbosity=verbosity)
    componentParams ={}
    componentParams['name'] = componentName
    componentParams['Life_time'] = container.lifetime
    componentParams['StartTime'] = 0
    componentParams['Repetitions'] = 0
    componentParams['tax'] = container.tax
    componentParams['inflation'] = container.inflation
    componentParams['cash_flows'] = None # reset using the calculated cashflow

    for k, v in Inputs.items():
      if k in container.paramList:
        setattr(container, k, v)
    for time in container.time:
      if time == container.startTime:
        container.survivalProbability[time-1] = 1.
        container.survivalProbability[time] = 1.0 - container.fp
        container.failureProbabilityAtTime[time] = container.fp
        container.failureProbability[time] = container.fp
      else:
        container.survivalProbability[time] = (1.-container.fp)**(container.count*(time-container.startTime + 1))
        container.failureProbability[time] = 1. - container.survivalProbability[time]
        container.failureProbabilityAtTime[time] = container.failureProbability[time] - container.failureProbability[time-1]

    for i, td in enumerate(container.td):
      container.expectedReplacementCost = {}
      container.expectedLostRevenue = {}
      for j, time in enumerate(container.time):
        delayedTime = container.startTime + td
        if delayedTime >= container.endTime:
          raise IOError("options for delayed time should not exceed the end liftime of given project")
        if time < delayedTime:
          container.expectedReplacementCost[time] = 0.
          container.expectedLostRevenue[time] = 0.
        if time == delayedTime:
          # initial planned replacement cost
          container.expectedReplacementCost[time] = -(container.count * container.cp) * container.survivalProbability[time-1] + container.failureProbabilityAtTime[time] * (container.cu*container.count)
          container.expectedLostRevenue[time] = container.cd * container.d * container.failureProbabilityAtTime[time] * container.count
        elif time > delayedTime and time < container.endTime:
          container.expectedReplacementCost[time] = container.failureProbabilityAtTime[time] * (container.cu*container.count)
          container.expectedLostRevenue[time] = container.cd * container.d * container.failureProbabilityAtTime[time] * container.count
        elif time == container.endTime:
          container.expectedReplacementCost[time] = container.survivalProbability[time-1] * container.cp * container.count
          container.expectedLostRevenue[time] = 0
        # compute savings
        container.cashflows[i,j] = container.expectedReplacementCost[time] + container.expectedLostRevenue[time]

      paramDict['alpha'] = np.ones(len(container.cashflows[i,:]))
      paramDict['driver'] = container.cashflows[i,:]
      cashflow.setParams(paramDict)
      cashflow._yearlyCashflow = container.cashflows[i,:]
      componentParams['cash_flows'] = [cashflow]
      component.setParams(componentParams)
      # variables = {'TotalSaving':container.cashflows}
      # run the calculations, and compute NPV, IRR and PI
      metrics = main.run(settings, [component], {})
      for k, v in metrics.items():
        print("name: ", k)
        # ToDo: now only add hard saving to npvs
        val = v + container.hardSavings
        print("value: ", val)
        setattr(container, container.output[i], val)
      # Debug
      # print("Expeced Lost Revenue:")
      # print(container.expectedLostRevenue)
      # print("Expected Replacement Cost:")
      # print(container.expectedReplacementCost)
      # print("Cashflow:")
      # print(container.cashflows)

    container.time = np.asarray(container.time)
