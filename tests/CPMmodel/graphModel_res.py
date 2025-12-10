from LOGOS.src.CPM.PertMain2 import Pert
from LOGOS.src.CPM.PertMain2 import Activity
import datetime
import pandas as pd
import numpy as np

class project():
  start = Activity("start", 10, res={"res1":1})
  b     = Activity("b",     20, res={"res1":1})
  c     = Activity("c",      5, res={"res1":1})
  d     = Activity("d",     10, res={"res1":1})
  f     = Activity("f",     15, res={"res1":1})
  g     = Activity("g",      5, res={"res1":1})
  h     = Activity("h",     15, res={"res1":1})
  end   = Activity("end",   20, res={"res1":1})

  graph = {start: [f,b,h],
           b    : [c],
           c    : [g,d],
           d    : [end],
           f    : [g],
           g    : [end],
           h    : [end],
           end  : []}
  
class resource_schedule():
  outageStartTime =  datetime(2025, 4, 25, 8)

  N = 30
  hourly_index = pd.date_range(start=outageStartTime, periods=N, freq='h')
  resources = pd.DataFrame({'res1': 2*np.ones(N)}, index=hourly_index)

