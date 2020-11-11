# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
import math
from scipy.integrate import quad
import numpy as np

def run(self,Input):
  T      = Input['T']      # in years
  T_repl = Input['T_repl'] # in years

  cost_TM_new  = Input['cost_TM_new']
  cost_TM_old  = Input['cost_TM_old']
  cost_repl    = Input['cost_repl']
  cost_penalty = Input['cost_penalty']

  r = Input['r']

  if   Input['failure_time_old_act'] >= T and Input['failure_time_new_act'] >= T:
    self.NPV = NPV_repl_no_fail(r,T,T_repl,cost_TM_new,cost_TM_old,cost_repl,cost_penalty)

  elif Input['failure_time_old_act'] >= T and Input['failure_time_new_act'] < T:
    self.NPV = NPV_repl_fail_new(Input['failure_time_new_act'],r,T,T_repl,cost_TM_new,cost_TM_old,cost_repl,cost_penalty)

  elif Input['failure_time_old_act'] < T  and Input['failure_time_new_act'] >= T:
    self.NPV = NPV_repl_fail_old(Input['failure_time_old_act'],r,T,T_repl,cost_TM_new,cost_TM_old,cost_repl,cost_penalty)

  elif Input['failure_time_old_act'] < T  and Input['failure_time_new_act'] < T:
    self.NPV = NPV_repl_fail_twice(Input['failure_time_old_act'],Input['failure_time_new_act'],r,T,T_repl,cost_TM_new,cost_TM_old,cost_repl,cost_penalty)
  else:
    print('Error in NPV_repl.py')




def costIntegral(t,cost,r):
  value = cost * math.exp(-r*t)
  return value

def costRepl(t,cost_repl,r):
  value = cost_repl * math.exp(-r*t)
  return value

def NPV_repl_no_fail(r,T,T_repl,cost_TM_new,cost_TM_old,cost_repl,cost_penalty):
  firstTerm  = quad(costIntegral,0,T_repl,args=(cost_TM_old,r))[0]
  #if T_repl < T:
  #  secondTerm = costRepl(T_repl,cost_repl,r)
  #else:
  #  secondTerm = 0.
  secondTerm = costRepl(T_repl,cost_repl,r)
  thirdTerm  = quad(costIntegral,T_repl,T,args=(cost_TM_new,r))[0]

  total = firstTerm + secondTerm + thirdTerm

  return total

def NPV_repl_fail_new(failure_time_new_act,r,T,T_repl,cost_TM_new,cost_TM_old,cost_repl,cost_penalty):
  firstTerm  = quad(costIntegral,0,T_repl,args=(cost_TM_old,r))[0]
  #if T_repl < T:
  #  secondTerm = costRepl(T_repl,cost_repl,r)
  #else:
  #  secondTerm = 0.
  secondTerm = costRepl(T_repl,cost_repl,r)
  thirdTerm  = quad(costIntegral,T_repl,failure_time_new_act,args=(cost_TM_new,r))[0]
  #if failure_time_new_act < T:
  #  fourthTerm = costRepl(failure_time_new_act,cost_repl+cost_penalty,r)
  #else:
  #  fourthTerm = 0.
  fourthTerm = costRepl(failure_time_new_act,cost_repl+cost_penalty,r)
  fithTerm   = quad(costIntegral,failure_time_new_act,T,args=(cost_TM_new,r))[0]

  total = firstTerm + secondTerm + thirdTerm + fourthTerm + fithTerm

  return total

def NPV_repl_fail_old(failure_time_old_act,r,T,T_repl,cost_TM_new,cost_TM_old,cost_repl,cost_penalty):
  firstTerm  = quad(costIntegral,0,failure_time_old_act,args=(cost_TM_old,r))[0]
  secondTerm = costRepl(failure_time_old_act,cost_repl+cost_penalty,r)
  thirdTerm  = quad(costIntegral,failure_time_old_act,T,args=(cost_TM_new,r))[0]

  total = firstTerm + secondTerm + thirdTerm

  return total

def NPV_repl_fail_twice(failure_time_old_act,failure_time_new_act,r,T,T_repl,cost_TM_new,cost_TM_old,cost_repl,cost_penalty):
  firstTerm  = quad(costIntegral,0,failure_time_old_act,args=(cost_TM_old,r))[0]

  secondTerm = costRepl(failure_time_old_act,cost_repl+cost_penalty,r)
  thirdTerm  = quad(costIntegral,failure_time_old_act,failure_time_new_act,args=(cost_TM_new,r))[0]
  fourthTerm = costRepl(failure_time_new_act,cost_repl+cost_penalty,r)
  fithTerm   = quad(costIntegral,failure_time_new_act,T,args=(cost_TM_new,r))[0]

  total = firstTerm + secondTerm + thirdTerm + fourthTerm + fithTerm

  return total
