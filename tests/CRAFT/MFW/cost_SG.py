import random
import numpy as np

def run(self,Input):
  # intput:
  # output:

  cost_SG = Input['SG_numberDaysSD'] * Input['SG_costPerDaySD']

  self.cost_SG = cost_SG * Input['costFactor']
