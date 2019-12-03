import numpy as np

def run(self,Input):

  t_shutdown = 25 # days
  repl_cost = 15.72 # M$
  risk_free_rate = 0.03
  hard_savings = 0.
  self.cr_npv_a = Input['cr_p_failure'] * t_shutdown + repl_cost + hard_savings
  self.cr_npv_b = self.cr_npv_a / (1.+risk_free_rate)**2
