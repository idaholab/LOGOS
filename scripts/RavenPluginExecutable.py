
"""
Tests by running an executable.
"""
import os
import sys
from RavenFramework import RavenFramework

myDir = os.path.dirname(os.path.realpath(__file__))
RavenPluginDir = os.path.abspath(os.path.join(myDir, '..', '..', '..', 'plugins'))
RavenTesterDir = os.path.abspath(os.path.join(myDir, '..', '..', '..', 'scripts', 'TestHarness', 'testers'))
sys.path.append(RavenTesterDir)

class RavenPluginExecutable(RavenFramework):
  """
  A Raven plugin executable test interface.
  """

  @staticmethod
  def get_valid_params():
    """
      Return a list of valid parameters and their descriptions for this type
      of test.
      @ In, None
      @ Out, params, _ValidParameters, the parameters for this class.
    """
    params = RavenFramework.get_valid_params()
    params.add_param('parameters', '', "arguments to the executable")
    return params

  def get_command(self):
    """
      Return the command this test will run.
      @ In, None
      @ Out, get_command, string, command to run
    """
    return self.required_executable + " " + self.specs["parameters"] + " " + self.specs["input"]

  def __init__(self, name, params):
    """
        Constructor that will setup this test with a name and a list of
        parameters.
        @ In, name: the name of this test case.
        @ In, params, a dictionary of parameters and their values to use.
    """
    RavenFramework.__init__(self, name, params)
    self.required_executable = os.path.abspath(os.path.join(RavenPluginDir, self.required_executable))
