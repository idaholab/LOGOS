from LOGOS.src.CPM.PertMain2 import Pert
from LOGOS.src.CPM.PertMain2 import Activity
import datetime as dt
import pandas as pd
import numpy as np

class project():
  start = Activity("start", duration=2, res={'res1':1})
  a     = Activity("a",     duration=2, res={'res1':1})
  b     = Activity("b",     duration=3, res={'res1':1})
  c     = Activity("c",     duration=3, res={'res1':1})
  d     = Activity("d",     duration=4, res={'res1':1})
  e     = Activity("e",     duration=3, res={'res1':1})
  f     = Activity("f",     duration=2, res={'res1':1})
  g     = Activity("g",     duration=3, res={'res1':1})
  h     = Activity("h",     duration=4, res={'res1':1})
  end   = Activity("end",   duration=2, res={'res1':1})

  graph = {start: [a, d, f],
          a: [b],
          b: [c],
          c: [g, h],
          d: [e],
          e: [c],
          f: [c],
          g: [end],
          h: [end],
          end:[]}
  
class resource_schedule():
  outageStartTime = dt.datetime(2025, 10, 20, 8)

  N = 30
  hourly_index = pd.date_range(start=outageStartTime, periods=N, freq='h')
  resources = pd.DataFrame({'res1': 2*np.ones(N)}, index=hourly_index)

