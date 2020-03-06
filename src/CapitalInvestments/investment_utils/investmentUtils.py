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
import os
import errno
import imp
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
    @ In, xmlNode, xml.etree.ElementTree.Element, xml element
    @ Out, val, float, value of xml element text
  """
  try:
    val = float(xmlNode.text)
    return val
  except (ValueError,TypeError):
    raise IOError('Real value is required for content of node %s, but got %s' %(node.tag, node.text))

def convertStringToInt(xmlNode):
  """
    Convert xml node text to integer.
    @ In, xmlNode, xml.etree.ElementTree.Element, xml element
    @ Out, val, integer, value of xml element text
  """
  try:
    val = int(xmlNode.text)
    return val
  except (ValueError,TypeError):
    raise IOError('Integer value is required for content of node %s, but got %s' %(node.tag, node.text))

def toString(s):
  """
    Method aimed to convert a string in type str
    @ In, s, string,  string to be converted
    @ Out, s, string, the casted value
  """
  if type(s) == type(""):
    return s
  else:
    return s.decode()

def convertStringToBool(nodeText):
  """
    Convert string to bool
    @ In, nodeText, str, string from xml node text
    @ Out, val, bool, True or False
  """
  stringsThatMeanTrue = list(['yes','y','true','t','on'])
  val = False
  if nodeText.lower() in stringsThatMeanTrue:
    val = True
  return val

def identifyIfExternalModuleExists(moduleIn, workingDir):
  """
    Method to check if a external module exists and in case return the module that needs to be loaded with
    the correct path
    @ In, moduleIn, string, module read from the XML file
    @ In, workingDir, string, the path of the working directory
    @ Out, (moduleToLoad, fileName), tuple, a tuple containing the module to load (that should be used in
      method importFromPath) and the filename (no path)
  """
  if moduleIn.endswith('.py'):
    moduleToLoadString = moduleIn[:-3]
  else:
    moduleToLoadString = moduleIn
  workingDirModule = os.path.abspath(os.path.join(workingDir,moduleToLoadString))
  if os.path.exists(workingDirModule + ".py"):
    moduleToLoadString = workingDirModule
    path, filename = os.path.split(workingDirModule)
    os.sys.path.append(os.path.abspath(path))
  else:
    path, filename = os.path.split(moduleToLoadString)
    if (path != ''):
      abspath = os.path.abspath(path)
      if '~' in abspath:
        abspath = os.path.expanduser(abspath)
      if os.path.exists(abspath):
        os.sys.path.append(abspath)
      else:
        raise IOError('The file "{}" provided does not exist!'.format(moduleIn))
  return moduleToLoadString, filename

def importFromPath(filename):
  """
    Method to import a module from a given path
    @ In, filename, str, the full path of the module to import
    @ Out, importedModule, module, the imported module
  """
  try:
    path, name = os.path.split(filename)
    name, ext = os.path.splitext(name)
    file, filename, data = imp.find_module(name, [path])
    importedModule = imp.load_module(name, file, filename, data)
  except Exception as ae:
    raise Exception('Importing module '+ filename + ' at ' + path + os.sep + name + ' failed with error '+ str(ae))
  return importedModule

def makeDir(dirName):
  """
    Function that will attempt to create a directory. If the directory already
    exists, this function will return silently with no error, however if it
    fails to create the directory for any other reason, then an error is
    raised.
    @ In, dirName, string, specifying the new directory to be created
    @ Out, None
  """
  try:
    os.makedirs(dirName)
  except OSError as exc:
    if exc.errno == errno.EEXIST and os.path.isdir(dirName):
      ## The path already exists so we can safely ignore this exception
      pass
    else:
      ## If it failed for some other reason, we want to see what the
      ## error is still
      raise
