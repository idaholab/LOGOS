"""
  This Module performs Unit Tests for the utils methods
  It cannot be considered part of the active code but of the regression test system
"""

#For future compatibility with Python 3
from __future__ import division, print_function, unicode_literals, absolute_import
import warnings
warnings.simplefilter('default',DeprecationWarning)

import os,sys
sys.path.insert(0, '../../../src/CPM/')
from PertMain2 import Pert, Activity

import numpy as np
from datetime import datetime, time

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

print(results)

sys.exit(results["fail"])

