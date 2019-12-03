import numpy as np

def run(self,Input):

  t_shutdown = 20 # days
  repl_cost = 37.54 # M$
  risk_free_rate = 0.03
  hard_savings = 130.

  self.rlpt_npv_a = Input['rlpt_p_failure'] * t_shutdown + repl_cost + hard_savings
  self.rlpt_npv_b = self.rlpt_npv_a / (1.+risk_free_rate)
