#
#
#
"""
Created on Feb. 21, 2019
@author: wangc, mandd
"""
#for future compatibility with Python 3-----------------------------------------
from __future__ import division, print_function, unicode_literals, absolute_import
import warnings
warnings.simplefilter('default',DeprecationWarning)
#End compatibility block for Python 3-------------------------------------------

################################################################################
try:
  from Logos.src.CapitalInvestments.PyomoModels.SingleKnapsack import SingleKnapsack
  from Logos.src.CapitalInvestments.PyomoModels.MultipleKnapsack import MultipleKnapsack
  from Logos.src.CapitalInvestments.PyomoModels.MCKP import MCKP
except ImportError:
  from .SingleKnapsack import SingleKnapsack
  from .MultipleKnapsack import MultipleKnapsack
  from .MCKP import MCKP

"""
 Interface Dictionary (factory) (private)
"""
__base = 'ModelBase'
__interfaceDict = {}
__interfaceDict['singleknapsack'           ] = SingleKnapsack
__interfaceDict['multipleknapsack'         ] = MultipleKnapsack
__interfaceDict['mckp'                     ] = MCKP


def knownTypes():
  """
    Returns a list of strings that define the types of instantiable objects for
    this base factory.
    @ In, None
    @ Out, knownTypes, list, the known types
  """
  return __interfaceDict.keys()

def returnInstance(classType):
  """
    Attempts to create and return an instance of a particular type of object
    available to this factory.
    @ In, classType, string, string should be one of the knownTypes.
    @ Out, returnInstance, instance, subclass object constructed with no arguments
  """
  return returnClass(classType)()

def returnClass(classType):
  """
    Attempts to return a particular class type available to this factory.
    @ In, classType, string, string should be one of the knownTypes.
    @ Out, returnClass, class, reference to the subclass
  """
  try:
    return __interfaceDict[classType.lower()]
  except KeyError:
    raise IOError(__name__ + ': unknown ' + __base + ' type ' + classType)
