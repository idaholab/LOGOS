# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
  Created on March. 19, 2019
  @author: wangc, mandd
"""

#External Modules------------------------------------------------------------------------------------
import abc
import logging
import copy
import pandas as pd
import pyomo.environ as pyomo
from pyomo.opt import SolverFactory, TerminationCondition
#External Modules End--------------------------------------------------------------------------------

#Internal Modules------------------------------------------------------------------------------------
try:
  from LOGOS.src.CapitalInvestments.investment_utils import investmentUtils as utils
  from LOGOS.src.CapitalInvestments.PyomoModels.PyomoWrapper import PyomoWrapper
except ImportError:
  from CapitalInvestments.investment_utils import investmentUtils as utils
  from .PyomoWrapper import PyomoWrapper
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
    self.tee = False            # print the output of the solver if True, otherwise not
    self.settings = None        # user provided controls
    self.sets = None            # pyomo required input sets info
    self.params = None          # pyomo required params info
    self.solutionVariableType = pyomo.Binary # solution variable type, i.e. Binary, Integers, Reals, default to Binary
    self.output = {}            # dictionary contains all outputs
    self.paramsAuxInfo = {}     # dict used by self.setParameters to generate the correct format of input parameters
    self.decisionVariable = 'x' # optimization solution variable name
    ## external constraints
    self.externalConstraints = {} # dictionary of user provided constraints
    self.externalConstModules = {} # Store the loaded module of user provided constraints
    self.workingDir = None # working directory
    self.executable = None      # specify the path to the solver

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
    self.settings = initDict.pop('Settings', None)
    self.sets = initDict.pop('Sets', None)
    self.params = initDict.pop('Parameters', None)
    self.externalConstraints = initDict.pop('ExternalConstraints')

  def setSettings(self):
    """
      Method to process the settings of pyomo solver
      @ In, None
      @ Out, None
    """
    logger.info('Initialize Settings of Optimization Instance: %s', self.name)
    self.sense = pyomo.maximize if self.settings.pop('sense', 'minimize') == 'maximize' else pyomo.minimize
    self.solver = self.settings.pop('solver', 'cbc')
    self.workingDir = self.settings.pop('workingDir')
    self.tee = self.settings.pop('tee',False)
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

  @abc.abstractmethod
  def generateModelInputData(self):
    """
      This method is used to generate input data for pyomo model
      @ In, None
      @ Out, data, dict, input data for pyomo model
    """
    pass

  def createModel(self):
    """
      This method is used to create pyomo model.
      @ In, None
      @ Out, model, pyomo.AbstractModel, abstract pyomo model
    """
    model = self.initializeModel()
    model = self.addAdditionalSets(model)
    model = self.addAdditionalParams(model)
    model = self.addVariables(model)
    model = self.addExpressions(model)
    model = self.addObjective(model)
    model = self.addConstraints(model)
    model = self.addAdditionalConstraints(model)
    return model

  @abc.abstractmethod
  def initializeModel(self):
    """
      Initialize the pyomo model parameters for the problem
      @ In, None
      @ Out, model, pyomo model instance, pyomo abstract model
    """

  @abc.abstractmethod
  def addConstraints(self, model):
    """
      Add specific constraints for the problem
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """

  @abc.abstractmethod
  def addVariables(self, model):
    """
      Add variables for the problem
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """

  @abc.abstractmethod
  def addObjective(self, model):
    """
      Add objective for the problem
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """

  def addExpressions(self, model):
    """
      Add specific expressions for the problem
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    return model

  def addAdditionalSets(self, model):
    """
      Add specific Sets for the problem
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    return model

  def addAdditionalParams(self, model):
    """
      Add specific Params for the problem
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    return model

  def addAdditionalConstraints(self, model):
    """
      Add specific constraints for problem
      @ In, model, pyomo model instance, pyomo abstract model
      @ Out, model, pyomo model instance, pyomo abstract model
    """
    return model

  def addExternalConstraints(self, model):
    """
      This method is used to load user provided external constraints
      @ In, model, pyomo.instance, instance of pyomo model
      @ Out, model, pyomo.instance, modified instance of pyomo model
    """
    logger.info('Add external constraints to optimization model')
    # create pyomo wrapper instance
    pyomoWrapper = PyomoWrapper(model)
    setsNameList = self.sets.keys()
    paramsNameList = self.params.keys()
    # retrieve sets and params
    setsDict = copy.deepcopy(pyomoWrapper.getAllSets(setsNameList))
    paramsDict = copy.deepcopy(pyomoWrapper.getAllParameters(paramsNameList))
    decisionVar = pyomoWrapper.getVariable(self.decisionVariable)
    # load all external constraint modules
    for key, val in self.externalConstraints.items():
      moduleToLoadString, filename = utils.identifyIfExternalModuleExists(val, self.workingDir)
      self.externalConstModules[key] = utils.importFromPath(moduleToLoadString)
      logger.info('Import external constraint module: "{}"'.format(moduleToLoadString))
    # start to execute functions from external constraint modules
    for constrKey, constrMod in self.externalConstModules.items():
      # 'initialize' can be used when the user wants to modify the values of parameters
      if 'initialize' in dir(constrMod):
        updateDict = constrMod.initialize()
        if updateDict:
          if set(updateDict.keys()).issubset(set(paramsNameList)):
            # pre-process data with extended indices (i.e. time_periods)
            extendedDict = {}
            for key, value in updateDict.items():
              extendedDict[key] = self.setParameters(key, self.paramsAuxInfo[key]['options'],
                                    self.paramsAuxInfo[key]['maxDim'],
                                    value
                                  )
            # call internal functions to update parameters, initial values provided by LOGOS input file will be
            # modified by given dictionary "extendedDict"
            pyomoWrapper.updateParams(extendedDict)
          else:
            missing = set(updateDict.keys()) - set(paramsNameList)
            raise IOError('The following parameters "{}" is not available in defined optimization problem, '
              'available parameters include: "{}"!'.format(', '.join(missing), ', '.join(paramsNameList))
            )
      if 'constraint' not in dir(constrMod):
        raise IOError(
          'External constraint: "{}" does not contain a method named "constraint". '
          'It must be present if this needs to be used in LOGOS optimization!'.format(constrKey)
        )
      else:
        # constrMod.constraint(pyomoWrapper, constrKey)
        externalConstraint = constrMod.constraint(decisionVar, setsDict, paramsDict)
        if len(externalConstraint) == 1:
          pyomoWrapper.addConstraint(constrKey, externalConstraint[0])
        else:
          pyomoWrapper.addConstraintSet(constrKey, externalConstraint[0], externalConstraint[1:])
    return model

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
    if self.externalConstraints:
      model = self.addExternalConstraints(model)
    model.dual = pyomo.Suffix(direction=pyomo.Suffix.IMPORT)
    return model

  def run(self):
    """
      This method execute the optimization on the knapsack problem.
      @ In, None
      @ Out, None
    """
    outputDict = {}
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
    return outputDict

  def printSolution(self, model):
    """
      Output optimization solution to screen
      @ In, model, instance, pyomo optimization model
      @ Out, outputDict, dict, dictionary stores the outputs
    """
    outputDict = {}
    return outputDict

  def writeOutput(self, filename):
    """
      Method used to output the optimization results
      @ In, filename, string, filename of output file
      @ Out, None
    """
    df = pd.DataFrame(self.output)
    df = df.sort_values(by=["MaxNPV"])
    df.to_csv(filename, index=False)
