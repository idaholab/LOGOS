#
#
#
"""
  Created on April 28, 2020
  @author: wangc, mandd
"""
#External Modules------------------------------------------------------------------------------------
import numpy as np
from sklearn.neighbors import DistanceMetric
import logging
import os
#External Modules End--------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def preprocessData(scenarioDict):
  """
    Pre-process scenario dictionary
    @ In, scenarioDict, dict, contains scenario data.
      i.e. {'scenario_i':{'paramName':{index/indexTuple:value}}}
    @ Out, data, list, 2-D list with shape (numberScenarios, paramsVariationSize)
  """
  data = []
  for sm, paramDict in scenarioDict.items():
    paramData = []
    for param, valueDict in paramDict.items():
      paramData.extend(valueDict.values())
    data.append(paramData)
  return data

def computeDist(distName, scenarioDict, kwargs={}):
  """
    Compute distance based on given distance metric name
    @ In, distName, str, the name of distance metric.
      i.e. euclidean, manhattan, chebyshev, minkowski, wminkowski, seuclidean and mahalanobis.
    @ In, scenarioDict, dict, contains scenario data.
      i.e. {'scenario_i':{'paramName':{index/indexTuple:value}}}
    @ In, kwargs, dict, options for the distance metric
    @ Out, distData, numpy.array, 2-D numpy array contains the pairwise distance between scenarios
  """
  dist = DistanceMetric.get_metric(distName, **kwargs)
  data = preprocessData(scenarioDict)
  distData = dist.pairwise(data)
  return distData
