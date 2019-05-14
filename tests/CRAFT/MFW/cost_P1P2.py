import random
import numpy as np

def run(self,Input):
  # intput:
  # output:

  self.cost_P1P2_0 = np.zeros(Input['time'].size)
  self.cost_P1P2_1 = Input['P1P2_numberDaysPred'] * Input['P1P2_costPerDayPred'] * Input['costFactor']

  costSD       = Input['P1P2_numberDaysSD'] * Input['P1P2_costPerDaySD'] * Input['costFactor']
  costReg       = Input['P1P2_numberDaysSD'] * Input['P1P2_costPerDayReg']

  self.cost_P1P2_2 = costSD + costReg
