import subprocess
import os
import sys
import time


def printFail(message, end = '\n'):
  sys.stderr.write('\x1b[1;31m' + message.strip() + '\x1b[0m' + end)

def printPass(message, end = '\n'):
  sys.stdout.write('\x1b[1;32m' + message.strip() + '\x1b[0m' + end)

def printWarning(message, end = '\n'):
  sys.stderr.write('\x1b[1;33m' + message.strip() + '\x1b[0m' + end)

def printInfo(message, end = '\n'):
  sys.stdout.write('\x1b[1;34m' + message.strip() + '\x1b[0m' + end)

def printBold(message, end = '\n'):
  sys.stdout.write('\x1b[1;37m' + message.strip() + '\x1b[0m' + end)

###################################################################################
#                 Test Inputs
####################################################################################
heavy = False
heavyInputs="""
./skp/test_EE_41_projects.xml -- heavy
"""

TESTINPUTS="""
./skp/test_bkp.xml
./skp/test_dkp.xml
./skp/test_mandated.xml
./skp/test_multi_variation.xml
./skp/test_noIndex.xml
./skp/test_npv.xml
./skp/test_skp.xml
./skp/test_skp_scenarios.xml
./skp/test_variation.xml

./mkp/test_mkp.xml
./mkp/test_mkp_1.xml
./mkp/test_mkp_noindex.xml
./mkp/test_mkp_scenarios.xml
./mkp/test_mkp_t.xml
./mkp/test_mkp_weing.xml

./mckp/test_mckp.xml
./mckp/test_mckp_scenario.xml

./examples/test_p16.xml
./examples/test_p16_ext.xml
"""

if __name__ == "__main__":
  testInputs = TESTINPUTS.split()
  heavyInputs = heavyInputs.split()
  if heavy:
    testInput = testInput + heavyInputs
  directory = os.getcwd()
  start = time.time()
  timeout = 300
  end = start + timeout
  fail = 0
  success = 0
  pythonPath = '/Users/wangc/miniconda3/envs/raven_libraries/bin/python'
  failList = []
  for test in testInputs:
    command = directory + '/logos -i ' + directory + '/tests/CapitalInvestments/' + test
    print(command)
    try:
      print(directory)
      process = subprocess.Popen(command, shell=True,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,
                                 cwd=directory,
                                 universal_newlines=True)
    except IOError as ioe:
      print("Failed: " + str(ioe))
    stdout, stderr = process.communicate()
    print(stdout)
    exit_code = process.returncode
    if exit_code != 0:
      fail += 1
      failList.append(test)
    else:
      success += 1
  printPass("PASSED: " + str(success))
  printFail('FAILED: ' + str(fail))
  for testInput in failList:
    printFail("Failed " + testInput)
