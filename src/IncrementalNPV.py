# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
  Author:  C. Wang and D. Mandelli
  Date  :  09/04/2019
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

#TEAL CashFlow modules----------------------------------------------------------
from TEAL.src import main
from TEAL.src import CashFlows
#TEAL CashFlow modules End------------------------------------------------------

class IncrementalNPV(ExternalModelPluginBase):
  ############################################################################
  #### Incremental NPV calculations for Nuclear Power Plant ####
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
    inputSpecs.addSub(InputData.parameterInputFactory('Cp', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('Cu', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('fp', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('count', contentType=InputTypes.IntegerType))
    inputSpecs.addSub(InputData.parameterInputFactory('Cd', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('D', contentType=InputTypes.IntegerType))
    inputSpecs.addSub(InputData.parameterInputFactory('HardSavings', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('inflation', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('discountRate', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('tax', contentType=InputTypes.FloatType))
    inputSpecs.addSub(InputData.parameterInputFactory('startTime', contentType=InputTypes.IntegerType))
    inputSpecs.addSub(InputData.parameterInputFactory('lifetime', contentType=InputTypes.IntegerType))
    options = InputData.parameterInputFactory('options')
    options.addSub(InputData.parameterInputFactory('Td', contentType=InputTypes.IntegerListType))
    options.addSub(InputData.parameterInputFactory('output', contentType=InputTypes.StringListType))
    inputSpecs.addSub(options)
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
    specs = self.getInputSpecs()()
    specs.parseNode(xmlNode)
    for node in specs.subparts:
      name = node.getName()
      val = node.value
      if name == "variables":
        container.variables = val
      elif name == "Cp":
        container.cp = val
      elif name == "Cu":
        container.cu = val
      elif name == "fp":
        container.fp = val
      elif name == "count":
        container.count = val
      elif name == "Cd":
        container.cd = val
      elif name == "D":
        container.d = val
      elif name == "HardSavings":
        container.hardSavings = val
      elif name == "options":
        for elem in node.subparts:
          if elem.getName() == "Td":
            container.td = elem.value
          elif elem.getName() == "output":
            container.output = elem.value
          else:
            raise IOError("Unrecognized input " + elem.tag)
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
    if container.inflation >= 0.:
      paramDict['inflation'] = True
    else:
      paramDict['inflation'] = False
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
      indicators = settings.getIndicators()
      if len(indicators) !=1 and indicators[0] != 'NPV':
        raise IOError("Indicator should be set to {}, but got {}".format('NPV', indicators))
      for k, v in metrics.items():
        if k not in indicators:
          continue
        print("name: ", k)
        # ToDo: now only add hard saving to npvs
        val = v + container.hardSavings
        print("value: ", val)
        setattr(container, container.output[i], val)

    container.time = np.asarray(container.time)
