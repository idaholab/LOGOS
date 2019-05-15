import random
import numpy as np

def initialize(self, runInfo, inputs):
  seed = 9491
  random.seed(seed)

def run(self,Input):
  # intput:
  # output:

  numberDaysSD = float(random.randint(10,30))
  costPerDay   = 0.8 + 0.4 * random.random()
  cost_V2 = numberDaysSD * costPerDay

  self.cost_V2 = cost_V2 * np.ones(Input['time'].size)
