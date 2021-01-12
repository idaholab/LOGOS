# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from scipy import stats
import plotly.figure_factory as ff
import copy
from collections import Counter


df = pd.read_csv('test.csv')

## plot with seaborn
# sns.distplot(df['MaxNPV'])
# sns.distplot(df['MaxNPV'], kde=False, rug=True)
# plt.show()

## pandas plot
# df['MaxNPV'].plot.hist()
# plt.show()

## plot with plotly
fig = ff.create_distplot([df['MaxNPV']], ['MaxNPV'])
fig.write_image("MaxNPV_dist.svg")
# fig.show()

## compute priority list

decisionVars = """HPFeedwaterHeaterUpgrade__PlanA, HPFeedwaterHeaterUpgrade__PlanB, HPFeedwaterHeaterUpgrade__DoNothing,
        PresurizerReplacement__PlanA,PresurizerReplacement__PlanB,PresurizerReplacement__PlanC,
        ImprovementEmergencyDieselGenerators__PlanA,ImprovementEmergencyDieselGenerators__PlanB,ImprovementEmergencyDieselGenerators__DoNothing,
        SecondarySystemPHMSystem__PlanA,SecondarySystemPHMSystem__PlanB,SecondarySystemPHMSystem__DoNothing,
        ReplacementTwoReactorCoolantPumps__PlanA,ReplacementTwoReactorCoolantPumps__PlanB,
        SeismicModificationRequalificationReinforcementImprovement__PlanA,SeismicModificationRequalificationReinforcementImprovement__PlanB,SeismicModificationRequalificationReinforcementImprovement__PlanC,SeismicModificationRequalificationReinforcementImprovement__DoNothing,
        FireProtection__PlanA,FireProtection__PlanB,
        ServiceWaterSystemUpgrade__PlanA,ServiceWaterSystemUpgrade__PlanB,ServiceWaterSystemUpgrade__DoNothing,
        BatteriesReplacement__PlanA,BatteriesReplacement__DoNothing,
        ReplaceCCWPipingHeatExchangersValues__PlanA,ReplaceCCWPipingHeatExchangersValues__PlanB,ReplaceCCWPipingHeatExchangersValues__PlanC,
        ReactorVesselInternals__PlanA,ReactorVesselInternals__PlanB,ReactorVesselInternals__DoNothing,
        ReactorVesselUpgrade__PlanA,
        ReplaceLPTurbine__PlanA,ReplaceLPTurbine__PlanB,ReplaceLPTurbine__DoNothing,
        ReplaceInstrumentationAndControlCables__PlanA,
        CondenserRetubing__PlanA,CondenserRetubing__PlanB,CondenserRetubing__DoNothing,
        ReplaceMoistureSeparatorReheater__PlanA,ReplaceMoistureSeparatorReheater__PlanB,ReplaceMoistureSeparatorReheater__PlanC,ReplaceMoistureSeparatorReheater__DoNothing"""

decisionVars = list(var.strip() for var in decisionVars.split(','))
outputPb = []
numSamples = 20000
for varName in decisionVars:
  pb = df[varName].sum()/float(numSamples)
  outputPb.append(pb)
outputDf = pd.DataFrame({'var':decisionVars, 'val':outputPb})

# compute probabilities and priority list: best portfolio
sortedOutput = outputDf.sort_values(by='val', ascending=False)
print(sortedOutput)
sortedOutput.to_csv('priority_output.csv', index=True)
# compute risk for best portfolio
riskDf = copy.deepcopy(sortedOutput)
riskDf['val'] = 1. - riskDf['val']
print("Risk: ", riskDf)
riskDf.to_csv('risk_output.csv', index=True)

# rank project based on frequencies
selectedVars = copy.copy(decisionVars)
selectedVars.append('MaxNPV')
decisionDf = df.loc[:, selectedVars] # used to determine the rank for each project portfolio
decisionRanks = decisionDf.groupby(decisionVars, as_index=False).size().reset_index().rename(columns={0:'records'}).sort_values(by='records', ascending=False)
# print(decisionRanks)

# compute the average NPV for each project portfolio
decisionArray = df.loc[:, decisionVars].values
u, indices = np.unique(decisionArray, axis=0, return_inverse=True)
uniqueDecisionDict = {}
uniqueDecisionNPVDict = {}
countDict = {}
for i, ind in enumerate(indices):
  if ind not in uniqueDecisionDict:
    uniqueDecisionNPVDict[ind] = [decisionDf.loc[i,'MaxNPV']]
    uniqueDecisionDict[ind] = decisionDf.loc[i,decisionVars]
    countDict[ind] = 1
  else:
    uniqueDecisionNPVDict[ind].append(decisionDf.loc[i,'MaxNPV'])
    countDict[ind] += 1

countList = list(countDict.items())
sortedList = sorted(countList, key=lambda tup: tup[1], reverse=True)
# print(sortedList)

# print decision variable and NPV
addVars = ['MinNPV', 'MaxNPV', 'AverageNPV','StandardDeviationNPV','Frequencies','Probabilities']
outputDict = dict((var,[]) for var in decisionVars + addVars)
print(decisionVars)
for val in sortedList:
  ind, freq = val
  print(uniqueDecisionDict[ind].values)
  print('average NPV:', np.mean(uniqueDecisionNPVDict[ind]))
  outputDict['Frequencies'].append(countDict[ind])
  outputDict['Probabilities'].append(countDict[ind]/float(numSamples))
  outputDict['MinNPV'].append(np.amin(uniqueDecisionNPVDict[ind]))
  outputDict['MaxNPV'].append(np.amax(uniqueDecisionNPVDict[ind]))
  outputDict['AverageNPV'].append(np.mean(uniqueDecisionNPVDict[ind]))
  outputDict['StandardDeviationNPV'].append(np.std(uniqueDecisionNPVDict[ind]))
  for var in decisionVars:
    outputDict[var].append(uniqueDecisionDict[ind][var])

rankedOutputDf = pd.DataFrame(outputDict)
print(rankedOutputDf)
rankedOutputDf.T.to_csv('ranked_output.csv', index=True)
