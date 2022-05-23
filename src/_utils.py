# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
  utilities for use within LOGOS
"""
import os
import sys
import importlib
import xml.etree.ElementTree as ET

def getRavenLoc():
  """
    Return RAVEN location: read from LOGOS/.ravenconfig.xml
    @ In, None
    @ Out, loc, string, absolute location of RAVEN
  """
  config = os.path.abspath(os.path.join(os.path.dirname(__file__),'..','.ravenconfig.xml'))
  if not os.path.isfile(config):
    raise IOError('LOGOS config file not found at "{}"! Has LOGOS been installed as a plugin in a RAVEN installation?'
                  .format(config))
  loc = ET.parse(config).getroot().find('FrameworkLocation').text
  return loc

def getPluginLoc(ravenPath=None, plugin='LOGOS'):
  """
    Get CashFlow location in installed RAVEN
    @ In, ravenPath, string, optional, if given then start with this path
    @ In, plugin, string, optional, the name of plugin for the request
    @ Out, pluginLoc, string, location of plugin
  """
  if ravenPath is None:
    ravenPath = getRavenLoc()
  phDir = os.path.join(ravenPath, '..', 'scripts')
  sys.path.append(phDir)
  ph = importlib.import_module('plugin_handler')
  sys.path.pop()
  pluginLoc = ph.getPluginLocation(plugin)
  return pluginLoc

if __name__ == '__main__':
  action = sys.argv[1]
  if action == 'getRavenLoc':
    print(getRavenLoc())
  elif action == 'getPluginLoc':
    print(getPluginLoc(plugin='LOGOS'))
  else:
    raise IOError('Unrecognized action: "{}"'.format(action))
