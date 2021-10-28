# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED

import os
import sys

logosDir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
testDir = os.path.join(logosDir, 'tests', 'CapitalInvestments')

def getRegressionTests(whichTests=1, skipExpectedFails=True):
  """
    Collects all the regression tests into a dictionary keyed by directory.
    @ In, whichTests, integer, optional, the test type:
                                       - 1 => xml test files,
                                       - 2 => python tests,
                                       default 1 => xml test files
    @ In, skipExpectedFails, optional, bool, if True skips framework/ErrorCheck directory
    @ Out, dict, dict[dir] = list(filenames)
  """
  testsFilenames = []
  #search for all the 'tests' files
  for root, _, files in os.walk(testDir):
    if skipExpectedFails and 'ErrorChecks' in root.split(os.sep):
      continue
    if 'tests' in files:
      testsFilenames.append((root, os.path.join(root, 'tests')))
  suffix = ".xml" if whichTests in [1] else ".py"
  #read all "input" node files from "tests" files
  doTests = {}
  for root, testFilename in testsFilenames:
    testsFile = open(testFilename, 'r')
    # collect the test specs in a dictionary
    testFileList = []
    testSpecs = {}
    startReading = False
    collectSpecs = False
    for line in testsFile:
      if line.strip().startswith("#"):
        continue
      if line.strip().startswith("[../]"):
        collectSpecs = True
        startReading = False
      if startReading:
        splitted = line.strip().split('=')
        if len(splitted) == 2:
          testSpecs[splitted[0].strip()] = splitted[1].replace("'", "").replace('"', '').strip()
      if line.strip().startswith("[./"):
        startReading = True
        collectSpecs = False
      if collectSpecs:
        # collect specs
        testFileList.append(testSpecs)
        collectSpecs = False
        testSpecs = {}
    if root not in doTests.keys():
      doTests[root] = []
    # now we have all the specs collected
    for spec in testFileList:
      # check if test is skipped or an executable is required
      if "required_executable" in spec or "skip" in spec:
        continue
      if "input" not in spec:
        continue
      testType = spec.get('type', "notfound").strip()
      newTest = spec['input'].split()[0]

      if whichTests in [1]:
        if newTest.endswith(suffix) and testType.lower() == 'logosrun':
          doTests[root].append(newTest)
        else:
          print('Skipping test:', newTest)
      else:
        if newTest.endswith(suffix) and testType.lower() in 'ravenpython':
          doTests[root].append(newTest)
  return doTests

if __name__ == '__main__':
  which = 1
  # skip the expected failed tests
  skipFails = True if '--skip-fails' in sys.argv else  False
  if '--get-python-tests' in sys.argv:
    # unit tests flag has priority over interface check
    which = 2

  tests = getRegressionTests(which, skipExpectedFails=skipFails)
  #print doTests
  testFiles = []
  for key in tests:
    testFiles.extend([os.path.join(key, l) for l in tests[key]])
  print(' \n'.join(testFiles))
