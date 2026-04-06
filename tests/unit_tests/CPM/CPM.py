"""
  This Module performs Unit Tests for the CPM methods
  It cannot be considered part of the active code but of the regression test system

  To run it: LOGOS/tests/unit_tests/CPM$ python CPM.py
"""

#For future compatibility with Python 3
import warnings
warnings.simplefilter('default',DeprecationWarning)

import sys
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from src.CPM.pert import Pert
from src.CPM.activity import Activity

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

pert = Pert(graph)
pert.startTime = outageStartTime

# Test CP
symbCPlist = pert.getCriticalPathSymbolic()
expected = ['start', 'd', 'e', 'c', 'h', 'end']
checkList('CP analysis (path)',symbCPlist,expected)

# Test end time
endTime = pert.returnScheduleEndTime()
expected = '2025-04-26 07:00:00'
checkAnswerString('CP analysis (end time)',str(endTime),expected)


print(results)

sys.exit(results["fail"])
