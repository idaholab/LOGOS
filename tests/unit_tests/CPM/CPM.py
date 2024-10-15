"""
  This Module performs Unit Tests for the utils methods
  It cannot be considered part of the active code but of the regression test system
"""

#For future compatibility with Python 3
from __future__ import division, print_function, unicode_literals, absolute_import
import warnings
warnings.simplefilter('default',DeprecationWarning)

import os,sys
import numpy as np

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
  if len(check) != len(expected):
    same=False
  else:
    for i in range(len(check)):
      same = same*checkAnswerString(comment+'[%i]'%i,check[i],expected[i],tol,False)
  if not same:
    print("checking array",comment,"did not match!")
    results['fail']+=1
    return False
  else:
    results['pass']+=1
    return True


# check getRelativeSortedListEntry
toPopulate = [0.8, 0.002, 0.0003, 0.9, 0.85, 0.799999999999, 0.90000001, 0.00029999999999]
#populating these in this order tests adding new entries to the front (0.0003), back (0.9), and middle (0.85),
#  as well as adding matches in the front (0.00029...), back (0.90...1), and middle (0.79...)
desired = [0.0003, 0.002, 0.8, 0.85, 0.9]
sortedList = []
for x in toPopulate:
  sortedList,index,match = utils.getRelativeSortedListEntry(sortedList,x,tol=1e-6)
checkArray('Maintaining sorted list',sortedList,desired)


# partial string formatting
s = '{a} {b} {a}'
correct = 'one {b} one'
got = s.format_map(utils.StringPartialFormatDict(a='one'))
checkTrue('Partial string formatting', got, correct)

s = '{a:3s} {b:2d} {c:3s}'
correct = '{a}  2 {c}'
got = utils.partialFormat(s, {'b': 2})
checkTrue('Partial string formatting 2', got, correct)

checkTrue('Partial string formatting 2', got, correct)

print(results)

sys.exit(results["fail"])

