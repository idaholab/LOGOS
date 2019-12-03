import numpy as np

def run(self,Input):

  t_shutdown = 30 # days
  repl_cost = 39.64 # M$
  risk_free_rate = 0.03
  hard_savings = 0.
  self.rvi_npv_a = Input['rvi_p_failure'] * t_shutdown + repl_cost + hard_savings
  self.rvi_npv_b = self.rvi_npv_a / (1.+risk_free_rate)**3
