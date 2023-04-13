from LOGOS.src.CPM.PertMain2 import Pert
from LOGOS.src.CPM.PertMain2 import Activity

class project():
  start = Activity("start", 10)
  b     = Activity("b",     20)
  c     = Activity("c",      5)
  d     = Activity("d",     10)
  f     = Activity("f",     15)
  g     = Activity("g",      5)
  h     = Activity("h",     15)
  end   = Activity("end",   20)

  graph = {start: [f,b,h], 
           b    : [c], 
           c    : [g,d], 
           d    : [end], 
           f    : [g], 
           g    : [end],
           h    : [end],
           end  : []}