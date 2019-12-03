import numpy as np

def run(self,Input):

  t_shutdown = 10 # days
  repl_cost = 4.68 # M$
  risk_free_rate = 0.03
  hard_savings = 0.
  self.sws_npv_a = Input['sws_p_failure'] * t_shutdown + repl_cost + hard_savings
  self.sws_npv_b = self.sws_npv_a / (1.+risk_free_rate)**2
