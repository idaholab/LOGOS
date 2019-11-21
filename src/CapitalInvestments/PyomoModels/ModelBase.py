#
#
#
"""
  Created on March. 19, 2019
  @author: wangc, mandd
"""

#for future compatibility with Python 3--------------------------------------------------------------
from __future__ import division, print_function, unicode_literals, absolute_import
import warnings
warnings.simplefilter('default',DeprecationWarning)
#End compatibility block for Python 3----------------------------------------------------------------

#External Modules------------------------------------------------------------------------------------
import abc
import itertools
import numpy as np
import logging
import pandas as pd
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
  from Logos.src.CapitalInvestments.investment_utils import investmentUtils as utils
except ImportError:
  from CapitalInvestments.investment_utils import investmentUtils as utils
#Internal Modules End--------------------------------------------------------------------------------

import pyutilib.subprocess.GlobalData
pyutilib.subprocess.GlobalData.DEFINE_SIGNAL_HANDLERS_DEFAULT = False

logger = logging.getLogger(__name__)

class ModelBase:
  """
    Base class for methods used to solving optimization problem
  """
  def __init__(self):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    # used for deterministic optimization
    self.type = self.__class__.__name__
    self.name = self.__class__.__name__
    self.sense = pyomo.minimize # type of optimization problem, i.e. minimize or maximize, default to minimize
    self.solver = 'cbc'         # type of solver, i.e. glpk, cbc, default cbc solver
    self.lowerBounds = None     # lower bounds of solution decision variables
    self.upperBounds = None     # upper bounds of solution decision variables
    self.regulatoryMandated = None # regulatory mandated projects
    self.tee = False            # print the output of the solver if True, otherwise not
    self.settings = None        # user provided controls
    self.sets = None            # pyomo required input sets info
    self.params = None          # pyomo required params info
    self.solutionVariableType = pyomo.Binary # solution variable type, i.e. Binary, Integers, Reals, default to Binary
    self.output = {}            # dictionary contains all outputs
    # additional info needed by stochastic optimization
    self.meta = None            # additional info
    self.scenariosData = None   # containers for scenarios/uncertainties input data
    self.uncertainties = None   # uncertainty info provided by users
    self.scenarios = {}         # dictionary contains scenarios information generated from self.uncertainties
    self.nonSelection = False   # options DoNothing should be included for each projects if True, otherwise should not be included
    self.optionalConstraints = {} # dictionary of optional constraints that users can turn on or off, i.e. {consistentConstraintI:True}
    self.sopts = {} # options for solvers, i.e. self.sopts['threads'] = 4
    self.phopts = {} # options for progressive hedging method
    self.phopts['--output-solver-log'] = None
    self.phopts['--max-iterations'] = 3
    self.phopts['--linearize-nonbinary-penalty-terms'] = 4
    self.phRho = 1
    self.executable = None      # specify the path to the solver
    self.stochSolver = 'ef'     # stochastic solver, default runef, can be switched to runph method in pyomo.
  def initialize(self, initDict):
    """
      Mehod to initialize
      @ In, initDict, dict, dictionary of preprocessed input data
        {'Sets':{}, 'Parameters':{}, 'Settings':{}, 'Meta':{}, 'Uncertainties':{}}
      @ Out, None
    """
    self.meta = initDict.pop('Meta', None)
    self.settings = initDict.pop('Settings', None)
    self.sets = initDict.pop('Sets', None)
    self.params = initDict.pop('Parameters', None)
    self.uncertainties = initDict.pop('Uncertainties', None)
    if self.uncertainties is not None:
      self.setScenarioData()
    if self.settings is not None:
      self.setSettings()

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
    logger.info('Initialize Settings of Optimization Instance: %s', self.name)
    self.sense = pyomo.maximize if self.settings.pop('sense', 'minimize') == 'maximize' else pyomo.maximize
    self.solver = self.settings.pop('solver', 'cbc')
    solverOptions = self.settings.pop('solverOptions', {})
    stochSolver = solverOptions.pop('StochSolver', None)
    if stochSolver is not None:
      self.stochSolver = stochSolver.lower().strip()
    self.executable = solverOptions.pop('executable', None)
    self.sopts.update(solverOptions)
    self.tee = self.settings.pop('tee',False)
    self.nonSelection = utils.convertStringToBool(self.settings.pop('nonSelection', 'False'))
    for optCon in self.optionalConstraints:
      if optCon == 'consistentConstraintI':
        self.optionalConstraints[optCon] = utils.convertStringToBool(self.settings.pop(optCon, 'True'))
      else:
        self.optionalConstraints[optCon] = utils.convertStringToBool(self.settings.pop(optCon, 'False'))
    self.optionalConstraints
    lowerBounds, upperBounds = self.settings.pop('lowerBounds', None), self.settings.pop('upperBounds', None)
    if lowerBounds is not None:
      self.lowerBounds = utils.convertNodeTextToFloatList(lowerBounds)
    else:
      self.lowerBounds = [0]
      logger.info('"lowerBounds" is not provided, default: "0"')
    if upperBounds is not None:
      self.upperBounds = utils.convertNodeTextToFloatList(upperBounds)
    else:
      self.upperBounds = [1]
      logger.info('"upperBounds" is not provided, default: "1"')
    regulatoryMandated = self.settings.pop('regulatoryMandated',None)
    if regulatoryMandated is not None:
      self.regulatoryMandated = utils.convertNodeTextToList(regulatoryMandated)
      if not set(self.regulatoryMandated).issubset(self.sets['investments']):
        raise IOError('"regulatoryMandated" list should be a subset of "investments"!')

  @abc.abstractmethod
  def generateModelInputData(self):
    """
      This method is used to generate input data for pyomo model
      @ In, None
      @ Out, data, dict, input data for pyomo model
    """
    pass

  @abc.abstractmethod
  def createModel(self):
    """
      This method is used to create pyomo model.
      @ In, None
      @ Out, model
    """
    pass

  def createInstance(self, data):
    """
      This method is used to instantiate the pyomo model
      @ In, data, dict, dictionary to initialize pyomo abstract model
      @ Out, model, pyomo.instance, instance of pyomo model
    """
    model = self.createModel()
    if not model.is_constructed():
      model = model.create_instance(data)
      # model.pprint()
    model.dual = pyomo.Suffix(direction=pyomo.Suffix.IMPORT)
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
    treeModel.Stages.add('FirstStage')
    treeModel.Stages.add('SecondStage')
    treeModel.Nodes.add('RootNode')
    for i in self.scenarios['scenario_name']:
      leafNode = 'leaf_' + i
      treeModel.Nodes.add(leafNode)
      treeModel.Scenarios.add(i)
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
    return instance

  def run(self):
    """
      This method execute the optimization on the knapsack problem.
      @ In, None
      @ Out, None
    """
    outputDict = {}
    if self.uncertainties is None:
      inputData = self.generateModelInputData()
      # specifying the path to a solver
      # with SolverFactory(self.solver, executable=self.executable) as opt:
      with SolverFactory(self.solver) as opt:
        opt.options.update(self.sopts) # add solver options
        model = self.createInstance(inputData)
        results = opt.solve(model, load_solutions=False, tee=self.tee, **{'use_signal_handling':False})
        if results.solver.termination_condition != TerminationCondition.optimal:
          raise RuntimeError("Solver did not report optimality:\n%s" %(results.solver))
        model.solutions.load_from(results)
        outputDict.update(self.printSolution(model))
        self.output.update(outputDict)
        # TODO: Add collect output and return a dictionary for raven to retrieve information
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

  def printSolution(self, model):
    """
      Output optimization solution to screen
      @ In, model, instance, pyomo optimization model
      @ Out, outputDict, dict, dictionary stores the outputs
    """
    outputDict = {}
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
                if index not in scenarioOutput:
                  scenarioOutput[index] = []
                scenarioOutput[index].append(value)
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
    df = pd.DataFrame(self.output)
    df = df.sort_values(by=["MaxNPV"])
    df.to_csv(filename, index=False)
    if self.scenarios:
      scenarioInfoFile = ".".join(filename.split('.')[:-1]) + "_scenario_info.o"
      self.writeScenarioInfo(scenarioInfoFile)