import numpy as np

def run(self,Input):

  t_shutdown = 10 # days
  repl_cost = 4.48 # M$
  risk_free_rate = 0.03
  hard_savings = 0.
  self.sm_npv_a = Input['sm_p_failure'] * t_shutdown + repl_cost + hard_savings
  self.sm_npv_b = self.sm_npv_a / (1.+risk_free_rate)**2
  self.sm_npv_c = self.sm_npv_a / (1.+risk_free_rate)**3
