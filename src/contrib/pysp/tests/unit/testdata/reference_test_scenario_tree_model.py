#  ___________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright 2017 National Technology and Engineering Solutions of Sandia, LLC
#  Under the terms of Contract DE-NA0003525 with National Technology and 
#  Engineering Solutions of Sandia, LLC, the U.S. Government retains certain 
#  rights in this software.
#  This software is distributed under the 3-clause BSD License.
#  ___________________________________________________________________________

import os
import tempfile

from pysp.scenariotree.tree_structure_model import \
    CreateAbstractScenarioTreeModel

with tempfile.NamedTemporaryFile(mode="w", suffix=".dat", delete=False) as f:
    f.write("""
set Stages :=
t1
t2
;

set Nodes :=
root 
n1
n2 
n3
;

param NodeStage :=
root t1 
n1 t2
n2 t2 
n3 t2
;

set Children[root] :=
n1 
n2 
n3
;

param ConditionalProbability :=
root 1.0
n1 0.33333333
n2 0.33333334
n3 0.33333333
;

set Scenarios :=
s1
s2
s3
;

param ScenarioLeafNode :=
s1 n1
s2 n2
s3 n3
;

set StageVariables[t1] :=
x
;

param StageCost :=
t1 cost[1]
t2 cost[2]
;
    """)

model = CreateAbstractScenarioTreeModel().create_instance(f.name)
os.remove(f.name)
