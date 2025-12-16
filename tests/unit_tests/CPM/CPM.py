"""
  This Module performs Unit Tests for the utils methods
  It cannot be considered part of the active code but of the regression test system

  To run it: LOGOS/tests/unit_tests/CPM$ python CPM.py
"""

#For future compatibility with Python 3
import warnings
warnings.simplefilter('default',DeprecationWarning)

import os,sys
print(os.getcwd())
sys.path.insert(0, '../../../src/CPM/')
from PertMain2 import Pert, Activity

import numpy as np
import pandas as pd
from datetime import datetime, time

import random

results = {"pass":0,"fail":0}

def checkAnswer(comment,value,expected,tol=1e-10,updateResults=True):
  """
    This method is aimed to compare two floats given a certain tolerance
    @ In, comment, string, a comment printed out if it fails
    @ In, value, float, the value to compare
    @ In, expected, float, the expected value
    @ In, tol, float, optional, the tolerance
    @ In, updateResults, bool, optional, if True updates global results
    @ Out, None
  """
  if abs(value - expected) > tol:
    print("checking answer",comment,value,"!=",expected)
    if updateResults:
      results["fail"] += 1
    return False
  else:
    if updateResults:
      results["pass"] += 1
    return True

def checkAnswerString(comment,value,expected,updateResults=True):
  """
    This method compares two strings
    @ In, comment, string, a comment printed out if it fails
    @ In, value, string, the value to compare
    @ In, expected, string, the expected value
    @ In, updateResults, bool, optional, if True updates global results
    @ Out, None
  """
  if not value==expected:
    print("checking answer",comment,value,"!=",expected)
    if updateResults:
      results["fail"] += 1
    return False
  else:
    if updateResults:
      results["pass"] += 1
    return True

def checkArray(comment,check,expected,tol=1e-10):
  """
    This method is aimed to compare two arrays of floats given a certain tolerance
    @ In, comment, string, a comment printed out if it fails
    @ In, check, list, the value to compare
    @ In, expected, list, the expected value
    @ In, tol, float, optional, the tolerance
    @ Out, None
  """
  same=True
  if len(check) != len(expected):
    same=False
  else:
    for i in range(len(check)):
      same = same*checkAnswer(comment+'[%i]'%i,check[i],expected[i],tol,False)
  if not same:
    print("checking array",comment,"did not match!")
    results['fail']+=1
    return False
  else:
    results['pass']+=1
    return True

def checkList(comment,check,expected):
  same=True
  if len(check) != len(expected):
    same=False
  else:
    for i in range(len(check)):
      same = same*checkAnswerString(comment+'[%i]'%i,check[i],expected[i],False)
  if not same:
    print("checking list",comment,"did not match!")
    results['fail']+=1
    return False
  else:
    results['pass']+=1
    return True

def checkDicts(comment,check,expected,updateResults=True):
  """
    This method is aimed to compare two dictionaries
    @ In, comment, string, a comment printed out if it fails
    @ In, check, list, the value to compare
    @ In, expected, list, the expected value
    @ Out, None
  """
  if check == expected:
    if updateResults:
      results["pass"] += 1
    return False
  else:
    if updateResults:
      print("checking answer",comment,"Dictionaries are different")
      results["fail"] += 1
    return True


# Initialize schedule
start = Activity("start", 5)
a = Activity("a", 2)
b = Activity("b", 3)
c = Activity("c", 3)
d = Activity("d", 4)
e = Activity("e", 3)
f = Activity("f", 6)
g = Activity("g", 3)
h = Activity("h", 6)
end = Activity("end", 2)

graph = {start: [a, d, f],
         a: [b],
         b: [c],
         c: [g, h],
         d: [e],
         e: [c],
         f: [c],
         g: [end],
         h: [end],
         end:[]}

outageStartTime = datetime(2025, 4, 25, 8)

pert = Pert(graph, startTime=outageStartTime)

# Test CP
symbCPlist = pert.getCriticalPathSymbolic()
expected = ['start', 'd', 'e', 'c', 'h', 'end']
checkList('CP analysis (path)',symbCPlist,expected)

# Test end time
endTime = pert.returnScheduleEndTime()
expected = '2025-04-26 07:00:00'
checkAnswerString('CP analysis (end time)',str(endTime),expected)

# Test paths parallel to CP
paths = pert.getAllPathsParallelToCP()
expected = [['start', 'a', 'b', 'c', 'g', 'end'],
            ['start', 'a', 'b', 'c', 'h', 'end'],
            ['start', 'd', 'e', 'c', 'g', 'end'],
            ['start', 'f', 'c', 'g', 'end'],
            ['start', 'f', 'c', 'h', 'end']]
for index,path in enumerate(paths):
    checkList('CP analysis (parallel paths)',pert.returnPathSymbolic(path),expected[index])

# Test subpaths
subpaths = pert.getSubpathsParalleltoCP()
subpathList = []
for subpath in subpaths:
    subpathList.append(pert.returnPathSymbolic(subpath))
expected = [['c', 'g', 'end'],
            ['start', 'a', 'b', 'c'],
            ['start', 'f', 'c']]
subpathList.sort()
expected.sort()
for index,subpath in enumerate(subpaths):
    checkList('CP analysis (subpaths)',subpathList[index],expected[index])

# Test reduced graph
pertRed = pert.simplifyGraph()
symbCPredList = pertRed.getCriticalPathSymbolic()
expected = ['start', 'd', 'c', 'h', 'end']
checkList('CP analysis (path)',symbCPredList,expected)


# Test RCPSP
N = 30
outageStartTime = datetime(2025, 10, 20, 8)
hourly_index = pd.date_range(start=outageStartTime, periods=N, freq='h')
resources = pd.DataFrame({'res1': 1*np.ones(N)}, index=hourly_index)

# Set of activities
start = Activity("start", duration=2, res={'res1':1})
a     = Activity("a",     duration=2, res={'res1':1})
b     = Activity("b",     duration=3, res={'res1':1})
c     = Activity("c",     duration=3, res={'res1':1})
d     = Activity("d",     duration=4, res={'res1':1})
e     = Activity("e",     duration=3, res={'res1':1})
f     = Activity("f",     duration=2, res={'res1':1})
g     = Activity("g",     duration=3, res={'res1':1})
h     = Activity("h",     duration=4, res={'res1':1})
end   = Activity("end",   duration=2, res={'res1':1})

# Activity dependencies
graph = {start: [a, d, f],
         a: [b],
         b: [c],
         c: [g, h],
         d: [e],
         e: [c],
         f: [c],
         g: [end],
         h: [end],
         end:[]}

# Test RCPSP - 1
priorities={}
random.seed(42)
for act in graph:
    priorities[act] = random.random()

pert = Pert(graph, startTime=outageStartTime, resourcesTS=resources, priorities=priorities)
pert.calculateScheduleWithResources('MD-Knapsack')
outageSchedule = pert.outageDF.to_dict()

outageSchedule_gold_1 = {'actID': { 0: 'start',
                                    1: 'a',
                                    2: 'b',
                                    3: 'c',
                                    4: 'd',
                                    5: 'e',
                                    6: 'f',
                                    7: 'g',
                                    8: 'h',
                                    9: 'end'},
                        'start': {0: pd.Timestamp('2025-10-20 08:00:00'),
                          1: pd.Timestamp('2025-10-20 10:00:00'),
                          2: pd.Timestamp('2025-10-20 12:00:00'),
                          3: pd.Timestamp('2025-10-21 00:00:00'),
                          4: pd.Timestamp('2025-10-20 15:00:00'),
                          5: pd.Timestamp('2025-10-20 19:00:00'),
                          6: pd.Timestamp('2025-10-20 22:00:00'),
                          7: pd.Timestamp('2025-10-21 03:00:00'),
                          8: pd.Timestamp('2025-10-21 06:00:00'),
                          9: pd.Timestamp('2025-10-21 10:00:00')},
                        'end': {0: pd.Timestamp('2025-10-20 10:00:00'),
                          1: pd.Timestamp('2025-10-20 12:00:00'),
                          2: pd.Timestamp('2025-10-20 15:00:00'),
                          3: pd.Timestamp('2025-10-21 03:00:00'),
                          4: pd.Timestamp('2025-10-20 19:00:00'),
                          5: pd.Timestamp('2025-10-20 22:00:00'),
                          6: pd.Timestamp('2025-10-21 00:00:00'),
                          7: pd.Timestamp('2025-10-21 06:00:00'),
                          8: pd.Timestamp('2025-10-21 10:00:00'),
                          9: pd.Timestamp('2025-10-21 12:00:00')},
                        'delay': {0: 0.0,
                          1: 0.0,
                          2: 0.0,
                          3: 0.0,
                          4: 5.0,
                          5: 0.0,
                          6: 12.0,
                          7: 0.0,
                          8: 3.0,
                          9: 0.0},
                        'duration': {0: 2.0,
                          1: 2.0,
                          2: 3.0,
                          3: 3.0,
                          4: 4.0,
                          5: 3.0,
                          6: 2.0,
                          7: 3.0,
                          8: 4.0,
                          9: 2.0}}

checkDicts('Test RCPSP - 1', outageSchedule, outageSchedule_gold_1, updateResults=True)

# Test RCPSP - 2
# Set of activities
start = Activity("start", duration=2, res={'res1':1})
a     = Activity("a",     duration=2, res={'res1':1})
b     = Activity("b",     duration=3, res={'res1':1})
c     = Activity("c",     duration=3, res={'res1':1})
d     = Activity("d",     duration=4, res={'res1':1})
e     = Activity("e",     duration=3, res={'res1':1})
f     = Activity("f",     duration=2, res={'res1':1})
g     = Activity("g",     duration=3, res={'res1':1})
h     = Activity("h",     duration=4, res={'res1':1})
end   = Activity("end",   duration=2, res={'res1':1})

# Activity dependencies
graph = {start: [a, d, f],
         a: [b],
         b: [c],
         c: [g, h],
         d: [e],
         e: [c],
         f: [c],
         g: [end],
         h: [end],
         end:[]}

priorities={}
random.seed(42)
for act in graph:
    priorities[act] = random.random()

N = 30
outageStartTime = datetime(2025, 10, 20, 8)
hourly_index = pd.date_range(start=outageStartTime, periods=N, freq='h')
resources = pd.DataFrame({'res1': 1*np.ones(N)}, index=hourly_index)

pert_2 = Pert(graph, startTime=outageStartTime, resourcesTS=resources, priorities=priorities)
pert_2.calculateScheduleWithResources('max_use_res_ranked')
outageSchedule_2 = pert_2.outageDF.to_dict()

outageSchedule_gold_2 = {'actID': {0: 'start',
  1: 'a',
  2: 'b',
  3: 'c',
  4: 'd',
  5: 'e',
  6: 'f',
  7: 'g',
  8: 'h',
  9: 'end'},
 'start': {0: pd.Timestamp('2025-10-20 08:00:00'),
  1: pd.Timestamp('2025-10-20 19:00:00'),
  2: pd.Timestamp('2025-10-20 21:00:00'),
  3: pd.Timestamp('2025-10-21 00:00:00'),
  4: pd.Timestamp('2025-10-20 12:00:00'),
  5: pd.Timestamp('2025-10-20 16:00:00'),
  6: pd.Timestamp('2025-10-20 10:00:00'),
  7: pd.Timestamp('2025-10-21 07:00:00'),
  8: pd.Timestamp('2025-10-21 03:00:00'),
  9: pd.Timestamp('2025-10-21 10:00:00')},
 'end': {0: pd.Timestamp('2025-10-20 10:00:00'),
  1: pd.Timestamp('2025-10-20 21:00:00'),
  2: pd.Timestamp('2025-10-21 00:00:00'),
  3: pd.Timestamp('2025-10-21 03:00:00'),
  4: pd.Timestamp('2025-10-20 16:00:00'),
  5: pd.Timestamp('2025-10-20 19:00:00'),
  6: pd.Timestamp('2025-10-20 12:00:00'),
  7: pd.Timestamp('2025-10-21 10:00:00'),
  8: pd.Timestamp('2025-10-21 07:00:00'),
  9: pd.Timestamp('2025-10-21 12:00:00')},
 'delay': {0: 0.0,
  1: 9.0,
  2: 0.0,
  3: 0.0,
  4: 2.0,
  5: 0.0,
  6: 0.0,
  7: 4.0,
  8: 0.0,
  9: 0.0},
 'duration': {0: 2.0,
  1: 2.0,
  2: 3.0,
  3: 3.0,
  4: 4.0,
  5: 3.0,
  6: 2.0,
  7: 3.0,
  8: 4.0,
  9: 2.0}}

checkDicts('Test RCPSP - 2', outageSchedule_2, outageSchedule_gold_2, updateResults=True)

# Test RCPSP - 3
start = Activity("start", duration=2, res={'res1':1})
a     = Activity("a",     duration=2, res={'res1':1})
b     = Activity("b",     duration=3, res={'res1':1})
c     = Activity("c",     duration=3, res={'res1':1})
d     = Activity("d",     duration=4, res={'res1':1})
e     = Activity("e",     duration=3, res={'res1':1})
f     = Activity("f",     duration=2, res={'res1':1})
g     = Activity("g",     duration=3, res={'res1':1})
h     = Activity("h",     duration=4, res={'res1':1})
end   = Activity("end",   duration=2, res={'res1':1})

# Activity dependencies
graph = {start: [a, d, f],
         a: [b],
         b: [c],
         c: [g, h],
         d: [e],
         e: [c],
         f: [c],
         g: [end],
         h: [end],
         end:[]}

N = 30
outageStartTime = datetime(2025, 10, 20, 8)
hourly_index = pd.date_range(start=outageStartTime, periods=N, freq='h')
resources = pd.DataFrame({'res1': 1*np.ones(N)}, index=hourly_index)

pert_3 = Pert(graph, startTime=outageStartTime, resourcesTS=resources)
pert_3.calculateScheduleWithResources('max_use_res_act')
outageSchedule_3 = pert_3.outageDF.to_dict()

outageSchedule_gold_3 = {'actID': {0: 'start',
  1: 'a',
  2: 'b',
  3: 'c',
  4: 'd',
  5: 'e',
  6: 'f',
  7: 'g',
  8: 'h',
  9: 'end'},
 'start': {0: pd.Timestamp('2025-10-20 08:00:00'),
  1: pd.Timestamp('2025-10-20 10:00:00'),
  2: pd.Timestamp('2025-10-20 12:00:00'),
  3: pd.Timestamp('2025-10-21 00:00:00'),
  4: pd.Timestamp('2025-10-20 15:00:00'),
  5: pd.Timestamp('2025-10-20 19:00:00'),
  6: pd.Timestamp('2025-10-20 22:00:00'),
  7: pd.Timestamp('2025-10-21 03:00:00'),
  8: pd.Timestamp('2025-10-21 06:00:00'),
  9: pd.Timestamp('2025-10-21 10:00:00')},
 'end': {0: pd.Timestamp('2025-10-20 10:00:00'),
  1: pd.Timestamp('2025-10-20 12:00:00'),
  2: pd.Timestamp('2025-10-20 15:00:00'),
  3: pd.Timestamp('2025-10-21 03:00:00'),
  4: pd.Timestamp('2025-10-20 19:00:00'),
  5: pd.Timestamp('2025-10-20 22:00:00'),
  6: pd.Timestamp('2025-10-21 00:00:00'),
  7: pd.Timestamp('2025-10-21 06:00:00'),
  8: pd.Timestamp('2025-10-21 10:00:00'),
  9: pd.Timestamp('2025-10-21 12:00:00')},
 'delay': {0: 0.0,
  1: 0.0,
  2: 0.0,
  3: 0.0,
  4: 5.0,
  5: 0.0,
  6: 12.0,
  7: 0.0,
  8: 3.0,
  9: 0.0},
 'duration': {0: 2.0,
  1: 2.0,
  2: 3.0,
  3: 3.0,
  4: 4.0,
  5: 3.0,
  6: 2.0,
  7: 3.0,
  8: 4.0,
  9: 2.0}}

checkDicts('Test RCPSP - 3', outageSchedule_3, outageSchedule_gold_3, updateResults=True)

print(results)

sys.exit(results["fail"])

