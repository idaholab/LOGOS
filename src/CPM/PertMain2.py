# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED

import math
import copy

class Activity:
  """
    This is the base class for a single activity
    Extended from the original development of Nofar Alfasi
    Source https://github.com/nofaralfasi/PERT-CPM-graph
  """
  def __init__(self, name, duration):
    """
      Constructor
      @ In, name, str, ID of the activity
      @ Out, None
    """
    self.name = str(name)
    self.duration = duration

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

  def updateDuration(self, newDuration):
    """
      Methods that changes the duration of the activity
      @ In, newDuration, str, updated duration of the activity
      @ Out, None
    """
    self.duration = copy.deepcopy(newDuration)

class Pert:
  """
    This is the base class for a schedule as a set of activities linked by a graph structure
    A graph is a map with activities as keys and list of outgoing activities as value for every key
    The graph starts with a 'start' node and ends with a 'end' node
    Extended from the original development of Nofar Alfasi
    Source https://github.com/nofaralfasi/PERT-CPM-graph
  """

  def __init__(self, graph={}):
    """
      Constructor
      @ In, None
      @ Out, None
    """
    self.forwardDict = graph   # list of out going nodes for every activity
    self.backwardDict = {}     # list of in going nodes for every activity
    self.infoDict = {}         # map of details for every activity
    self.startActivity = Activity
    self.endActivity = Activity
    self.resetInitialGraph()  # first reset of the graph
    self.generateInfo()        # entering values into 'info_dict'

    # str method for pert
  def __str__(self):
    """
      Method designed to returun basic information of the schedule graph
      @ In, None
      @ Out, None
    """
    iterator = iter(self)
    graph_str = 'Activities:\n'
    for activity in iterator:
      graphStr += str(activity) + '\n'
    return (graphStr + 'Connections:\n'
      + str(self.forwardDict)
      + '\nProject Duration:\n'
      + str(self.infoDict[self.endActivity]['ef']))

  # iterator for the pert class
  def __iter__(self):
    return iter(self.forwardDict)

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
  def addActivity(self, activity, in_connections=[], out_connections=[]):
    """
      Method designed to add a new activity to an exisiting schedule
      @ In, activity, activity, activitiy to be added
      @ In, in_connections, list, list of activities arriving into new activity
      @ In, out_connections, list, list of activities departing from new activity
      @ Out, None
    """
    if activity in self.forwardDict:
      return
    self.forwardDict[activity] = out_connections
    self.backwardDict[activity] = in_connections
    if in_connections != []:
      for node in in_connections:
        if self.forwardDict[node] is None:
          self.forwardDict[node] = []
        self.forwardDict[node] += [activity]
    if out_connections != []:
      for node in out_connections:
        if self.backwardDict[node] is None:
          self.backwardDict[node] = []
        self.backwardDict[node] += [activity]
    self.infoDict[activity] = {
      "duration": activity.duration,
      "es": 0, "ef": 0, "ls": 0, "lf": math.inf,
      "slack": 0}
    self.resetInfo()
    self.generateInfo()

  # find isolated activities
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
    slacks = {activity: self.infoDict[activity]["slack"] for activity in self.infoDict if self.infoDict[activity]["slack"] != 0}
    slackVals = sorted(slacks.items(), key=lambda kv: kv[1], reverse=True)
    return slackVals

  # get the sum of all the slacks in the project
  def getSumOfSlacks(self):
    """
      Get sum of the slack values for all activities
      @ In, None
      @ Out, sumSlacks, float, sum of the slack values for all activities
    """
    slacks = [kv[1] for kv in self.getSlackForEachActivity()]
    sumSlacks = sum(slacks)
    return sumSlacks

  # get the critical path as list
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
    critical_path = self.getCriticalPath()
    maxDecreaseToActivities = {activity: activity.duration - 1 for activity in critical_path}
    for i in range(0,  len(critical_path), 1):
      for j in range(2, len(critical_path) - i, 1):
        for path in self.getAllAlternativePaths(critical_path[i], critical_path[i + j]):
          for activity in critical_path[i + 1 : i + j : 1]:
            if path[1] not in critical_path and maxDecreaseToActivities[activity] >= self.infoDict[path[1]]["slack"]:
              maxDecreaseToActivities[activity] = self.infoDict[path[1]]["slack"] - 1
    return maxDecreaseToActivities

  def getAllAlternativePaths(self, startActivity, endActivity, path=[]):
    """
      Get all the paths between 2 nodes (activities) in the graph (pert)
      @ In, None
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
    return paths

'''
# Example of usegae of the pert class
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
