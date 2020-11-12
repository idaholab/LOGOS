# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
import random
import numpy as np

def run(self,Input):
  # intput: failure_time_new, failure_time_old , T, T_repl
  # output: failure_time_act

  T      = Input['T']      # in years
  T_repl = Input['T_repl'] # in years

  if Input['failure_time_old'] < T_repl:
    self.failure_time_old_act = Input['failure_time_old']
  else:
    self.failure_time_old_act = T

  T_repl = self.failure_time_old_act

  if (Input['failure_time_new'] + T_repl) < T:
    self.failure_time_new_act = T_repl + Input['failure_time_new']
  else:
    self.failure_time_new_act = T
