# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
  Created on Aug. 18, 2021
  @author: wangc, mandd
"""

#External Modules------------------------------------------------------------------------------------
import abc
import itertools
import numpy as np
import logging
import copy
import collections
import six
from six import iterkeys, iteritems, itervalues
import pyomo.environ as pyomo
from pyomo.opt import SolverFactory, TerminationCondition
import pyomo.pysp.util.rapper as rapper
from pyomo.pysp.scenariotree.tree_structure_model import CreateAbstractScenarioTreeModel
#External Modules End--------------------------------------------------------------------------------

#Internal Modules------------------------------------------------------------------------------------
try:
  from LOGOS.src.CapitalInvestments.investment_utils import investmentUtils as utils
  from LOGOS.src.CapitalInvestments.investment_utils import distanceUtils
  from LOGOS.src.CapitalInvestments.PyomoModels.ModelBase import ModelBase
except ImportError:
  from CapitalInvestments.investment_utils import investmentUtils as utils
  from CapitalInvestments.investment_utils import distanceUtils
  from .ModelBase import ModelBase
#Internal Modules End--------------------------------------------------------------------------------

# check pyomo version
import pyomo as pyo
pyoVersion = False
version = list(int(val) for val in pyo.__version__.split('.'))
version = version[0]*10 + version[1]
if version >= 57:
  pyoVersion = True

logger = logging.getLogger(__name__)

class PySPBase(ModelBase):
  """
    Base class for methods used to solving optimization problem
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    super().__init__()
    ## additional info needed by stochastic optimization
    self.meta = None            # additional info
    self.scenariosData = None   # containers for scenarios/uncertainties input data
    self.uncertainties = None   # uncertainty info provided by users
    self.scenarios = {}         # dictionary contains scenarios information generated from self.uncertainties
    self.optionalConstraints = {} # dictionary of optional constraints that users can turn on or off, i.e. {consistentConstraintI:True}
    self.phopts = {} # options for progressive hedging method
    self.phopts['--output-solver-log'] = None
    self.phopts['--max-iterations'] = 3
    self.phopts['--linearize-nonbinary-penalty-terms'] = 4
    self.phRho = 1
    self.stochSolver = 'ef'     # stochastic solver, default runef, can be switched to runph method in pyomo.
    ## used for distributionally robust optimization
    self.epsilon = 0.0 # specify the radius of radius ambiguity for distributionally robust optimization
    self.sigma = [] # list of scenario names
    self.prob = []  # list of probabilities
    self.distData = None # 2-D distance array, representing the pairwised distance between scenarios
    ## used for CVaR optimization
    self._lambda = 0.0 # risk aversion
    self.alpha = 0.95 # confidence level

  def initialize(self, initDict):
    """
      Mehod to initialize
      @ In, initDict, dict, dictionary of preprocessed input data
        {
          'Sets':{setName: list of setValues},
          'Parameters':{paramName:{setsIndex:paramValue}} or {paramName:{'None':paramValue}},
          'Settings':{xmlTag:xmlVal},
          'Meta':{paramName:{setIndexName:indexDim}} or {paramName:None},
          'Uncertainties':{paramName:{'scenarios':{scenarioName:{setIndex:uncertaintyVal}}, 'probabilities': [ProbVals]}}
        }
      @ Out, None
    """
    super().initialize(initDict)
    self.uncertainties = initDict.pop('Uncertainties', None)
    if self.uncertainties is not None:
      self.setScenarioData()
      if 'DRO' in self.name:
        self.distData = distanceUtils.computeDist('minkowski', self.scenarios['scenario_data'])
        ## Add distance scenario data into self.scenarios
        distData = copy.copy(self.distData)
        smIndices = list(self.scenarios['probabilities'].keys())
        for sm, paramDict in self.scenarios['scenario_data'].items():
          i = int(sm.split('_')[-1]) - 1
          paramDict['dist'] = dict(zip(smIndices, np.ravel(distData[i,:])))

  def setScenarioData(self):
    """
      Method to setup the scenario data for scenario tree construction
      @ In, None
      @ Out, None
    """
    # uncertainties for multiple parameters
    logger.info('Initialize Uncertainties for Optimization Instance: %s', self.name)
    self.scenarios = {}
    scenarioList = []
    scenarioNameList = []
    scenarioProbList = []
    paramList = []
    for paramName, scenarioDict in self.uncertainties.items():
      scenarioList.append(list(scenarioDict['scenarios'].values()))
      scenarioNameList.append(list(scenarioDict['scenarios'].keys()))
      scenarioProbList.append(scenarioDict['probabilities'])
      paramList.append(paramName)
    self.scenarios['params'] = paramList
    self.scenarios['scenario_name'] = dict(('scenario_' + str(i), name) for i, name in enumerate(list(itertools.product(*scenarioNameList)), 1))
    self.scenarios['probabilities'] = dict(('scenario_' + str(i), float(np.product(list(prob)))) for i, prob in enumerate(list(itertools.product(*scenarioProbList)), 1))
    self.scenarios['scenario_data'] = dict(('scenario_' + str(i), dict(zip(paramList, data))) for i, data in enumerate(list(itertools.product(*scenarioList)), 1))

  def setSettings(self):
    """
      Method to process the settings of pyomo solver
      @ In, None
      @ Out, None
    """
    super().setSettings()
    solverOptions = self.settings.pop('solverOptions', {})
    self.executable = solverOptions.pop('executable', None)
    stochSolver = solverOptions.pop('StochSolver', None)
    if stochSolver is not None:
      self.stochSolver = stochSolver.lower().strip()
    ## used for DRO
    # TODO: check provided epsilon is float
    self.epsilon = float(solverOptions.pop('radius_ambiguity', 0.0))
    ## used for CVaR
    self._lambda = float(solverOptions.pop('risk_aversion', 0.0))
    self.alpha = float(solverOptions.pop('confidence_level', 0.95))
    self.sopts.update(solverOptions)
    for optCon in self.optionalConstraints:
      if optCon == 'consistentConstraintI':
        self.optionalConstraints[optCon] = utils.convertStringToBool(self.settings.pop(optCon, 'True'))
      else:
        self.optionalConstraints[optCon] = utils.convertStringToBool(self.settings.pop(optCon, 'False'))

  def createModel(self):
    """
      This method is used to create pyomo model.
      @ In, None
      @ Out, model, pyomo.AbstractModel, abstract pyomo model
    """
    model = super().createModel()
    return model

  def createInstance(self, data):
    """
      This method is used to instantiate the pyomo model
      @ In, data, dict, dictionary to initialize pyomo abstract model
      @ Out, model, pyomo.instance, instance of pyomo model
    """
    model = super().createInstance(data)
    # Default to disable some optional constraints
    if len(self.optionalConstraints) > 0 and self.uncertainties is not None:
      # if 'consistentConstraintI' in self.optionalConstraints:
      #   model.consistentConstraint.deactivate()
      if 'consistentConstraintII' in self.optionalConstraints:
        model.consistentConstraintII.deactivate()
        logger.debug('Default to deactivate consistent constraint II')
      for optCon, decision in self.optionalConstraints.items():
        if optCon == 'consistentConstraintI' and not decision:
          model.consistentConstraintI.deactivate()
          logger.debug('Deactivate consistent constraint I')
        if optCon == 'consistentConstraintII' and decision:
          model.consistentConstraintII.activate()
          logger.debug('Activate consistent constraint II')
    return model

  def createScenarioTreeModel(self):
    """
      Construct scenario tree based on abstract scenario tree model for stochastic programming
      @ In, None
      @ Out, treeModel, Instance, pyomo scenario tree model
    """
    treeModel = CreateAbstractScenarioTreeModel()
    if pyoVersion:
      treeModel = treeModel.create_instance()
    treeModel.Stages.add('FirstStage')
    treeModel.Stages.add('SecondStage')
    treeModel.Nodes.add('RootNode')
    for i in self.scenarios['scenario_name']:
      leafNode = 'leaf_' + i
      treeModel.Nodes.add(leafNode)
      treeModel.Scenarios.add(i)
    if not pyoVersion:
      treeModel = treeModel.create_instance()
    treeModel.NodeStage['RootNode'] = 'FirstStage'
    treeModel.ConditionalProbability['RootNode'] = 1.0
    for node in treeModel.Nodes:
      if node != 'RootNode':
        treeModel.NodeStage[node] = 'SecondStage'
        treeModel.Children['RootNode'].add(node)
        treeModel.Children[node].clear()
        treeModel.ConditionalProbability[node] = self.scenarios['probabilities'][node.replace('leaf_','')]
        treeModel.ScenarioLeafNode[node.replace('leaf_','')] = node

    return treeModel

  @abc.abstractmethod
  def pysp_scenario_tree_model_callback(self):
    """
      scenario tree instance creation callback
      @ In, None
      @ Out, treeModel, Instance, pyomo scenario tree model for two stage stochastic programming
    """
    pass

  def pysp_instance_creation_callback(self, scenario_name, node_names):
    """
      Clone a new instance and update the stochastic parameters from the sampled scenario
      @ scenario_name, str, name of sampled scenario
      @ node_names, str, not used
      @ instance, instance, pyomo model instance
    """
    inputData = self.generateModelInputData()
    model = self.createInstance(inputData)
    instance = model.clone()
    for key, val in self.scenarios['scenario_data'][scenario_name].items():
      # instance.component(key).value = val
      instance.component(key).store_values(val)
    # instance.pprint()
    return instance

  def run(self):
    """
      This method execute the optimization on the knapsack problem.
      @ In, None
      @ Out, None
    """
    outputDict = {}
    if self.uncertainties is None:
      outputDict = super().run()
    else:
      tree_model = self.pysp_scenario_tree_model_callback()
      if self.stochSolver == 'ef':
        # fsfct the callback function for pysp_instance_creation_callback
        # tree_model generated by pysp_scenario_tree_model_callback
        stsolver = rapper.StochSolver("", fsfct=self.pysp_instance_creation_callback, tree_model=tree_model)
        ef_sol = stsolver.solve_ef(self.solver, sopts=self.sopts, tee=self.tee)
        if ef_sol.solver.termination_condition != TerminationCondition.optimal:
          raise RuntimeError("Solver did not report optimality:\n%s" %(ef_sol.solver))
        # TODO: Add collect output and return a dictionary for raven to retrieve information
        self.printScenarioSolution(stsolver)
        outputDict.update(self.getScenarioSolution(stsolver.scenario_tree))
        self.output.update(outputDict)
      elif self.stochSolver == 'ph':
        # fsfct the callback function for pysp_instance_creation_callback
        # tree_model generated by pysp_scenario_tree_model_callback
        stsolver = rapper.StochSolver("", fsfct=self.pysp_instance_creation_callback, tree_model=tree_model, phopts=self.phopts)
        ph_sol = stsolver.solve_ph(subsolver=self.solver, default_rho=self.phRho, phopts=self.phopts, sopts=self.sopts, tee=self.tee)
        # TODO: Add collect output and return a dictionary for raven to retrieve information
        # first retrieve the xhat solution from solver, then print the solution
        obj, xhat = rapper.xhat_from_ph(ph_sol)
        for nodeName, varName, varValue in rapper.xhat_walker(xhat):
          print (nodeName, varName, varValue)

        if ph_sol.solver.termination_condition != TerminationCondition.optimal:
          raise RuntimeError("Solver did not report optimality:\n%s" %(ph_sol.solver))
        self.printScenarioSolution(stsolver)
        outputDict.update(self.getScenarioSolution(stsolver.scenario_tree))
        self.output.update(outputDict)

    return outputDict


  def printScenarioSolution(self, stsolver):
    """
      Output optimization solution to screen
      @ In, stsolver, instance, pyomo stochastic programming solver instance
      @ Out, None
    """
    # use function from ./pyomo/pysp/scenariotree/tree_structure.py pprintSolution to
    # pretty-print the solution associated with a scenario tree
    stsolver.scenario_tree.pprintSolution()
    stsolver.scenario_tree.pprintCosts()

  def writeScenarioInfo(self, filename):
    """
      write scenario info into files
      @ In, filename, string, filename of output file
      @ Out, None
    """
    fileObj = open(filename,"w")
    fileObj.write("Scenarios: \n")
    for scenarioName, scenarioInfo in self.scenarios['scenario_data'].items():
      fileObj.write("\t%s:\n" %scenarioName)
      for var, valDict in scenarioInfo.items():
        fileObj.write("\t\t%s:" % var)
        for val in valDict.values():
          fileObj.write("%10.4f" % val)
        fileObj.write("\n")
    fileObj.close()

  def getScenarioSolution(self, ts):
    """
      Output optimization solution to csv file
      @ In, ts, instance, scenario tree structure intance
      @ Out, None
    """
    # dump scenario solutions into csv file
    logger.info("Dumping scenario solutions ...")
    scenarioOutput = collections.OrderedDict()
    scenarioNameList = []
    probabilityWeight = []
    # Loop over scenario tree to get the the solutions for each scienario
    for treeNodeName in sorted(iterkeys(ts._tree_node_map)):
      treeNode = ts._tree_node_map[treeNodeName]
      if treeNodeName == "RootNode":
        continue
      scenarioNameList.append(treeNodeName.replace("leaf_", ""))
      probabilityWeight.append(treeNode._conditional_probability)
      if (len(treeNode._stage._variable_templates) > 0) or (len(treeNode._variable_templates) > 0):
        for name in sorted(treeNode._variable_indices):
          for index in sorted(treeNode._variable_indices[name]):
            id = treeNode._name_index_to_id[name, index]
            if id in treeNode._standard_variable_ids:
              # if a solution has not yet been stored snapshotted, then the value won't be in the solution map
              try:
                value = treeNode._solution[id]
              except KeyError:
                value = None
              if value is not None:
                if index not in scenarioOutput and index is not None:
                  scenarioOutput[index] = []
                elif index is None and name not in scenarioOutput:
                  scenarioOutput[name] = []
                if index:
                  scenarioOutput[index].append(value)
                else:
                  scenarioOutput[name].append(value)
    scenarioOutput['ScenarioName'] = scenarioNameList
    scenarioOutput['ProbabilityWeight'] = probabilityWeight
    # Loop over scenario to get the cost
    costList = []
    for scenarioName in scenarioNameList:
      scenario = ts._scenario_map[scenarioName]
      aggregateCost = 0.0
      for stage in ts._stages:
        treeNode = None
        for node in scenario._node_list:
          if node._stage == stage:
            treeNode = node
            break
        costVariableValue = scenario._stage_costs[stage._name]
        if costVariableValue is not None:
          aggregateCost += costVariableValue
      costList.append(aggregateCost)

    scenarioOutput['MaxNPV'] = costList
    return scenarioOutput

  def writeOutput(self, filename):
    """
      Method used to output the optimization results
      @ In, filename, string, filename of output file
      @ Out, None
    """
    super().writeOutput(filename)
    if self.scenarios:
      scenarioInfoFile = ".".join(filename.split('.')[:-1]) + "_scenario_info.o"
      self.writeScenarioInfo(scenarioInfoFile)
