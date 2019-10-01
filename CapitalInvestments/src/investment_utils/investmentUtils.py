#
#
#
"""
  Created on April 4, 2019
  @author: wangc, mandd
"""
#for future compatibility with Python 3--------------------------------------------------------------
from __future__ import division, print_function, unicode_literals, absolute_import
import warnings
warnings.simplefilter('default',DeprecationWarning)
#End compatibility block for Python 3----------------------------------------------------------------

#External Modules------------------------------------------------------------------------------------
import numpy as np
import logging
import xml.etree.ElementTree as ET
#External Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def convertNodeTextToList(nodeText, sep=None):
  """
    Convert space or comma separated string to list of string
    @ In, nodeText, str, string from xml node text
    @ Out, listData, list, list of strings
  """
  listData = None
  if sep is None:
    if ',' in nodeText:
      listData = list(elem.strip() for elem in nodeText.split(','))
    else:
      listData = list(elem.strip() for elem in nodeText.split())
  else:
    listData = list(elem.strip() for elem in nodeText.split(sep))
  return listData

def convertNodeTextToFloatList(nodeText, sep=None):
  """
    Convert space or comma separated string to list of float
    @ In, nodeText, str, string from xml node text
    @ Out, listData, list, list of floats
  """
  listData = None
  if sep is None:
    if ',' in nodeText:
      listData = list(float(elem) for elem in nodeText.split(','))
    else:
      listData = list(float(elem) for elem in nodeText.split())
  else:
    listData = list(elem.strip() for elem in nodeText.split(sep))
  return listData

def convertStringToFloat(xmlNode):
  """
    Convert xml node text to float
  """
  try:
    val = float(xmlNode.text)
    return val
  except (ValueError,TypeError):
    raise IOError('Real value is required for content of node %s, but got %s' %(node.tag, node.text))

def convertStringToInt(xmlNode):
  """
    Convert xml node text to integer.
  """
  try:
    return int(xmlNode.text)
  except (ValueError,TypeError):
    raise IOError('Integer value is required for content of node %s, but got %s' %(node.tag, node.text))

def toString(s):
  """
    Method aimed to convert a string in type str
    @ In, s, string,  string to be converted
    @ Out, response, string, the casted value
  """
  if type(s) == type(""):
    return s
  else:
    return s.decode()

def convertStringToBool(nodeText):
  """
    Convert string to bool
    @ In, nodeText, str, string from xml node text
    @ Out, , bool, True or False
  """
  stringsThatMeanTrue = list(['yes','y','true','t','on'])
  if nodeText.lower() in stringsThatMeanTrue:
    return True
  else:
    return False
