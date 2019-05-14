import random
import numpy as np

def run(self,Input):
  # intput:
  # output:

  cost_V2 = Input['V2_numberDaysSD'] * Input['V2_costPerDaySD']

  self.cost_V2 = cost_V2 * Input['costFactor']
