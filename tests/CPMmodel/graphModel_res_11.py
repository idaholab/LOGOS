from LOGOS.src.CPM.PertMain2 import Pert
from LOGOS.src.CPM.PertMain2 import Activity
import datetime as dt
import pandas as pd
import numpy as np

# From https://www.iste.co.uk/data/doc_dtalmanhopmh.pdf
class project():
  start = Activity("start", duration=1, res={'res1':1, 'res2':0})
  A_1   = Activity("A_1",   duration=6, res={'res1':2, 'res2':1})
  A_2   = Activity("A_2",   duration=1, res={'res1':1, 'res2':0})
  A_3   = Activity("A_3",   duration=1, res={'res1':3, 'res2':1})
  A_4   = Activity("A_4",   duration=2, res={'res1':2, 'res2':0})
  A_5   = Activity("A_5",   duration=3, res={'res1':1, 'res2':1})
  A_6   = Activity("A_6",   duration=5, res={'res1':2, 'res2':1})
  A_7   = Activity("A_7",   duration=6, res={'res1':3, 'res2':0})
  A_8   = Activity("A_8",   duration=3, res={'res1':1, 'res2':2})
  A_9   = Activity("A_9",   duration=2, res={'res1':1, 'res2':2})
  A_10  = Activity("A_10",  duration=4, res={'res1':1, 'res2':1})
  end   = Activity("end",   duration=1, res={'res1':1, 'res2':0})

  graph = {start:  [A_1, A_2, A_3, A_4],
             A_1:  [A_10],
             A_2:  [A_5, A_6],
             A_3:  [A_7],
             A_4:  [A_8],
             A_5:  [A_9],
             A_6:  [A_10],
             A_7:  [end],
             A_8:  [end],
             A_9:  [end],
             A_10: [end],
             end:  []}

class resource_schedule():
  outageStartTime = dt.datetime(2025, 10, 20, 8)

  N = 20
  hourly_index = pd.date_range(start=outageStartTime, periods=N, freq='h')
  resources = pd.DataFrame({'res1': 7*np.ones(N), 'res2': 4*np.ones(N)}, index=hourly_index)

