# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED

import math
import copy
import networkx as nx
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import patches
import itertools
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import os
import plotly.express as px
import plotly.io as pio
import random
import json

from MDKoutage import mdkChoiceModel


class Activity:
  """
    This is the base class for a single activity
    Extended from the original development of Nofar Alfasi
    Source https://github.com/nofaralfasi/PERT-CPM-graph
  """
  def __init__(self, name, duration, res=None, childs=None):
    """
      Constructor
      @ In, name, str, ID of the activity
      @ In, duration, float, planned activity duration
      @ In, res, list, required resource to complete the activity
      @ In, child, list, list containing the names (str type) of the children (i.e., successors)
      @ Out, None
    """
    self.name = str(name)       # name ID of the activity
    self.duration = duration    # planned duration of of the activity
    self.subActivities = []     # list of activities that have been clustered to this activity
    self.belongsToCP = False    # Boolean flag that indicates if the axctivity belongs to the CP
    self.resources = res        # resources required to complete the activity

    self.startTime = None       # activity actual start time
    self.endTime   = None       # activity actual completion time

    self.delay = 0              # delay imposed to the activity by resource availability

    if childs is None:
      self.childs = []          # list containing the names (str type) of the children (i.e., successors)
    else:
      self.childs = childs

  def printToJson(self):
    """
      Method designed to print on file activity in json format
      @ In, None
      @ Out, file in json format
    """
    return json.dumps(self.__dict__, sort_keys=True, default=str)

  def updateChilds(self, childs):
    """
      Method designed to assign the childs of an activity
      @ In, childs, list containing the names (str type) of the children (i.e., successors)
      @ Out, None
    """
    for child in childs:
      self.childs.append(child.returnName())

  def returnName(self):
    """
      Methods that returns the name of the activity
      @ In, None
      @ Out, name, str, name ID of the activity
    """
    return self.name

  def returnDuration(self):
    """
      Methods that returns the duration of the activity
      @ In, None
      @ Out, duration, float, duration of the activity
    """
    return self.duration

  def returnResources(self):
    """
      Methods that returns the duration of the activity
      @ In, None
      @ Out, resources, str, resources required to complete the activity
    """
    return self.resources

  def updateDuration(self, newDuration):
    """
      Methods that changes the duration of the activity
      @ In, newDuration, str, updated duration of the activity
      @ Out, None
    """
    self.duration = copy.deepcopy(newDuration)

  def addDelay(self):
    """
      Methods that changes the duration of the activity
      @ In, newDuration, str, updated duration of the activity
      @ Out, None
    """
    self.duration = self.duration + 1.
    self.delay = self.delay + 1.

  def returnSubActivities(self):
    """
      Methods that returns the list of subactivities
      @ In, None
      @ Out, subActivities, list, list of subactivities
    """
    return self.subActivities

  def addSubActivities(self, subActivities):
    """
      Method that associates a list of subactivities
      @ In, subActivities, list, list of subactivities
      @ Out, None
    """
    self.subActivities = subActivities
    tempDuration = 0.
    for act in subActivities:
      tempDuration += act.returnDuration() - act.delay
    self.duration = tempDuration

  def setOnCP(self):
    """
      Methods that sets if an activity is part of the CP
      @ In, None
      @ Out, None
    """
    self.belongsToCP = True

  def returnCPstatus(self):
    """
      Return if an activity is part of the CP or not
      @ In, None
      @ Out, belongsToCP, bool, variable that flags if activity belongs to CP
    """
    return self.belongsToCP

  def setActualStartTime(self, Tin):
    """
      Set initial time of the activity based on CPM calculations
      @ In, Tin,  float, initial time of the activity
      @ Out, None
    """
    self.startTime = Tin
    self.endTime   = Tin + timedelta(hours=self.duration-self.delay)

  def returnAbsTimes(self):
    """
      Return initial and final time of the activity based on CPM calculations
      @ In, None
      @ Out, (self.startTime,self.endTime), tuple, tuple containing initial and final time of the activity
    """
    return (self.startTime,self.endTime)




class Pert:
  """
    This is the base class for a schedule as a set of activities linked by a graph structure
    A graph is a map with activities as keys and list of outgoing activities as value for every key
    The graph starts with a 'start' node and ends with a 'end' node.
    Extended from the original development of Nofar Alfasi
    Source https://github.com/nofaralfasi/PERT-CPM-graph
  """

  def __init__(self, graph={}, jsonFile=None, startTime=None, resourcesTS=None, priorities=None):
    """
      Constructor
      @ In, graph, dict, dictionary containing the child activities for each activity
      @ In, startTime, datetime, absolute initial time of schedule
      @ In, resourcesTS, dataframe, pandas dataframe containing resources availability
      @ In, priorities, dict, dictionary containing the priority values for each activity
      @ Out, None
    """
    if jsonFile is not None:
      self.forwardDict = []
    else:
      self.forwardDict = graph    # list of out going nodes for every activity
    
    self.resources = resourcesTS  # dataframe containing resources availability
    self.startTime = startTime    # initial time/date of project schedule

    self.priorities = priorities

    if resourcesTS is not None:
      self.checkResources()
      self.resourcesTemporalCheck()
      if pd.infer_freq(resourcesTS.index) not in ['h','H']:
        print("resourcesTS in PERT is set on the wrong index frequency: " + str(pd.infer_freq(resourcesTS.index)) + " instead of h or H")

    self.backwardDict = {}     # list of in going nodes for every activity
    self.infoDict = {}         # map of details for every activity
    self.startActivity = Activity
    self.endActivity = Activity
    self.resetInitialGraph()   # first reset of the graph
    self.generateInfo()        # entering values into 'infoDict'

    # Initialization of the seed used by the random shuffling choice strategy
    self.seed = 2506178
    random.seed(self.seed)

    for act in self.forwardDict.keys():
      act.updateChilds(self.forwardDict[act])


  def parseInputFile(self, filename):
    """
    Parses a JSON file containing activity definitions and dependencies.
    Validates unique activity IDs, instantiates Activity objects, and builds the graph.
    Returns:
        graph: dict mapping Activity instance to list of dependent Activity instances
    """
    with open(filename, 'r') as f:
      data = json.load(f)

    activities_data = data.get("activities", [])
    dependencies_data = data.get("dependencies", {})

    # Validate unique IDs
    ids = [activity['id'] for activity in activities_data]
    if len(ids) != len(set(ids)):
      raise ValueError("Activity IDs must be unique.")

    # Instantiate Activity objects
    id_to_activity = {}
    for activity in activities_data:
        act = Activity(activity['id'], activity['duration'], activity['resources'])
        id_to_activity[activity['id']] = act

    # Build graph using Activity instances
    graph = {}
    for src_id, dest_ids in dependencies_data.items():
        src_activity = id_to_activity[src_id]
        graph[src_activity] = []
        for dest_id in dest_ids:
            graph[src_activity].append(id_to_activity[dest_id])

    return graph
  
  def __str__(self):
    """
      Method designed to return basic information of the schedule graph
      @ In, None
      @ Out, None
    """
    iterator = iter(self)
    graphStr = 'Activities:\n'
    for activity in iterator:
      graphStr += str(activity) + '\n'
    return (graphStr + 'Connections:\n'
      + str(self.forwardDict)
      + '\nProject Duration:\n'
      + str(self.infoDict[self.endActivity]['ef']))

  # iterator for the pert class
  def __iter__(self):
    return iter(self.forwardDict)

  def reseed(self, seed_value):
    """
      Method designed to reseed the RNG
      @ In, seed_value, int, new seed value
      @ Out, None
    """
    self.seed = seed_value
    random.seed(self.seed)

  def checkResources(self):
    """
      Method designed to check that the provided resource temporal profile contains allowed resource types
      @ In, None
      @ Out, None
    """
    for act in self.forwardDict:
      if act.returnName() not in ['start','end'] and not set(act.returnResources().keys()).issubset(set(self.resources.columns)):
        raise IOError("Activity " + str(act.returnName()) + " requires a resource that is not allowed: " + str(act.returnResources()))

  def resetInitialGraph(self):
    """
      Method designed to reset the schedule graph:
       * reseting 'backward_dict' for every activity
       * setting 'startActivity' and 'endActivity'
      @ In, None
      @ Out, None
    """
    for activity in self.forwardDict:
      self.backwardDict[activity] = []
    for activity in self.forwardDict:
      if activity.name == "start":
        self.startActivity = activity
      if activity.name == "end":
        self.endActivity = activity
      for node in self.forwardDict[activity]:
        self.backwardDict[node].append(activity)
    self.resetInfo()

  def resetInfo(self):
    """
      Method designed to reset the numeric values of the schedule graph:
        # duration: the duration of the activity
        # es: early start
        # ef: early finish
        # ls: late start
        # lf: late finish
        # slack: lf - ef or ls - es
      @ In, None
      @ Out, None
    """
    for activity in self.forwardDict:
      self.infoDict[activity] = {
        "duration": activity.duration,
        "es": 0, "ef": 0, "ls": 0, "lf": math.inf,
        "slack": 0}

  def returnGraph(self):
    """
      Method designed to return the graph info contained in the self.forwardDict dictionary
      @ In, None
      @ Out, self.forwardDict, dict, graph info (edges, nodes and time values)
    """
    return self.forwardDict

  def returnGraphSymbolic(self):
    """
      Method designed to return the graph in a symbolic form
      @ In, None
      @ Out, symbolicGraph, dict, graph structure
    """
    symbolicGraph = {}
    for key in self.forwardDict.keys():
      symbolicGraph[key.returnName()]=[]
      for elem in self.forwardDict[key]:
        symbolicGraph[key.returnName()].append(elem.returnName())
    return symbolicGraph

  def generateInfo(self):
    """
      Method designed to calculate es, ef, ls, and lf of the schedule
        # run from start to end and put all 'es' 'ef' details in place
        # run from end to start and put all 'ls' 'lf' details in place
        # calculate slack for all activities (except isolated)
        # calculate details for isolated activities
      @ In, None
      @ Out, None
    """
    if self.forwardDict == {}:
      return
    self.infoDict[self.startActivity]["ef"] = self.infoDict[self.startActivity]["duration"]
    self.startToEndScan(self.startActivity)
    self.infoDict[self.endActivity]["lf"] = self.infoDict[self.endActivity]["ef"]
    self.infoDict[self.endActivity]["ls"] = self.infoDict[self.endActivity]["lf"] - self.infoDict[self.endActivity]["duration"]
    self.endToStartScan(self.endActivity)
    self.calculateSlack()
    self.generateInfoForIsolated()

  def startToEndScan(self, activity):
    """
      Method designed to calculate es and ef of the activities in the schedule
        # run from start to end and put all 'es' 'ef' details in place
      @ In, None
      @ Out, None
    """
    for node in self.forwardDict[activity]:
      if self.infoDict[activity]["ef"] > self.infoDict[node]["es"]:
        self.infoDict[node]["es"] = self.infoDict[activity]["ef"]
        self.infoDict[node]["ef"] = self.infoDict[node]["es"] + self.infoDict[node]["duration"]
      self.startToEndScan(node)

  def endToStartScan(self, activity):
    """
      Method designed to calculate ls and lf of the activities in the schedule
        # run from end to start and put all 'ls' 'lf' details in place
      @ In, None
      @ Out, None
    """
    for node in self.backwardDict[activity]:
      if (self.infoDict[node]["lf"] > self.infoDict[activity]["ls"]):
        self.infoDict[node]["lf"] = self.infoDict[activity]["ls"]
        self.infoDict[node]["ls"] = (self.infoDict[node]["lf"] - self.infoDict[node]["duration"])
      self.endToStartScan(node)

  def calculateSlack(self):
    """
      Method designed to calculate slack of the activities in the schedule (except isolated)
      @ In, None
      @ Out, None
    """
    for activity in self.forwardDict:
      self.infoDict[activity]["slack"] = self.infoDict[activity]["lf"] - self.infoDict[activity]["ef"]

  def generateInfoForIsolated(self):
    """
      Method designed to calculate slack for isolated activities
         # assumption: activity duration shorter than project duration
      @ In, None
      @ Out, None
    """
    isolated = self.findIsolated()
    for activity in isolated:
      self.infoDict[activity]["ef"] = self.infoDict[activity]["es"] + self.infoDict[activity]["duration"]
      self.infoDict[activity]["lf"] = self.infoDict[self.endActivity]["lf"]
      self.infoDict[activity]["ls"] = self.infoDict[activity]["lf"] - self.infoDict[activity]["duration"]
      self.infoDict[activity]["slack"] = self.infoDict[activity]["lf"] - self.infoDict[activity]["ef"]

  # add activity to the pert
  def addActivity(self, activity, inConnections=[], outConnections=[]):
    """
      Method designed to add a new activity to an exisiting schedule
      @ In, activity, activity, activitiy to be added
      @ In, inConnections, list, list of activities arriving into new activity
      @ In, outConnections, list, list of activities departing from new activity
      @ Out, None
    """
    if activity in self.forwardDict:
      return
    self.forwardDict[activity] = outConnections
    self.backwardDict[activity] = inConnections
    if inConnections != []:
      for node in inConnections:
        if self.forwardDict[node] is None:
          self.forwardDict[node] = []
        self.forwardDict[node] += [activity]
    if outConnections != []:
      for node in outConnections:
        if self.backwardDict[node] is None:
          self.backwardDict[node] = []
        self.backwardDict[node] += [activity]
    self.infoDict[activity] = {
      "duration": activity.duration,
      "es": 0, "ef": 0, "ls": 0, "lf": math.inf,
      "slack": 0}
    self.resetInfo()
    self.generateInfo()

  def findIsolated(self):
    """
      Method designed to find isolated activities
      @ In, None
      @ Out, isolated, list, list of isolated activities
    """
    isolated = list(self.infoDict)
    for activity in self.forwardDict:
      if self.forwardDict[activity] != [] and activity in isolated:
        isolated.remove(activity)
    for activity in self.backwardDict:
      if self.backwardDict[activity] != [] and activity in isolated:
        isolated.remove(activity)
    return isolated

  def getSlackForEachActivity(self):
    """
      Get slack time for each activity in descending order without critical activities
      @ In, None
      @ Out, slackVals, list, list of slack value for all activities
    """
    slacks = {activity.returnName(): self.infoDict[activity]["slack"] for activity in self.infoDict if self.infoDict[activity]["slack"] != 0}
    slackVals = sorted(slacks.items(), key=lambda kv: kv[1], reverse=True)
    return slackVals

  def getSumOfSlacks(self):
    """
      Get sum of the slack values for all activities
      @ In, None
      @ Out, sumSlacks, float, sum of the slack values for all activities
    """
    slacks = [kv[1] for kv in self.getSlackForEachActivity()]
    sumSlacks = sum(slacks)
    return sumSlacks

  def getCriticalPath(self):
    """
      Get CP of the schedule as a list of activities
      @ In, None
      @ Out, path, list, list of activities included in the CP
    """
    activity = self.startActivity
    path = [activity]
    while activity != self.endActivity :
      for node in self.forwardDict[activity]:
        if self.infoDict[node]["slack"] <= 0.0001:   # originally set as " == 0." . Modified to handle non integers durations
          activity = node
      path += [activity]
    return path

  def getCriticalPathSymbolic(self):
    """
      Get CP of the schedule as a string of activities ID
      @ In, None
      @ Out, symbPath, str, list of activities included in the CP in string form
    """
    path = self.getCriticalPath()
    symbPath=[]
    for elem in path:
      symbPath.append(elem.returnName())
    return symbPath

  def getCriticalPathWithLength(self):
    """
      Get CP of the schedule dictionary
      @ In, None
      @ Out, CPdict, dict, dictionary of activities included in the CP alonf with their corresponding duration
    """
    CPdict = {activity: activity.duration for activity in self.getCriticalPath()}
    return CPdict

  def shortenCriticalPath(self):
    """
      Get a map of the activities with the maximum amount of time to reduce from it's duration without taking it our of the critical path
      We are getting all alternative paths between 2 nodes (activities) in the critical path (only nodes that have at least one node between them)
      We are taking the minimum slack and putting it as the value for maximum reduction
      The minimum duration for every task is 1
      @ In, None
      @ Out, CPdict, dict, dictionary of activities included in the CP alonf with their corresponding duration
    """
    criticalPath = self.getCriticalPath()
    maxDecreaseToActivities = {activity: activity.duration - 1 for activity in criticalPath}
    for i in range(0,  len(criticalPath), 1):
      for j in range(2, len(criticalPath) - i, 1):
        for path in self.getAllAlternativePaths(criticalPath[i], criticalPath[i + j]):
          for activity in criticalPath[i + 1 : i + j : 1]:
            if path[1] not in criticalPath and maxDecreaseToActivities[activity] >= self.infoDict[path[1]]["slack"]:
              maxDecreaseToActivities[activity] = self.infoDict[path[1]]["slack"] - 1
    return maxDecreaseToActivities

  def getAllAlternativePaths(self, startActivity, endActivity, path=[], symbolic=False):
    """
      Get all the paths between 2 nodes (activities) in the graph (pert)
      @ In, startActivity, activity, activity at the beginning of the path
      @ In, endActivity activity, activity at the end of the path
      @ In, symbolic, bool, flag to indicate if alternate path should be generated in twerms of name of each activity
      @ Out, paths, list, list of paths between startActivity and endActivity
    """
    onePath = path + [startActivity]
    if startActivity == endActivity:
      return [onePath]
    if startActivity not in self.infoDict:
      return []
    paths = []
    for activity in self.forwardDict[startActivity]:
      paths += self.getAllAlternativePaths(activity, endActivity, onePath)
    if symbolic:
      symbPaths = []
      for path in paths:
        symbPath = []
        for act in path:
          symbPath.append(act.returnName())
        symbPaths.append(symbPath)
      return symbPaths
    else:
      return paths

  def getAllPathsParallelToCP(self):
    """
      Method designed to return all the paths parallel to the critical path
      @ In, none
      @ Out, pathsList, list, list of paths that are parallel to the critical path
    """
    CP = self.getCriticalPath()
    pathsList = self.getAllAlternativePaths(CP[0], CP[-1])
    pathsList.remove(CP)
    return pathsList

  def returnSuccList(self,node):
    """
      Method designed to return the immediate successors of a node
      @ In, node, activity, activity being queried
      @ Out, listSucc, list, list of activities that are immediate successors of "node"
    """
    listSucc = list(self.forwardDict[node])
    return listSucc

  def returnNumberSucc(self,node):
    """
      Method designed to return the number of immediate successors of a node
      @ In, node, activity, activity being queried
      @ Out, numSucc, int, number activities that are immediate successors of "node"
    """
    numSucc = len(list(self.forwardDict[node]))
    return numSucc

  def returnPredList(self,node):
    """
      Method designed to return the immediate predecessors of a node
      @ In, node, activity, activity being queried
      @ Out, listPred, list, list of activities that are immediate predecessors of "node"
    """
    listPred = (self.backwardDict[node])
    return listPred

  def returnNumberPred(self,node):
    """
      Method designed to return the number of immediate predecessors of a node
      @ In, node, activity, activity being queried
      @ Out, numPred, int, number activities that are immediate predecessors of "node"
    """
    numPred = len((self.backwardDict[node]))
    return numPred

  def returnSubActivities(self, node):
    """
      Method retrun the set of activities that have been merged into an activity
      @ In, node, activity, activity to be queried
      @ Out, listSubAct, list, list of activities
    """
    listSubAct = node.returnSubActivities()
    return listSubAct

  def deleteActivity(self,node):
    """
      Method designed to delete an activity from a schedule
      @ In, node, activity, activity to be removed
      @ Out, none
    """
    del self.forwardDict[node]

  def updateMergedSeries(self, node, listSucc, subActivities):
    """
      Method designed to add a merged series to a schedule
      @ In, node, activity, activity to be added
      @ In, listSucc, list, list of sucessor activities associated with "node"
      @ In, subActivities, list, list of activities that are part of the series
      @ Out, none
    """
    node.addSubActivities(subActivities)
    self.forwardDict[node] = listSucc

  def simplifyGraph(self):
    """
      Method designed to simplify the structure of a Pert graph by combining activities that are in series
      @ In, none
      @ Out, reducedPertModel, Pert model, reduced Pert model
    """
    updatedGraph = copy.deepcopy(self.forwardDict)
    reducedPertModel = Pert(updatedGraph)

    listPairs = reducedPertModel.pairsDetection()

    G = nx.DiGraph()
    G.add_edges_from(listPairs)

    subgraphs_of_G_ex, removed_edges = graphPartitioning(G, plotting=False)
    listSeries = list(subgraphs_of_G_ex)

    for series in listSeries:
        temp = list(nx.topological_sort(series))
        succOFSeries = list(updatedGraph[temp[-1]])
        for node in list(series.nodes):
            reducedPertModel.deleteActivity(node)
        if checkForEndNode(temp) is None:
            reducedPertModel.updateMergedSeries(temp[0], succOFSeries, temp)
        else:
            reducedPertModel.updateMergedSeries(checkForEndNode(temp), succOFSeries, temp)

    return reducedPertModel

  def pairsDetection(self):
    """
      Method designed to identify pairs of activities that are in series
      @ In, none
      @ Out, pairs, list of tuples, list of pairs of activities, each pair is a tuple (activity_1, activity_2)
    """
    pairs = []
    for node in self.forwardDict:
        if self.returnNumberSucc(node)==1:
            successor = self.returnSuccList(node)[0]
            if self.returnNumberPred(successor)==1:
                pairs.append((node,successor))
    return pairs

  def getSubpathsParalleltoCP(self):
    """
      Method designed to return the subpaths that are parallel to CP
      @ In, none
      @ Out, subpathsSetRed, list of activities, list of activities that are parallel to the CP
    """
    CP = self.getCriticalPath()
    paths = self.getAllPathsParallelToCP()
    subpathsSet = []
    for path in paths:
      subpaths = getSubpaths(path,CP)
      bSet = set(map(tuple,subpaths))
      subpathsSetRed = list(map(list,bSet))
      subpathsSetRed.remove([])
      subpathsSetExp = expandSubpaths(subpathsSetRed,path)
      subpathsSet = subpathsSet + subpathsSetExp

    cSet = set(map(tuple,subpathsSet))
    subpathsSetRed = list(map(list,cSet))
    return subpathsSetRed

  def returnPathSymbolic(self, path):
    """
      Method designed to print the symbolic name of a path
      @ In, path, list, list of activities
      @ Out, None
    """
    symbPath = []
    for act in path:
      symbPath.append(act.name)
    return symbPath

  def returnScheduleEndTime(self):
    """
      Method designed to return the absolute end time of the schedule
      @ In, None
      @ Out, endTime, float, absolute end time of the schedule
    """
    endTime = self.startTime + timedelta(hours=self.infoDict[self.getCriticalPath()[-1]]['ef'])
    return endTime

  def saveScheduleToJsn(self, nameFile='schedule.json'):
    """
      Method designed to print on file schedule in json format
      @ In, nameFile, string, name of the generated file
      @ Out, file in json format
    """
    with open(nameFile, 'w', encoding="utf-8") as fp:
      for act in self.forwardDict.keys():
        json.dump(act.printToJson(), fp, sort_keys=True, indent=4)
        fp.write("\n")

  def resourcesTemporalCheck(self):
    """
      Method designed to assess time dependent resources requested by actual schedule
      @ In, None
      @ Out, None
    """
    self.reqResources = pd.DataFrame().reindex_like(self.resources)
    self.reqResources = self.reqResources.replace(np.nan, 0)
    for act in self.forwardDict:
      absTimeVals = act.returnAbsTimes()
      res_dict = act.returnResources()
      for res in res_dict.keys():
        self.reqResources.loc[absTimeVals[0]:absTimeVals[1],res] += res_dict[res]

  def convertListOfActToSymbolic(self, activities_list):
    """
      Method designed to convert a list of activities into a list activity names
      @ In, activities_list, list, list of activities instances
      @ Out, symbList, list, list of strings (i.e., activity names)
    """
    symbList = []
    for act in activities_list:
      symbList.append(act.returnName())
    return symbList

  def localOptStatus(self, candidateActivities, time_index, selectedActivities):
    """
      Method designed to print on terminal the project scheduling process; this method has been added mainly
      for debugging purposes
      @ In, candidateActivities, dict, dictionary of candidate activities in the form:
                                       {activity_instance: {'duration': , 'es': , 'ef': , 'ls': , 'lf': , 'slack': , 'value': }}
      @ Out, None
    """
    print('----------------')
    print(time_index)

    wait = self.convertListOfActToSymbolic(self.wait)
    print('wait      : ' + str(wait))

    candidates = self.convertListOfActToSymbolic(candidateActivities)
    print('candidates: ' + str(candidates))

    selected = self.convertListOfActToSymbolic(selectedActivities)
    print('selected  : ' + str(selected))

    ongoing = self.convertListOfActToSymbolic(self.ongoing)
    print('ongoing   : ' + str(ongoing))

    completed = self.convertListOfActToSymbolic(self.completed)
    print('completed : ' + str(completed))

    new_elem = pd.DataFrame([[time_index, wait, candidates, selected, ongoing, completed]], columns=self.optStatusDF.columns)
    self.optStatusDF = pd.concat([self.optStatusDF, new_elem], ignore_index=True)

  def printSchedulingProgression(self, fileName=None):
    """
      Method designed to print on .csv file the project scheduling process
      @ In,fileName, string, name of the file that will be generated
      @ Out, None
    """
    if fileName is None:
      fileName = 'scheduleProgression.csv'
    else:
      fileName = fileName + '.csv'
    self.optStatusDF.to_csv(fileName, index=False)

  def calculateScheduleWithResources(self, choice):
    """
      Method designed to schedule activity actual start and end time based on available resources
      @ In, choice, string, type of choice to select activities out of available candidates. Note that some types have been
                            added only for testing purposes. Allowed types:
                            * first: select the first activity in candidates
                            * first_with_res: select the first activity in candidates only if present and future resources
                              are available
                            * max_use_res_act: select the first N activities in candidates only if present and future
                              resources are available
                            * max_use_res_ranked: rank activities based on float values and select the first N activities
                              in candidates only if present and future resources are available
                            * max_use_res_shuffled: randomly shuffle the initial list of activities and select the first
                              N activities in candidates only if present and future resources are available
                            * MD-Knapsack: select N activities through the multi-dimensional knapsack optimization model
                                           in candidates only if present resources are available. This assumes that once a
                                           resource has been tasked to an activty, that resource is assigned until the activity
                                           has been completed. This might lead to negative resource availability
      @ Out, None
    """

    N_activities   = len(self.infoDict.keys())
    self.wait      = list(self.forwardDict.keys())    # List of activities that needs to be completed
    self.ongoing   = []                               # List of activities that are actually being performed
    self.completed = []                               # List of activities that have been completed

    T_max = self.resources.index.max()

    time_index = self.startTime

    # Initialize dataframe that will contain calculation progression
    self.optStatusDF = pd.DataFrame(columns=['time', 'wait', 'candidates', 'selected', 'ongoing', 'completed'])

    while len(self.completed) != N_activities and time_index<T_max:
      # select resources available at time t
      res_at_t = self.resources.loc[time_index].to_dict()

      # Select set of activities that can potentially start from wait (criteria: early start (ES) values is <=time_index)
      if self.priorities is None:
        candidateActivities = self.selectCandidateActivities(time_index, 'TF_based')
      else:
        candidateActivities = self.selectCandidateActivities(time_index, 'external')

      # If there are potential candidates
      if candidateActivities:
        # Select activities that will start at time t and generate the future usage profile of the resources of
        # the selected actvities
        selectedActivities, res_usage = self.scheduleGenerationScheme(candidateActivities, res_at_t, time_index, choice)

        # update the lists self.wait and self.ongoing based on candidateActivities and selectedActivities
        self.updateSetActivities(selectedActivities, candidateActivities, time_index)

        # Update resource availability for time greater than time_index
        self.updateResourceAvailability(res_usage, time_index)

        # Run CPM model with update duration values
        self.resetInitialGraph()
        self.generateInfo()
      else:
        print('no candidates')

      # Update the self.ongoing and self.completed lists: the activities that are completed at time t
      self.updateOngoingList(time_index)

      # Save RCPSP calculation status outcome at each iteration
      self.localOptStatus(candidateActivities, time_index, selectedActivities)

      time_index = time_index + pd.Timedelta(hours=1)

    self.summarizeSchedule()
    self.printSchedulingProgression()
    self.printSchedule()

  def selectCandidateActivities(self, time, valueAssignment):
    """
      Method designed to:
      1) select all activities in the wait list that can start at time t=time
      2) assign a weigth value based on the slack
      @ In, time, datetime, current time of project schedule progression
      @ Out, actReadyToGo, dict, dictionary of activities that can start
    """
    actReadyToGo = {}
    for act in self.wait:
      absES = self.startTime + pd.Timedelta(hours=self.infoDict[act]['es'])
      if absES<=time:
        actReadyToGo[act] = self.infoDict[act]

    if valueAssignment == 'TF_based':
      for act in actReadyToGo.keys():
        actReadyToGo[act]['value'] = weightFunction(actReadyToGo[act]['slack'])
    elif valueAssignment == 'external':
      for act in actReadyToGo.keys():
        actReadyToGo[act]['value'] = self.priorities[actReadyToGo[act].returnName()]
    return actReadyToGo

  def scheduleGenerationScheme(self, candidates, res, time_index, choice):
    """
      Method that implement the chosen choice to select activities out of a set of candidate activities
      @ In, candidates, dict, dictionary of candidate activities in the form:
                              {activity_instance: {'duration': , 'es': , 'ef': , 'ls': , 'lf': , 'slack': , 'value': }}
      @ In, res, pd.dataframe, resources availability at time time_index
      @ In, time_index, datetime, current time of project schedule progression
      @ In, choice, string, type of choice to select activities out of candidates
      @ Out, selected, list, lis of selected activities out of candidatesthat have been chosen according to selected choice
      @ Out, res_usage, dict, dictionary containing the forecasted resources required to complete the selected activities
    """
    if choice=='first':
      # select first element in activities
      selected = [next(iter(candidates))]
    elif choice=='first_with_res':
      # select first element in activities and check actual resources are available
      selected = [next(iter(candidates))]
      reqResources = selected[0].returnResources()
      if res[reqResources]<1.:
        selected = []
    elif choice=='max_use_res_act' or choice=='max_use_res_ranked' or choice=='max_use_res_shuffled':
      # select all activities that match available resources
      selected = []
      if choice=='max_use_res_act':
        pass
      if choice=='max_use_res_ranked':
        candidates = self.ranked(candidates)
      if choice=='max_use_res_shuffled':
        candidates = self.shuffle(candidates)
      for act in candidates:
        temp = copy.deepcopy(self.resources)
        temp_selected = copy.deepcopy(selected)
        temp_selected.append(act)
        res_usage_temp = self.resourceUseProfile(temp_selected)
        # check resource availability
        outcome = self.checkResourceAvailability(temp, res_usage_temp, time_index)
        if outcome:
          selected.append(act)
    elif choice=='MD-Knapsack':
      if self.priorities is not None:
        MDKmodel = mdkChoiceModel(candidates, self.resources.loc[time_index], 'uniform')
      else:
        MDKmodel = mdkChoiceModel(candidates, self.resources.loc[time_index], 'value_based')
      selected = MDKmodel.run()
    else:
      raise IOError('Chosen choice method not allowed')

    res_usage = self.resourceUseProfile(selected)

    return selected, res_usage

  def checkResourceAvailability(self, res_profile, res_usage, time):
    """
      Method designed to check that the forecasted resource availability is not ngetaive (i.e., planned
      resources exceed actual availability)
      @ In, res_profile, pd.dataframe, temporal profile of resources availability
      @ In, res_usage, dict, dictionary containing the forecasted resources required to complete the selected activities
      @ In, time, datetime, current time of project schedule progression
      @ Out, out, boolean, outcome of the check: resource availability < 0
    """
    for res in res_usage:
      delta = pd.Timedelta(hours=len(res_usage[res])-1)
      res_profile.loc[time:time+delta,res] = res_profile.loc[time:time+delta,res].values - res_usage[res]

    if (res_profile.values<0).any():
      out = False
    else:
      out = True
    return out

  def resourceUseProfile(self, selected):
    """
      Method designed to rank set of candidates based on weightFunction calculated using acitivity'slack (i.e. float)
      @ In, selected, list, lis of selected activities
      @ Out, res_usage, dict, dictionary containing the forecasted resources required to complete the selected activities.
                              Dictionay format: res_usage = {'res1': np.array, 'res2': np.array, ...}
    """
    res_usage = {}
    if selected:
      for act in selected:
        res_dict = act.returnResources()
        dur = int(act.returnDuration()-act.delay)
        for res in res_dict.keys():
          if res in res_usage.keys():
            if dur==len(res_usage[res]):
              res_usage[res] = res_usage[res] + np.ones([dur])*res_dict[res]
            elif dur>len(res_usage[res]):
              temp = np.ones([dur])*res_dict[res]
              temp[0:len(res_usage[res])] = temp[0:len(res_usage[res])] + res_usage[res]
              res_usage[res] = temp
            else: # dur<len(res_usage[res])
              temp = res_usage[res]
              temp[0:dur] = temp[0:dur] + np.ones([dur])*res_dict[res]
              res_usage[res] = temp
          else:
            res_usage[res] = np.ones([int(dur)])*res_dict[res]
    return res_usage

  def ranked(self, candidate_dict):
    """
      Method designed to rank set of candidates based on weightFunction calculated using activity slack (i.e. float)
      @ In, candidate_dict, dict, dictionary of candidate activities in the form:
                                  {activity_instance: {'duration': , 'es': , 'ef': , 'ls': , 'lf': , 'slack': , 'value': }}
      @ Out, value_dict_sorted.keys(), dict, dictionary of candidate activities sorted by weightFunction
    """
    #value_dict = {}

    #for act in candidate_dict:
    #  TF = self.infoDict[act]['slack']
    #  imp_value = weightFunction(TF)
    #  value_dict[act] = imp_value

    #value_item_sorted = sorted(value_dict.items(), key=lambda item: item[1], reverse=True)
    #value_dict_sorted = dict(value_item_sorted)

    value_dict_sorted = dict(sorted(candidate_dict.items(), key=lambda item: item[1]['value'], reverse=True))

    return value_dict_sorted.keys()

  def shuffle(self, candidate_dict):
    """
     Method designed to randomly shuffle the set of candidate activities
     @ In, candidate_list, dict, dictionary of candidate activities in the form:
                                  {activity_instance: {'duration': , 'es': , 'ef': , 'ls': , 'lf': , 'slack': , 'value': }}
     @ Out, value_dict_sorted.keys(), dict, dict of candidate activities sorted by weightFunction
    """
    value_dict = {}

    for act in candidate_dict:
      value_dict[act] = random.random()

    value_item_sorted = sorted(value_dict.items(), key=lambda item: item[1], reverse=True)
    value_dict_sorted = dict(value_item_sorted)

    return value_dict_sorted.keys()

  def updateResourceAvailability(self, res_usage, time):
    """
      Method designed to update resources availability based on planned acitivities
      @ In, res_usage, dict, dictionary containing planned resource use. Dictionay format:
                             res_usage = {'res1': np.array, 'res2': np.array, ...}
      @ Out, None
    """
    if res_usage:
      for res in res_usage:
        delta = pd.Timedelta(hours=len(res_usage[res])-1)
        self.resources.loc[time:time+delta,res] = self.resources.loc[time:time+delta,res].values - res_usage[res]

  def updateSetActivities(self, selectedActivities, candidateActivities, time_index):
    """
      Method designed to update the set of activities that at time_index were candidate to start (i.e., candidateActivities).
      A subset got selected (i.e., selectedActivities) while the remaining are postponed.
      Move the selected activities to self.ongoing, add delay to the ones that did not get selected
      @ In, candidateActivities, dict, dictionary of candidate activities in the form:
                                       {activity_instance: {'duration': , 'es': , 'ef': , 'ls': , 'lf': , 'slack': , 'value': }}
      @ In, selectedActivities, list, list of activities that got selected to start
      @ In, time_index, datetime, current time of project schedule progression
      @ Out, None
    """
    if selectedActivities:
      # Set actual start time of selected activities to time_index
      for act in selectedActivities:
        act.setActualStartTime(time_index)

      # Remove selected activities from wait
      for act in selectedActivities:
        self.wait.remove(act)

      # Move selected activities to ongoing
      self.ongoing = self.ongoing + selectedActivities

      # Increment (i.e., +1) duration for set activities that have not been selected
      postponedActivities = candidateActivities.keys() - selectedActivities
      for act in postponedActivities:
        act.addDelay()
    else:
      postponedActivities = candidateActivities.keys()
      for act in postponedActivities:
        act.addDelay()

  def updateOngoingList(self, time_index):
    """
      Method designed to update the set of ongoing activities.
      If they have been completed (i.e., time_index>= act.returnAbsTimes()[1]), the acitivity has been completed
      and hence:
      - move the activity out of self.ongoing
      - move the activity into self.completed
      @ In, time_index,
      @ Out, None
    """
    # From ongoing identify completed activities
    for act in self.ongoing:
      if time_index>= act.returnAbsTimes()[1]:
        # Move completed activities to completed
        self.completed.append(act)
        # Remove completed activities from ongoing
        self.ongoing.remove(act)

  def summarizeSchedule(self):
    """
      Method designed to save on dataframe the built project schedule
      @ In, None
      @ Out, None
    """
    actID     = []
    startTime = []
    endTime   = []
    duration  = []
    delay     = []

    for act in self.infoDict:
      actID.append(act.returnName())
      tin, tfin = act.returnAbsTimes()
      startTime.append(tin)
      endTime.append(tfin)
      delay.append(act.delay)
      duration.append(act.returnDuration()-act.delay)

    self.outageDF = pd.DataFrame({'actID': actID,
                                  'start': startTime,
                                  'end'  : endTime,
                                  'delay': delay,
                                  'duration': duration})

  def printSchedule(self, fileName=None):
    """
      Print on file the built project schedule
      @ In, None
      @ Out, None
    """
    if fileName is None:
      fileName = 'schedule.csv'
    else:
      fileName = fileName + '.csv'
    self.outageDF.to_csv(fileName, index=False)

  def plotGanttChart(self):
    """
      Method designed to plot the Gantt chart of the project schedule
      @ In, None
      @ Out, None
    """
    tin  = self.outageDF['start'].min()
    tfin = self.outageDF['end'].max()

    fig = px.timeline(self.outageDF, x_start="start", x_end="end", y="actID")
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(dtick=60*60*1000 ,tickangle=90, tickformat='%m/%d %H:%M')

    fig.update_xaxes(range=[tin, tfin])
    fileID = 'gantt.png'
    pio.write_image(fig, fileID)

  def plotResource(self, resID):
    """
      Method designed to plot the histogram temporal profile of the selected resource
      @ In, resID, string, ID of the selected resource
      @ Out, None
    """
    if resID not in self.resources.columns.tolist():
      print('Specified resource is not part of the predfined set of resources: ' + str(self.resources.columns.tolist()))
    tin  = self.outageDF['start'].min()
    tfin = self.outageDF['end'].max()
    fig  = px.bar(self.resources[resID], x=self.resources.index, y=resID)
    fig.update_xaxes(range=[tin, tfin])
    fig.update_xaxes(dtick=60*60*1000 ,tickangle=90, tickformat='%m/%d %H:%M')
    fig.update_xaxes(showgrid=True)
    fileID = resID + str('.png')
    pio.write_image(fig, fileID)


def weightFunction(TF):
  """
    Method designed to return a weight value based on the float of an activity
    @ In, TF, float, total float of the activity
    @ Out, w, float, weight value
  """
  w=1.-1./(1.+math.exp(5.-TF))
  return w

def expandSubpaths(subpaths, path):
  """
    Method designed to
    @ In, path, list, critical path
    @ In, subpaths, list, list of identified subpaths
    @ Out, expandedPaths, ,
  """
  expandedPaths = []
  for subpath in subpaths:
    idx1 = path.index(subpath[0])
    if len(subpath)==1:
      expSubpath = path[idx1-1:idx1+2]
    else:
      expSubpath = path[idx1-1:idx1+len(subpath)+1]
    expandedPaths.append(expSubpath)
  return expandedPaths

def checkForEndNode(listActivities):
  """
    Method designed to return the end (i.e., final) activity
    @ In, listActivities, list, list  of activities
    @ Out, elem, activty, schedule final activity
  """
  for elem in listActivities:
    if elem.returnName()=='end':
      return elem
  return None

def getSubpaths(path,CP):
  """
    Method designed to return the set of subpaths that are part of a path parallel to the CP
    @ In, path, list, list of activities of a path that is parallel to the critical path
    @ In, CP, list, list of activities that are part of the critical path
    @ Out, subpaths, list, list of subpaths that are part "path" parallel to "CP"
  """
  subpaths = []
  splitListRecursiveList(path, subpaths, [], CP)
  return subpaths

def splitListRecursiveList(testList, result, tempList, particularList):
  """
    Recursive method designed to split a list in sub-lists separated by elements that are included in particularList
    Source: https://www.geeksforgeeks.org/python-split-list-into-lists-by-particular-value/
    @ In, testList, list,
    @ In, result, list, lis of subpath
    @ In, tempList, list, temporary list of
    @ In, particularList, list, list of element that mark a separation between sub-lists
    @ Out, None
  """
  if not testList:
    result.append(tempList)
    return
  if testList[0] in particularList:
    result.append(tempList)
    splitListRecursiveList(testList[1:], result, [], particularList)
  else:
    splitListRecursiveList(testList[1:],
                           result,
                           tempList + [testList[0]],
                           particularList)

def graphPartitioning(G, plotting=True):
  """
    Partition a directed graph into a list of subgraphs that contain only entirely supported or entirely unsupported nodes.
    @ In, G, graph, networkx graph to be analyzed
    @ In, plotting, bool, flag to indicate if a plot should be generated
    @ Out, subgraphs, list of graph nodes that are in series
    @ Out, GminusH, set of removed edges
  """
  # Categorize nodes by their node_type attribute
  supportedNodes = {n for n, d in G.nodes(data="node_type") if d == "supported"}
  unsupportedNodes = {n for n, d in G.nodes(data="node_type") if d == "unsupported"}

  # Make a copy of the graph.
  H = G.copy()
  # Remove all edges connecting supported and unsupported nodes.
  H.remove_edges_from(
      (n, nbr, d)
      for n, nbrs in G.adj.items()
      if n in supportedNodes
      for nbr, d in nbrs.items()
      if nbr in unsupportedNodes
  )
  H.remove_edges_from(
      (n, nbr, d)
      for n, nbrs in G.adj.items()
      if n in unsupportedNodes
      for nbr, d in nbrs.items()
      if nbr in supportedNodes
  )

  # Collect all removed edges for reconstruction.
  GminusH = nx.DiGraph()
  GminusH.add_edges_from(set(G.edges) - set(H.edges))

  if plotting:
      # Plot the stripped graph with the edges removed.
      _nodeColors = [c for _, c in H.nodes(data="node_color")]
      _pos = nx.spring_layout(H)
      plt.figure(figsize=(8, 8))
      nx.draw_networkx_edges(H, _pos, alpha=0.3, edge_color="k")
      nx.draw_networkx_nodes(H, _pos, node_color=_nodeColors)
      nx.draw_networkx_labels(H, _pos, font_size=14)
      plt.axis("off")
      plt.title("The stripped graph with the edges removed.")
      plt.show()
      # Plot the edges removed.
      _pos = nx.spring_layout(GminusH)
      plt.figure(figsize=(8, 8))
      ncl = [G.nodes[n]["node_color"] for n in GminusH.nodes]
      nx.draw_networkx_edges(GminusH, _pos, alpha=0.3, edge_color="k")
      nx.draw_networkx_nodes(GminusH, _pos, node_color=ncl)
      nx.draw_networkx_labels(GminusH, _pos, font_size=14)
      plt.axis("off")
      plt.title("The removed edges.")
      plt.show()

  # Find the connected components in the stripped undirected graph.
  # And use the sets, specifying the components, to partition
  # the original directed graph into a list of directed subgraphs
  # that contain only entirely supported or entirely unsupported nodes.
  subgraphs = [
      H.subgraph(c).copy() for c in nx.connected_components(H.to_undirected())
  ]
  return subgraphs, GminusH


'''
# Example of usage of the pert class
if __name__ == "__main__":
    start = Activity("start", 5)
    a = Activity("a", 2)
    b = Activity("b", 3)
    c = Activity("c", 3)
    d = Activity("d", 4)
    e = Activity("e", 3)
    f = Activity("f", 6)
    end = Activity("end", 2)
    graph = {start: [a, d, f], a: [b], b: [c], c: [end], d: [e], e: [end], f:[end], end:[]}

    print("initialize a graph:")
    pert = Pert(graph)

    # add activity
    j = Activity("j", 16)
    print("add activity to project:")
    pert.addActivity(j, [start], [end])

    # print activity with str
    print("print activity:")
    print(j)
    print("critical path:")
    print(pert.getCriticalPath())

    # maximum shorting times
    print("maximum shorting times:")
    print(pert.shortenCriticalPath())

    # slack time for each activity
    print("slack time in descending order:")
    print(pert.getSlackForEachActivity())

    # sum of slack times
    print("sum of slack times:")
    print(pert.getSumOfSlacks())

    # iterate on the nodes with iterator
    print("iterate over all the activities with iterator:")
    for activity in iter(pert):
        print(activity)

    # isolated activities
    print("isolated activities:")
    print(pert.findIsolated())
    # print pert
    print("print pert:")
    print(pert)
'''
