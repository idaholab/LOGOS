# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
  Author:  C. Wang and D. Mandelli
  Date  :  07/12/2019
"""

#External Modules---------------------------------------------------------------
import numpy as np
import math
import os
import sys
#External Modules End-----------------------------------------------------------

#Internal Modules---------------------------------------------------------------
from ravenframework.utils import InputData, InputTypes
from ravenframework.PluginBaseClasses.ExternalModelPluginBase import ExternalModelPluginBase
#Internal Modules End-----------------------------------------------------------
from LOGOS.src._utils import getRavenLoc
ravenFrameworkPath = getRavenLoc()
sys.path.append(os.path.join(ravenFrameworkPath, '..', 'plugins'))
#TEAL CashFlow modules----------------------------------------------------------
from TEAL.src import main
from TEAL.src import CashFlows
#TEAL CashFlow modules End------------------------------------------------------

class BatteryReplacementCashFlowModel(ExternalModelPluginBase):
  ############################################################################
  #### Battery Replacement Cash Flow calculations for Nuclear Power Plant ####
  ############################################################################
  @classmethod
  def getInputSpecs(cls):
    """
      Collects input specifications for this class.
      @ In, None
      @ Out, inputSpecs, InputData, input specifications
    """
    inputSpecs = InputData.parameterInputFactory('ExternalModel')
    inputSpecs.addParam('name', param_type=InputTypes.StringType, required=True)
    inputSpecs.addParam('subType', param_type=InputTypes.StringType, required=True)
    inputSpecs.addSub(InputData.parameterInputFactory('variables', contentType=InputTypes.StringListType))
    inputSpecs.addSub(InputData.parameterInputFactory('plannedReplacementCost', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('unplannedReplacementCost', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('batteryFailureProbability', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('numberBatteries', contentType=InputTypes.IntegerType))
    inputSpecs.addSub(InputData.parameterInputFactory('weeklyInspectionCost', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('batteryIncurringShutdownProbability', contentType=InputTypes.FloatType))
    unitsCapacity = InputData.parameterInputFactory('unitsCapacity', contentType=InputTypes.FloatType)
    unitsCapacity.addParam('unit', param_type=InputTypes.StringType, required=False)
    inputSpecs.addSub(unitsCapacity)
    inputSpecs.addSub(InputData.parameterInputFactory('unitsDowntimeCost', contentType=InputTypes.FloatType))
    electricityMarginalCost = InputData.parameterInputFactory('electricityMarginalCost', contentType=InputTypes.FloatType)
    electricityMarginalCost.addParam('unit', param_type=InputTypes.StringType, required=False)
    inputSpecs.addSub(electricityMarginalCost)
    inputSpecs.addSub(InputData.parameterInputFactory('inflation', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('discountRate', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('tax', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('lifetime', contentType=InputTypes.IntegerType))
    inputSpecs.addSub(InputData.parameterInputFactory('startTime', contentType=InputTypes.IntegerType))
    inputSpecs.addSub(InputData.parameterInputFactory('startMaintenanceTime', contentType=InputTypes.IntegerType))
    inputSpecs.addSub(InputData.parameterInputFactory('endMaintenanceTime', contentType=InputTypes.IntegerType))
    contributionFactor = InputData.parameterInputFactory('contributionFactor')
    contributionFactor.addSub(InputData.parameterInputFactory('hardSavings', contentType=InputTypes.FloatType))
    contributionFactor.addSub(InputData.parameterInputFactory('projectedSavings', contentType=InputTypes.FloatType))
    contributionFactor.addSub(InputData.parameterInputFactory('reliabilitySavings', contentType=InputTypes.FloatType))
    contributionFactor.addSub(InputData.parameterInputFactory('efficientSavings', contentType=InputTypes.FloatType))
    contributionFactor.addSub(InputData.parameterInputFactory('otherSavings', contentType=InputTypes.FloatType))
    inputSpecs.addSub(contributionFactor)
    alias = InputData.parameterInputFactory('alias', contentType=InputTypes.StringType)
    alias.addParam('variable', param_type=InputTypes.StringType, required=True)
    alias.addParam('type', param_type=InputTypes.StringType, required=True)
    inputSpecs.addSub(alias)

    return inputSpecs

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

    specs = self.getInputSpecs()()
    specs.parseNode(xmlNode)
    for node in specs.subparts:
      name = node.getName()
      val = node.value
      if name == "variables":
        container.variables = val
      elif name == "plannedReplacementCost":
        container.plannedReplacementCost = val
      elif name == "unplannedReplacementCost":
        container.unplannedReplacementCost = val
      elif name == "batteryFailureProbability":
        container.batteryFailureProbability = val
      elif name == "numberBatteries":
        container.numberBatteries = val
      elif name == "weeklyInspectionCost":
        container.weeklyInspectionCost = val
      elif name == "batteryIncurringShutdownProbability":
        container.batteryIncurringShutdownProbability = val
      elif name == "unitsCapacity":
        container.unitsCapacity = val
      elif name == "unitsDowntimeCost":
        container.unitsDowntimeCost = val
      elif name == "electricityMarginalCost":
        container.electricityMarginalCost = val
      elif name == "inflation":
        container.inflation = val
      elif name == "discountRate":
        container.discountRate = val
      elif name == "tax":
        container.tax = val
      elif name == "lifetime":
        container.lifetime = val
      elif name == "startTime":
        container.startTime = val
      elif name == "startMaintenanceTime":
        container.startMaintenanceTime = val
      elif name == "endMaintenanceTime":
        container.endMaintenanceTime = val
      elif name == "contributionFactor":
        for subElem in node.subparts:
          if subElem.getName() in container.contributionFactor:
            container.contributionFactor[subElem.getName()] = subElem.value
          else:
            print("Node " + subElem.getName() + " is not valid!")

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
    if container.inflation >= 0.:
      paramDict['inflation'] = True
    else:
      paramDict['inflation'] = False
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
    indicators = settings.getIndicators()
    if len(indicators) !=1 and indicators[0] != 'NPV':
      raise IOError("Indicator should be set to {}, but got {}".format('NPV', indicators))
    for k, v in metrics.items():
      if k not in indicators:
        continue
      setattr(container, k, v)
