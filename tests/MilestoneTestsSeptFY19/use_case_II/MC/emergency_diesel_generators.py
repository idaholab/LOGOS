import numpy as np

def run(self,Input):

  t_shutdown = 7 # days
  repl_cost = 20.16 # M$
  risk_free_rate = 0.03
  hard_savings = 0.
  self.edg_npv_a = Input['edg_p_failure'] * t_shutdown + repl_cost + hard_savings
  self.edg_npv_b = self.edg_npv_a * (1.+risk_free_rate)
