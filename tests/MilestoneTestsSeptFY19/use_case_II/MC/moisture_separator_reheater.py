import numpy as np

def run(self,Input):

  t_shutdown = 12 # days
  repl_cost = 7.9 # M$
  risk_free_rate = 0.03
  hard_savings = 0.
  self.msr_npv_a = Input['msr_p_failure'] * t_shutdown + repl_cost + hard_savings
  self.msr_npv_b = self.msr_npv_a / (1.+risk_free_rate)**3
  self.msr_npv_c = self.msr_npv_a / (1.+risk_free_rate)**4
