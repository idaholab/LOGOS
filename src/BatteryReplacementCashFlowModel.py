"""
  Author:  C. Wang and D. Mandelli
  Date  :  07/12/2019
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

class BatteryReplacementCashFlowModel(ExternalModelPluginBase):
  ############################################################################
  #### Battery Replacement Cash Flow calculations for Nuclear Power Plant ####
  ############################################################################
  def _readMoreXML(self, container, xmlNode):
    """
      Method to read the portion of the XML that belongs to this plugin
      @ In, container, object, self-like object where all the variables can be stored
      @ In, xmlNode, xml.etree.ElementTree.Element, XML node that needs to be read
      @ Out, None
    """
    container.plannedReplacementCost = 70000
    container.unplannedReplacementCost = 350000
    container.batteryFailureProbability = 0.01
    container.numberBatteries = 1
    container.weeklyInspectionCost = 160
    container.batteryIncurringShutdownProbability = 0.05
    container.unitsCapacity = 1250 # MW
    container.unitsDowntimeCost = 6720000
    container.electricityMarginalCost = 32 #/MWh
    container.contributionFactor = {"hardSavings":1., "projectedSavings":0.9, "reliabilitySavings":0.8, "efficientSavings":0.65, "otherSavings":0.5}
    container.lifetime = 16
    container.startTime = 2019
    container.startMaintenanceTime = 2020
    container.endMaintenanceTime = 2034
    container.inflation = 0.015
    container.discountRate = 0.09
    container.tax = 0.
    container.paramList = ["plannedReplacementCost", "unplannedReplacementCost", "batteryFailureProbability", "numberBatteries", \
                          "weeklyInspectionCost", "batteryIncurringShutdownProbability", "unitsCapacity", "unitsDowntimeCost", \
                          "electricityMarginalCost", "inflation", "tax"]


    for child in xmlNode:
      if child.tag.strip() == "variables":
        # get verbosity if it exists
        container.variables = [var.strip() for var in child.text.split(",")]
      elif child.tag.strip() == "plannedReplacementCost":
        container.plannedReplacementCost = float(child.text)
      elif child.tag.strip() == "unplannedReplacementCost":
        container.unplannedReplacementCost = float(child.text)
      elif child.tag.strip() == "batteryFailureProbability":
        container.batteryFailureProbability = float(child.text)
      elif child.tag.strip() == "numberBatteries":
        container.numberBatteries = int(child.text)
      elif child.tag.strip() == "weeklyInspectionCost":
        container.weeklyInspectionCost = float(child.text)
      elif child.tag.strip() == "batteryIncurringShutdownProbability":
        container.batteryIncurringShutdownProbability = float(child.text)
      elif child.tag.strip() == "unitsCapacity":
        container.unitsCapacity = float(child.text)
      elif child.tag.strip() == "unitsDowntimeCost":
        container.unitsDowntimeCost = float(child.text)
      elif child.tag.strip() == "electricityMarginalCost":
        container.electricityMarginalCost = float(child.text)
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
      elif child.tag.strip() == "startMaintenanceTime":
        container.startMaintenanceTime = int(child.text)
      elif child.tag.strip() == "endMaintenanceTime":
        container.endMaintenanceTime = int(child.text)
      elif child.tag.strip() == "contributionFactor":
        for subElem in child:
          if subElem.tag.strip() in container.contributionFactor:
            container.contributionFactor[subElem.tag.strip()] = float(subElem.text)
          else:
            print("Node " + child.tag + " is not valid!")

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
    # output variables
    container.cashflows = np.zeros(len(container.time))
    container.survivalProbability = {}
    container.failureProbability = {}
    container.failureProbabilityAtTime = {}
    container.incurringShutdownProbabilityAtTime = {}

  def run(self, container, Inputs):
    """
      This method compute the cashflows of battery replacement case.
      @ In, container, object, self-like object where all the variables can be stored
      @ In, Inputs, dict, dictionary of inputs from RAVEN
    """
    for k, v in Inputs.items():
      if k in container.paramList:
        setattr(container, k, v)
    for time in container.time:
      if time == container.startTime:
        container.survivalProbability[time] = 1.0
        container.failureProbabilityAtTime[time] = 0.
        container.incurringShutdownProbabilityAtTime[time] = 0.
        container.failureProbability[time] = 0.
      else:
        container.survivalProbability[time] = (1-container.batteryFailureProbability)**(container.numberBatteries*(time-container.startTime))
        container.failureProbability[time] = 1. - container.survivalProbability[time]
        container.failureProbabilityAtTime[time] = container.failureProbability[time] - container.failureProbability[time-1]
        container.incurringShutdownProbabilityAtTime[time] = container.survivalProbability[time] * container.batteryIncurringShutdownProbability

    container.expectedReplacementCost = {}
    container.expectedInspectionCostsNoReplacement = {}
    container.expectedInspectionCostsWithReplacement = {}
    container.projectedSoftSaving = {}
    container.expectedLostRevenue = {}
    container.expectedDowntimeCost = {}
    container.reliabilitySoftSaving = {}
    container.totalHardSaving = {}
    container.totalSoftSaving = {}
    container.totalSaving = {}
    for i, time in enumerate(container.time):
      if time >= container.startMaintenanceTime and time <= container.endMaintenanceTime:
        container.expectedInspectionCostsNoReplacement[time] = container.numberBatteries * container.weeklyInspectionCost * container.survivalProbability[time] * 4. * 12.
        container.expectedInspectionCostsWithReplacement[time] = (1.-container.survivalProbability[time]) * container.weeklyInspectionCost * 12. * container.numberBatteries
      else:
        container.expectedInspectionCostsNoReplacement[time] = 0.
        container.expectedInspectionCostsWithReplacement[time] = 0.
      if time == container.startTime:
        # initial planned replacement cost
        container.expectedReplacementCost[time] = -(container.numberBatteries * container.plannedReplacementCost)
        container.expectedLostRevenue[time] = 0.
        container.expectedDowntimeCost[time] = 0.
      elif time < container.endTime:
        container.expectedReplacementCost[time] = container.failureProbabilityAtTime[time] * (container.unplannedReplacementCost*container.numberBatteries)
        container.expectedLostRevenue[time] = container.incurringShutdownProbabilityAtTime[time] * container.unitsCapacity * container.numberBatteries * container.electricityMarginalCost * 6.0
        container.expectedDowntimeCost[time] = container.unitsDowntimeCost * container.failureProbabilityAtTime[time]
      else:
        container.expectedReplacementCost[time] = container.survivalProbability[time] * container.plannedReplacementCost * container.numberBatteries
        container.expectedLostRevenue[time] = 0.
        container.expectedDowntimeCost[time] = 0.

      # compute savings
      container.projectedSoftSaving[time] = container.expectedInspectionCostsNoReplacement[time] - container.expectedInspectionCostsWithReplacement[time]
      container.reliabilitySoftSaving[time] = container.expectedLostRevenue[time] + container.expectedDowntimeCost[time]
      container.totalHardSaving[time] = container.expectedReplacementCost[time]
      container.totalSoftSaving[time] = container.projectedSoftSaving[time] * container.contributionFactor["projectedSavings"] + container.reliabilitySoftSaving[time] * container.contributionFactor["reliabilitySavings"]
      container.totalSaving[time] = container.totalHardSaving[time] + container.totalSoftSaving[time]
      container.cashflows[i] = container.totalSaving[time]
      container.time = np.asarray(container.time)

    # print("Expected Lost Revenue:")
    # print(container.expectedLostRevenue)
    # print("Expeced Downtime Cost:")
    # print(container.expectedDowntimeCost)
    #
    # print("Projected Soft Saving:")
    # print(container.projectedSoftSaving)
    # print("Reliability Saving:")
    # print(container.reliabilitySoftSaving)
    # print("Total Hard Saving:")
    # print(container.totalHardSaving)
    # print("Total Soft Saving:")
    # print(container.totalSoftSaving)
    # print("Total Saving:")
    # print(container.cashflows)

    # construct cashflow related objects
    #GlobalSettings
    verbosity = 100
    settings = CashFlows.GlobalSettings(verbosity=verbosity)
    paramDict = {}
    paramDict['DiscountRate'] = container.discountRate
    paramDict['tax'] = container.tax
    paramDict['inflation'] = container.inflation
    paramDict['projectTime'] = container.lifetime
    paramDict['Indicator'] = {'name':['NPV'], 'target':None, 'active':['Battery|Replacement']}
    settings.setParams(paramDict)

    #Cashflow, using Capex
    cashflow = CashFlows.Recurring(component='Battery', verbosity=verbosity)
    paramDict = {}
    paramDict['name'] = 'Replacement'
    paramDict['tax'] = container.tax
    paramDict['inflation'] = container.inflation
    paramDict['X'] = 1.
    paramDict['reference'] = 1.
    # paramDict['multiply'] = 1.
    paramDict['alpha'] = np.ones(len(container.cashflows))
    paramDict['driver'] = container.cashflows
    cashflow.setParams(paramDict)
    cashflow._yearlyCashflow = container.cashflows

    #Component
    component = CashFlows.Component(verbosity=verbosity)
    paramDict ={}
    paramDict['name'] = 'Battery'
    paramDict['Life_time'] = container.lifetime
    paramDict['StartTime'] = 0
    paramDict['Repetitions'] = 0
    paramDict['tax'] = container.tax
    paramDict['inflation'] = container.inflation
    paramDict['cash_flows'] = [cashflow]
    component.setParams(paramDict)

    # variables = {'TotalSaving':container.cashflows}
    # run the calculations, and compute NPV, IRR and PI
    metrics = main.run(settings, [component], {})
    for k, v in metrics.items():
      print("name: ", k)
      print("value: ", v)
      setattr(container, k, v)
