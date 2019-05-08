import random
import numpy as np

def run(self,Input):
  # intput: 
  # output: 

  self.CDF_mean = np.zeros(len(self.time))
  self.CDF_5p   = np.zeros(len(self.time))
  self.CDF_95p  = np.zeros(len(self.time))

  for ts in range(len(self.time)):
    if ts == 0:
      dt = self.time[ts]
    else:
      dt = self.time[ts] - self.time[ts-1]
    
    pV1_1y = self.p_V1[ts] * 1./dt
    pV2_1y = self.p_V2[ts] * 1./dt
    pP1_1y = self.p_P1[ts] * 1./dt
    pP2_1y = self.p_P2[ts] * 1./dt
    pSG_1y = self.p_SG[ts] * 1./dt

    self.CDF_mean[ts] = pV1_1y + pV2_1y + (pP1_1y*pP2_1y) - pV1_1y*pV2_1y - pV1_1y*(pP1_1y*pP2_1y) - pV2_1y*(pP1_1y*pP2_1y) + pV1_1y*pV2_1y*(pP1_1y*pP2_1y)
    self.CDF_5p[ts]   = self.CDF_mean[ts] * (1.-0.1)
    self.CDF_95p[ts]  = self.CDF_mean[ts] * (1.+0.22)
