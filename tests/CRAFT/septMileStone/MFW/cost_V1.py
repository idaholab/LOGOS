import random
import numpy as np

def run(self,Input):
  # intput:
  # output:

  cost_V1 = Input['V1_numberDaysSD'] * Input['V1_costPerDaySD']

  self.cost_V1 = cost_V1 * Input['costFactor']
