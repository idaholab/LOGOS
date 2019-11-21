#
#
#
"""
  Created on March. 15, 2019
  @author: wangc, mandd
"""
#for future compatibility with Python 3--------------------------------------------------------------
from __future__ import division, print_function, unicode_literals, absolute_import
import warnings
warnings.simplefilter('default',DeprecationWarning)
#End compatibility block for Python 3----------------------------------------------------------------

#External Modules------------------------------------------------------------------------------------
import sys
import logging
import argparse
#External Modules End--------------------------------------------------------------------------------

#Internal Modules------------------------------------------------------------------------------------
#import PyomoModels
from CapitalInvestments import PyomoModels
from CapitalInvestments.investment_utils import inputReader
#Internal Modules End--------------------------------------------------------------------------------

logging.basicConfig(format='%(asctime)s %(name)-20s %(levelname)-8s %(message)s', datefmt='%d-%b-%y %H:%M:%S', level=logging.DEBUG)
# To enable the logging to both file and console, the logger for the main should be the root,
# otherwise, a function to add the file handler and stream handler need to be created and called by each module.
# logger = logging.getLogger(__name__)
logger = logging.getLogger()
# # create file handler which logs debug messages
fh = logging.FileHandler(filename='logos.log', mode='w')
fh.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s %(name)-20s %(levelname)-8s %(message)s')
fh.setFormatter(formatter)
# add the handlers to the logger
logger.addHandler(fh)


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description='Run Logos as a stand-alone code')
  parser.add_argument('-i', '--input', nargs=1, required=True, help='Logos input filename')
  parser.add_argument('-o', '--output', nargs=1, help='Logos output filename')
  args = parser.parse_args()
  args = vars(args)
  inFile = args['input'][0]
  logger.info('Logos input file: %s', inFile)
  if args['output'] is not None:
    outFile = args['output'][0]
    logger.info('Logos output file: %s', outFile)
  else:
    outFile = '.'.join(inFile.split('.')[:-1]) + '.csv'
    logger.warning('Output file is not specifies, default output file with name ' + outFile + ' will be used')

  # process input file
  logger.info('Starting to process input file: %s', inFile)
  initDict = inputReader.readInput(inFile)
  logger.info('Input file is successfully processed')
  problemType = initDict['Settings'].pop('problem_type', 'SingleKnapsack')
  logger.info('Set problem type to default: %s', 'Knapsacks Problem')
  logger.info('Starting to create Optimization Instance')
  modelInstance = PyomoModels.returnInstance(problemType)
  logger.info('Optimization Instance: %s is successfully created', modelInstance.name)
  logger.info('Starting to initialize Optimizer Instance: %s', modelInstance.name)
  modelInstance.initialize(initDict)
  logger.info('Optimization Instance: %s is successfully intialized', modelInstance.name)
  logger.info('Starting to run Optimization Instance: %s', modelInstance.name)
  modelInstance.run()
  logger.info('Optimization Instance: %s is successfully optimized', modelInstance.name)
  logger.info('Starting to dump output to: %s', outFile)
  modelInstance.writeOutput(outFile)
  logger.info('Output is successfully written into: %s', outFile)
  logger.info("Run complete!")
