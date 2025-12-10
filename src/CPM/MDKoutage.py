import pyomo.environ as pyo
import numpy as np
import random


class mdkChoiceModel:
    """
        This is the base class for the multi-dimensional knapsack problem adapted to the outage
        scheduling problem
    """
    def __init__(self, candidates, resources, valueType):
        """
        Constructor
        @ In, candidates, list, list of candidate activities
        @ In, resources, pd.dataframe, present resources availability
        @ Out, None
        """
        resourcesList = list(resources.keys())

        self.jobs_ID = []
        self.res_ID  = list(resources.to_dict().keys())

        self.knapsacks = resources.to_dict()

        self.jobs_ID = []
        for job in candidates:
            self.jobs_ID.append(job.returnName())

        self.res_dict = {}
        for candidate in candidates.keys():
            req_res = candidate.returnResources()
            for res in resourcesList:
                if res in list(req_res):
                    self.res_dict[(candidate.returnName(),res)] = req_res[res]
                else:
                    self.res_dict[(candidate.returnName(),res)] = 0.

        if valueType == 'uniform':
            self.values = {candidate.returnName(): 1 for candidate in candidates}
        elif valueType == 'value_based':
            self.values = {candidate.returnName(): candidate['value']  for candidate in candidates}
        else:
            print('Error on mdkChoiceModel valueType')
        
        self.candidate_mapping = {candidate.returnName(): candidate for candidate in candidates}
        #for candidate in candidates:
        #    self.values[candidate.returnName()] = 1.
        #    self.candidate_mapping[candidate.returnName()] = candidate

    def run(self):
        """
        Multi-dimensional knapsack solver
        @ In, None
        @ Out, selected, list, lis of selected activities
        """
        model = pyo.ConcreteModel()

        model.I = pyo.Set(initialize=self.jobs_ID)
        model.K = pyo.Set(initialize=self.res_ID)

        model.value    = pyo.Param(model.I, initialize=self.values)
        model.weight   = pyo.Param(model.I, model.K, initialize=self.res_dict)
        model.capacity = pyo.Param(model.K, initialize=self.knapsacks)

        model.x = pyo.Var(model.I, domain=pyo.Binary)

        model.objective = pyo.Objective(expr=sum(model.value[i] * model.x[i] for i in model.I), sense=pyo.maximize)

        def capacity_rule(model, k):
            return sum(model.weight[i, k] * model.x[i] for i in model.I) <= model.capacity[k]
        model.capacity_constraint = pyo.Constraint(model.K, rule=capacity_rule)

        solver = pyo.SolverFactory('glpk') 
        results = solver.solve(model)

        selected = []

        print("Optimal objective value:", pyo.value(model.objective))
        for i in model.I:
            if pyo.value(model.x[i]) > 0.5:
                print(f"Knapsack model: job {i} selected")
                selected.append(self.candidate_mapping[i])

        return selected
