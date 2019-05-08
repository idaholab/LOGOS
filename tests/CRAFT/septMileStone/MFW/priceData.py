import numpy as np

def run(self,Input):
  # intput: None
  # output: time

  T  = Input['time'][-1] - Input['time'][0] + 1.
  # load Electricity per KWH in U.S. city average, average price from 1998 to 2017
  # Construct a cost factor for all cost models
  fname = "cpiAveragePriceData.csv"
  cpiData = np.loadtxt(fname, delimiter=',', skiprows=1)
  cpiData = cpiData[:,1:13].reshape(-1)
  cpiData = cpiData / np.mean(cpiData)
  xp = np.linspace(Input['time'][0],Input['time'][-1],cpiData.size)
  self.costFactor = np.interp(Input['time'], xp, cpiData)
