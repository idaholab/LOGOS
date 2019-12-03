import numpy as np

def run(self,Input):

  t_shutdown = 20 # days
  repl_cost = 25.98 # M$
  risk_free_rate = 0.03
  hard_savings = 0.

  self.hp_fwh_npv_a = Input['hp_fwh_p_failure'] * t_shutdown + repl_cost + hard_savings
  self.hp_fwh_npv_b = self.hp_fwh_npv_a / (1.+risk_free_rate)
