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
        @ In, candidates, dict, dictionary of candidate activities in the form:
                                {activity_instance: {'duration': , 'es': , 'ef': , 'ls': , 'lf': , 'slack': , 'value': }}
        @ In, resources, pd.dataframe, present resources availability
        @ In, valueType, string, approach employed to assign values to activities
                                 * uniform: assign equal velua (1.) to every activity
                                 * value_based: employ the value specified in candidates[activity]['value']
        @ Out, None
        """
        resourcesList = list(resources.keys())

        self.jobsID = [] # ID (string) of the candidate activities
        self.resID  = list(resources.to_dict().keys()) # ID (string) of the available resources

        self.knapsacks = resources.to_dict()

        for job in candidates:
            self.jobsID.append(job.returnName())

        self.resDict = {}
        for candidate in candidates.keys():
            reqRes = candidate.returnResources()
            for res in resourcesList:
                if res in list(reqRes):
                    self.resDict[(candidate.returnName(),res)] = reqRes[res]
                else:
                    self.resDict[(candidate.returnName(),res)] = 0.

        if valueType == 'uniform':
            self.values = {candidate.returnName(): 1 for candidate in candidates}
        elif valueType == 'value_based':
            self.values = {candidate.returnName(): candidates[candidate]['value'] for candidate in candidates}
        else:
            raise ValueError('Error on mdkChoiceModel valueType')

        self.candidateMapping = {candidate.returnName(): candidate for candidate in candidates}

    def run(self):
        """
        Multi-dimensional knapsack solver
        @ In, None
        @ Out, selected, list, lis of selected activities
        """
        model = pyo.ConcreteModel()

        model.I = pyo.Set(initialize=self.jobsID)
        model.K = pyo.Set(initialize=self.resID)

        model.value    = pyo.Param(model.I, initialize=self.values)
        model.weight   = pyo.Param(model.I, model.K, initialize=self.resDict)
        model.capacity = pyo.Param(model.K, initialize=self.knapsacks)

        model.x = pyo.Var(model.I, domain=pyo.Binary)

        model.objective = pyo.Objective(expr=sum(model.value[i] * model.x[i] for i in model.I), sense=pyo.maximize)

        def capacity_rule(model, k):
            return sum(model.weight[i, k] * model.x[i] for i in model.I) <= model.capacity[k]
        model.capacity_constraint = pyo.Constraint(model.K, rule=capacity_rule)

        solver = pyo.SolverFactory('glpk')
        results = solver.solve(model)

        selected = []

        for i in model.I:
            if pyo.value(model.x[i]) > 0.5:
                selected.append(self.candidateMapping[i])

        return selected
