import random
import numpy as np

def run(self,Input):

  self.capitals = np.atleast_1d(['unit_1', 'unit_2'])
  self.available_capitals = np.atleast_1d([103 + Input['x'][0], 156 + Input['y'][0]])
