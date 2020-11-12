# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
import math
from scipy.integrate import quad
import numpy as np

def run(self,Input):
  T      = Input['T']      # in years

  cost_TM_new  = Input['cost_TM_new']
  cost_TM_old  = Input['cost_TM_old']
  cost_repl    = Input['cost_repl']
  cost_penalty = Input['cost_penalty']

  r = Input['r']

  if   Input['failure_time_old_act'] >= T:
    self.NPV = NPV_repl_no_fail(r,T,cost_TM_old)

  elif Input['failure_time_old_act'] < T and Input['failure_time_new_act'] >= T:
    self.NPV = NPV_repl_fail_old(Input['failure_time_old_act'],r,T,cost_TM_new,cost_TM_old,cost_repl,cost_penalty)

  elif Input['failure_time_old_act'] < T and Input['failure_time_new_act'] < T:
    self.NPV = NPV_repl_fail_twice(Input['failure_time_old_act'],Input['failure_time_new_act'],r,T,cost_TM_new,cost_TM_old,cost_repl,cost_penalty)

  else:
    print('Error in NPV_no_repl.py')




def costIntegral(t,cost,r):
  value = cost * math.exp(-r*t)
  return value

def costRepl(t,cost_repl,r):
  value = cost_repl * math.exp(-r*t)
  return value

def NPV_repl_no_fail(r,T,cost_TM_old):
  firstTerm  = quad(costIntegral,0,T,args=(cost_TM_old,r))[0]

  total = np.asarray(firstTerm)

  return total

def NPV_repl_fail_old(failure_time_old_act,r,T,cost_TM_new,cost_TM_old,cost_repl,cost_penalty):
  firstTerm  = quad(costIntegral,0,failure_time_old_act,args=(cost_TM_old,r))[0]
  secondTerm = costRepl(failure_time_old_act,cost_repl+cost_penalty,r)
  thirdTerm  = quad(costIntegral,failure_time_old_act,T,args=(cost_TM_new,r))[0]

  total = firstTerm + secondTerm + thirdTerm

  return total

def NPV_repl_fail_twice(failure_time_old_act,failure_time_new_act,r,T,cost_TM_new,cost_TM_old,cost_repl,cost_penalty):
  firstTerm  = quad(costIntegral,0,failure_time_old_act,args=(cost_TM_old,r))[0]
  secondTerm = costRepl(failure_time_old_act,cost_repl+cost_penalty,r)
  thirdTerm  = quad(costIntegral,failure_time_old_act,failure_time_new_act,args=(cost_TM_new,r))[0]
  fourthTerm = costRepl(failure_time_new_act,cost_repl+cost_penalty,r)
  fithTerm   = quad(costIntegral,failure_time_new_act,T,args=(cost_TM_new,r))[0]

  total = firstTerm + secondTerm + thirdTerm + fourthTerm + fithTerm

  return total
