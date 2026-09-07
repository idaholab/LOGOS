import pyomo.environ as pyo
import numpy as np
import random


class mdkChoiceModel:
    """
    Base class for the multi-dimensional knapsack problem adapted to the outage
    scheduling problem.

    Selects a subset of candidate activities that maximises total value subject
    to the availability of every shared resource, formulated as a 0/1
    multi-dimensional knapsack and solved with Pyomo/GLPK.

    Parameters
    ----------
    candidates : dict
        Dictionary of candidate activities in the form
        ``{activity_instance: {'duration': , 'es': , 'ef': , 'ls': , 'lf': ,
        'slack': , 'value': }}``.
    resources : pandas.DataFrame
        Present resource availability.
    valueType : str
        Approach employed to assign values to activities:

        * ``'uniform'`` — assign equal value (1.0) to every activity.
        * ``'value_based'`` — use the value specified in
          ``candidates[activity]['value']``.

    Raises
    ------
    ValueError
        If ``valueType`` is neither ``'uniform'`` nor ``'value_based'``.
    """
    def __init__(self, candidates, resources, valueType):
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
        Solve the multi-dimensional knapsack and return the chosen activities.

        Builds a Pyomo ``ConcreteModel`` with one binary variable per candidate
        activity, maximises the total activity value subject to one capacity
        constraint per resource, and solves it with the GLPK solver.

        Returns
        -------
        list
            The activity instances selected by the solver.
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
