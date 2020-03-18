
"""
Tests by running an executable.
"""
import os
import sys

LOGOS_LOC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(LOGOS_LOC)
import _utils as logos_utils
sys.path.pop()

# get RAVEN base testers
RAVEN_FRAMEWORK_LOC = logos_utils.get_raven_loc()
TESTER_LOC = os.path.join(RAVEN_FRAMEWORK_LOC, '..', 'scripts', 'TestHarness', 'testers')
sys.path.append(TESTER_LOC)
from RavenFramework import RavenFramework as RavenTester
sys.path.pop()


class LogosRun(RavenTester):
  """
    A Logos stand-alone test interface.
  """

  @staticmethod
  def get_valid_params():
    """
      Return a list of valid parameters and their descriptions for this type
      of test.
      @ In, None
      @ Out, params, _ValidParameters, the parameters for this class.
    """
    params = RavenTester.get_valid_params()
    params.add_param('parameters', '-i', 'Input argument to LOGOS')
    return params

  def __init__(self, name, param):
    """
      Constructor.
      @ In, name, str, name of test
      @ In, params, dict, test parameters
      @ Out, None
    """
    RavenTester.__init__(self, name, param)
    self.logos_driver = os.path.join(LOGOS_LOC, 'logos_main.py')

  def get_command(self):
    """
      Return the command this test will run.
      @ In, None
      @ Out, cmd, string, command to run
    """
    cmd = ''
    pythonCmd = self._get_python_command()
    cmd = pythonCmd + " " + self.logos_driver + " " + self.specs["parameters"] + " " + self.specs["input"]
    return cmd
