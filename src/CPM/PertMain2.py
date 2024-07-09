# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED

import math
import copy
import networkx as nx
import matplotlib.pyplot as plt
import itertools

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
    self.subActivities = []
    self.belongsToCP = False

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

  def returnSubActivities(self):
    """
      Methods that returns the list of subactivities 
      @ In, None
      @ Out, subActivities, list, list of subactivities
    """
    return self.subActivities

  def addSubActivities(self, subActivities):
    """
      Methods that associate a list of subactivities 
      @ In, subActivities, list, list of subactivities
      @ Out, None
    """
    self.subActivities = subActivities
    tempDuration = 0.
    for act in subActivities:
      tempDuration += act.returnDuration()
    self.duration = tempDuration

  def setOnCP(self):
    self.belongsToCP = True

  def returnCPstatus(self):
    return self.belongsToCP


class Pert:
  """
    This is the base class for a schedule as a set of activities linked by a graph structure
    A graph is a map with activities as keys and list of outgoing activities as value for every key
    The graph starts with a 'start' node and ends with a 'end' node.
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
    self.resetInitialGraph()   # first reset of the graph
    self.generateInfo()        # entering values into 'info_dict'

    # str method for pert
  def __str__(self):
    """
      Method designed to return basic information of the schedule graph
      @ In, None
      @ Out, None
    """
    iterator = iter(self)
    graph_str = 'Activities:\n'
    for activity in iterator:
      graph_str += str(activity) + '\n'
    return (graph_str + 'Connections:\n'
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
    slacks = {activity.returnName(): self.infoDict[activity]["slack"] for activity in self.infoDict if self.infoDict[activity]["slack"] != 0}
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
    CP = self.getCriticalPath()
    pathsList = self.getAllAlternativePaths(CP[0], CP[-1])
    pathsList.remove(CP)
    return pathsList
  
  def returnSuccList(self,node):
    """
      Method designed to return the immediate successors of a node
      @ In, node, activity, activity being queried
      @ Out, list, list of activities that are immediate successors of "node"
    """
    return list(self.forwardDict[node])
  
  def returnNumberSucc(self,node):
    """
      Method designed to return the number of immediate successors of a node
      @ In, node, activity, activity being queried
      @ Out, int, number activities that are immediate successors of "node"
    """
    return len(list(self.forwardDict[node]))

  def returnPredList(self,node):
    """
      Method designed to return the immediate predecessors of a node
      @ In, node, activity, activity being queried
      @ Out, list, list of activities that are immediate predecessors of "node"
    """
    return (self.backwardDict[node])

  def returnNumberPred(self,node):
    """
      Method designed to return the number of immediate predecessors of a node
      @ In, node, activity, activity being queried
      @ Out, int, number activities that are immediate predecessors of "node"
    """
    return len((self.backwardDict[node]))  
  
  def returnSubActivities(self, node):
    return node.returnSubActivities()
  
  def deleteActivity(self,node):
    """
      Method designed to remove an activity from a schedule
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

    #UG = G.to_undirected()
    #A = (UG.subgraph(c) for c in nx.connected_components(UG))

    subgraphs_of_G_ex, removed_edges = graphPartitioning(G, plotting=False)
    listSeries = list(subgraphs_of_G_ex)

    for series in listSeries:
        temp = list(nx.topological_sort(series))
        succOFSeries = list(updatedGraph[temp[-1]])
        for node in list(series.nodes):
            reducedPertModel.deleteActivity(node)
        reducedPertModel.updateMergedSeries(temp[0], succOFSeries, temp)
    
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
    CP = self.getCriticalPath() 
    paths = self.getAllPathsParallelToCP()
    subpathsSet = []
    for path in paths:
      subpaths = getSubpaths(path,CP)
      subpathsSet = subpathsSet + subpaths
    
    b_set = set(map(tuple,subpathsSet)) 
    subpathsSetRed = list(map(list,b_set)) 
    subpathsSetRed.remove([])
    return subpathsSetRed
  
  def printPathSymbolic(self, path):
    symbPath = ''
    for act in path:
      symbPath = symbPath + '-' + str(act.name)
    print(symbPath)

  
'''  def getSubpathsParalleltoCP(self, CP, paths):
    subpaths = []

    pathsOrdered = paths.sort(key=len,reverse=True)
    
    for path in pathsOrdered:
      subpath = list(set(path) - set(CP))
      subpaths.append(subpath)
    
    subpathsList = set()
    for i, subpath in enumerate(subpaths):
      for j in range(i+1,len(subpaths)):
        if set(subpath).issubset(subpaths[j]):
          subpathsList.add()
        else:      
          pass'''

def getSubpaths(path,CP):
  subpaths = []
  split_list_recursive_list(path, subpaths, [], CP)
  return subpaths


def split_list_recursive_list(test_list, result, temp_list, particular_list):
  # Source: https://www.geeksforgeeks.org/python-split-list-into-lists-by-particular-value/
  if not test_list:
    result.append(temp_list)
    return
  if test_list[0] in particular_list:
    result.append(temp_list)
    split_list_recursive_list(test_list[1:], result, [], particular_list)
  else:
    split_list_recursive_list(test_list[1:],
                              result,
                              temp_list + [test_list[0]],
                              particular_list)


  
def graphPartitioning(G, plotting=True):
  """Partition a directed graph into a list of subgraphs that contain
  only entirely supported or entirely unsupported nodes.
  """
  # Categorize nodes by their node_type attribute
  supported_nodes = {n for n, d in G.nodes(data="node_type") if d == "supported"}
  unsupported_nodes = {n for n, d in G.nodes(data="node_type") if d == "unsupported"}

  # Make a copy of the graph.
  H = G.copy()
  # Remove all edges connecting supported and unsupported nodes.
  H.remove_edges_from(
      (n, nbr, d)
      for n, nbrs in G.adj.items()
      if n in supported_nodes
      for nbr, d in nbrs.items()
      if nbr in unsupported_nodes
  )
  H.remove_edges_from(
      (n, nbr, d)
      for n, nbrs in G.adj.items()
      if n in unsupported_nodes
      for nbr, d in nbrs.items()
      if nbr in supported_nodes
  )

  # Collect all removed edges for reconstruction.
  G_minus_H = nx.DiGraph()
  G_minus_H.add_edges_from(set(G.edges) - set(H.edges))

  if plotting:
      # Plot the stripped graph with the edges removed.
      _node_colors = [c for _, c in H.nodes(data="node_color")]
      _pos = nx.spring_layout(H)
      plt.figure(figsize=(8, 8))
      nx.draw_networkx_edges(H, _pos, alpha=0.3, edge_color="k")
      nx.draw_networkx_nodes(H, _pos, node_color=_node_colors)
      nx.draw_networkx_labels(H, _pos, font_size=14)
      plt.axis("off")
      plt.title("The stripped graph with the edges removed.")
      plt.show()
      # Plot the edges removed.
      _pos = nx.spring_layout(G_minus_H)
      plt.figure(figsize=(8, 8))
      ncl = [G.nodes[n]["node_color"] for n in G_minus_H.nodes]
      nx.draw_networkx_edges(G_minus_H, _pos, alpha=0.3, edge_color="k")
      nx.draw_networkx_nodes(G_minus_H, _pos, node_color=ncl)
      nx.draw_networkx_labels(G_minus_H, _pos, font_size=14)
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

  return subgraphs, G_minus_H


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
