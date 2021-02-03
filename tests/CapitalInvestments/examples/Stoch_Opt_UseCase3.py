#import pandas
import sys
from pyomo.core import *
from pyomo.environ import *
from pyomo.opt import SolverFactory
opt = SolverFactory("glpk")
model = ConcreteModel()


# Input data
model.T =['year1','year2','year3','year4','year5']
model.I = ['HP_feedwater_heater_upgrade',
'Presurizer_replacement',
'Improvement_to_emergency_diesel_generators',
'Secondary_system_PHM_system',
'Replacement_of_two_reactor_coolant_pumps',
'Seismic_modification_requalification_reinforcement_improvement',
'Fire_protection',
'Service_water_system_upgrade',
'Batteries_replacement',
'Replace_CCW_piping_heat_exchangers_valves',
'Reactor_vessel_internals',
'Reactor_vessel_upgrade',
'Replace_LP_turbine',
'Replace_instrumentation_and_control_cables',
'Condenser_retubing',
'Replace_moisture_separator_reheater']

model.K =['cap','om']
model.J = ['PlanA','PlanB','PlanC']
model.wb = ['S1','S2','S3','S4','S5','S6','S7','S8','S9','S10']
model.wl = ['Low','Medium','High']
model.wm = ['Low','Medium','High']

model.risklow=['Improvement_to_emergency_diesel_generators',
'Seismic_modification_requalification_reinforcement_improvement',
'Condenser_retubing']

model.riskmed = ['HP_feedwater_heater_upgrade',
'Service_water_system_upgrade',
'Reactor_vessel_internals',
'Replace_LP_turbine',
'Replace_moisture_separator_reheater']

model.norisk = ['Presurizer_replacement',
'Secondary_system_PHM_system',
'Replacement_of_two_reactor_coolant_pumps',
'Fire_protection',
'Batteries_replacement',
'Replace_CCW_piping_heat_exchangers_valves',
'Reactor_vessel_upgrade',
'Replace_instrumentation_and_control_cables']

model.IM = ['Presurizer_replacement',
'Replacement_of_two_reactor_coolant_pumps',
'Fire_protection',
'Replace_CCW_piping_heat_exchangers_valves',
'Reactor_vessel_upgrade',
'Replace_instrumentation_and_control_cables']

model.IJ = {('HP_feedwater_heater_upgrade','PlanA'):1,
('Presurizer_replacement','PlanA'):1,
('Improvement_to_emergency_diesel_generators','PlanA'):1,
('Secondary_system_PHM_system','PlanA'):1,
('Replacement_of_two_reactor_coolant_pumps','PlanA'):1,
('Seismic_modification_requalification_reinforcement_improvement','PlanA'):1,
('Fire_protection','PlanA'):1,
('Service_water_system_upgrade','PlanA'):1,
('Batteries_replacement','PlanA'):1,
('Replace_CCW_piping_heat_exchangers_valves','PlanA'):1,
('Reactor_vessel_internals','PlanA'):1,
('Reactor_vessel_upgrade','PlanA'):1,
('Replace_LP_turbine','PlanA'):1,
('Replace_instrumentation_and_control_cables','PlanA'):1,
('Condenser_retubing','PlanA'):1,
('Replace_moisture_separator_reheater','PlanA'):1,
('HP_feedwater_heater_upgrade','PlanB'):1,
('Presurizer_replacement','PlanB'):1,
('Improvement_to_emergency_diesel_generators','PlanB'):1,
('Secondary_system_PHM_system','PlanB'):1,
('Replacement_of_two_reactor_coolant_pumps','PlanB'):1,
('Seismic_modification_requalification_reinforcement_improvement','PlanB'):1,
('Fire_protection','PlanB'):1,
('Service_water_system_upgrade','PlanB'):1,
('Replace_CCW_piping_heat_exchangers_valves','PlanB'):1,
('Reactor_vessel_internals','PlanB'):1,
('Replace_LP_turbine','PlanB'):1,
('Condenser_retubing','PlanB'):1,
('Replace_moisture_separator_reheater','PlanB'):1,
('Presurizer_replacement','PlanC'):1,
('Seismic_modification_requalification_reinforcement_improvement','PlanC'):1,
('Replace_CCW_piping_heat_exchangers_valves','PlanC'):1,
('Replace_moisture_separator_reheater','PlanC'):1}


model.qwb={'S1': 0.1,'S2':0.1,'S3': 0.1, 'S4': 0.1,'S5': 0.1,'S6': 0.1,'S7': 0.1,'S8': 0.1,'S9': 0.1,'S10': 0.1}

model.qwl = {'Low': 0.166666666666,'Medium': 0.666666666666,'High': 0.166666666666}

model.qwm = {'Low': 0.333333333333,'Medium': 0.500000000000,'High': 0.166666666666}

model.w = [model.wb,model.wl,model.wm]



model.atemp = {('HP_feedwater_heater_upgrade','PlanA','Low'):13.3129,
('HP_feedwater_heater_upgrade','PlanA','Medium'):21.8624,
('HP_feedwater_heater_upgrade','PlanA','High'):25.9080,
('HP_feedwater_heater_upgrade','PlanB','Low'):12.0228,
('HP_feedwater_heater_upgrade','PlanB','Medium'):18.9040,
('HP_feedwater_heater_upgrade','PlanB','High'):21.2811,
('Presurizer_replacement','PlanA','Low'):-10.07,
('Presurizer_replacement','PlanA','Medium'):-10.07,
('Presurizer_replacement','PlanA','High'):-10.07,
('Presurizer_replacement','PlanB','Low'):-9.776699029,
('Presurizer_replacement','PlanB','Medium'):-9.776699029,
('Presurizer_replacement','PlanB','High'):-9.776699029,
('Presurizer_replacement','PlanC','Low'):-9.21547651,
('Presurizer_replacement','PlanC','Medium'):-9.21547651,
('Presurizer_replacement','PlanC','High'):-9.21547651,
('Improvement_to_emergency_diesel_generators','PlanA','Low'):-2.4372,
('Improvement_to_emergency_diesel_generators','PlanA','Medium'):1.7311,
('Improvement_to_emergency_diesel_generators','PlanA','High'):4.3399,
('Improvement_to_emergency_diesel_generators','PlanB','Low'):-2.6301,
('Improvement_to_emergency_diesel_generators','PlanB','Medium'):1.8953,
('Improvement_to_emergency_diesel_generators','PlanB','High'):4.8291,
('Secondary_system_PHM_system','PlanA','Low'):35,
('Secondary_system_PHM_system','PlanA','Medium'):35,
('Secondary_system_PHM_system','PlanA','High'):35,
('Secondary_system_PHM_system','PlanB','Low'):33.98058252,
('Secondary_system_PHM_system','PlanB','Medium'):33.98058252,
('Secondary_system_PHM_system','PlanB','High'):33.98058252,
('Replacement_of_two_reactor_coolant_pumps','PlanA','Low'):-18.6,
('Replacement_of_two_reactor_coolant_pumps','PlanA','Medium'):-18.6,
('Replacement_of_two_reactor_coolant_pumps','PlanA','High'):-18.6,
('Replacement_of_two_reactor_coolant_pumps','PlanB','Low'):-17.02163486,
('Replacement_of_two_reactor_coolant_pumps','PlanB','Medium'):-17.02163486,
('Replacement_of_two_reactor_coolant_pumps','PlanB','High'):-17.02163486,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','Low'):7.4458,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','Medium'):7.6872,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','High'):5.7428,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','Low'):5.5379,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','Medium'):3.5478,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','High'):1.3533,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','Low'):4.7605,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','Medium'):2.4095,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','High'):0.6569,
('Fire_protection','PlanA','Low'):-1.44,
('Fire_protection','PlanA','Medium'):-1.44,
('Fire_protection','PlanA','High'):-1.44,
('Fire_protection','PlanB','Low'):-1.317803989,
('Fire_protection','PlanB','Medium'):-1.317803989,
('Fire_protection','PlanB','High'):-1.317803989,
('Service_water_system_upgrade','PlanA','Low'):0.7879,
('Service_water_system_upgrade','PlanA','Medium'):6.1143,
('Service_water_system_upgrade','PlanA','High'):8.6657,
('Service_water_system_upgrade','PlanB','Low'):0.6783,
('Service_water_system_upgrade','PlanB','Medium'):4.9364,
('Service_water_system_upgrade','PlanB','High'):6.4683,
('Batteries_replacement','PlanA','Low'):2.1,
('Batteries_replacement','PlanA','Medium'):2.1,
('Batteries_replacement','PlanA','High'):2.1,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','Low'):-5.03,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','Medium'):-5.03,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','High'):-5.03,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','Low'):-5.1809,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','Medium'):-5.1809,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','High'):-5.1809,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','Low'):-4.883495146,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','Medium'):-4.883495146,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','High'):-4.883495146,
('Reactor_vessel_internals','PlanA','Low'):-0.9595,
('Reactor_vessel_internals','PlanA','Medium'):19.6488,
('Reactor_vessel_internals','PlanA','High'):28.5139,
('Reactor_vessel_internals','PlanB','Low'):-0.7563,
('Reactor_vessel_internals','PlanB','Medium'):14.0932,
('Reactor_vessel_internals','PlanB','High'):18.2305,
('Reactor_vessel_upgrade','PlanA','Low'):-5.25,
('Reactor_vessel_upgrade','PlanA','Medium'):-5.25,
('Reactor_vessel_upgrade','PlanA','High'):-5.25,
('Replace_LP_turbine','PlanA','Low'):128.1380,
('Replace_LP_turbine','PlanA','Medium'):137.9677,
('Replace_LP_turbine','PlanA','High'):141.9167,
('Replace_LP_turbine','PlanB','Low'):128.2798,
('Replace_LP_turbine','PlanB','Medium'):137.2397,
('Replace_LP_turbine','PlanB','High'):140.7095,
('Replace_instrumentation_and_control_cables','PlanA','Low'):-6.52,
('Replace_instrumentation_and_control_cables','PlanA','Medium'):-6.52,
('Replace_instrumentation_and_control_cables','PlanA','High'):-6.52,
('Condenser_retubing','PlanA','Low'):2.8700,
('Condenser_retubing','PlanA','Medium'):12.6347,
('Condenser_retubing','PlanA','High'):16.8106,
('Condenser_retubing','PlanB','Low'):2.4705,
('Condenser_retubing','PlanB','Medium'):10.5147,
('Condenser_retubing','PlanB','High'):13.6791,
('Replace_moisture_separator_reheater','PlanA','Low'):1.0747,
('Replace_moisture_separator_reheater','PlanA','Medium'):5.7381,
('Replace_moisture_separator_reheater','PlanA','High'):7.7351,
('Replace_moisture_separator_reheater','PlanB','Low'):0.8547,
('Replace_moisture_separator_reheater','PlanB','Medium'):4.3390,
('Replace_moisture_separator_reheater','PlanB','High'):5.6566,
('Replace_moisture_separator_reheater','PlanC','Low'):0.7869,
('Replace_moisture_separator_reheater','PlanC','Medium'):3.9302,
('Replace_moisture_separator_reheater','PlanC','High'):5.0684,
}

model.b={('year1','S1','cap'):19,
('year1','S2','cap'):22.15,
('year1','S3','cap'):24.25,
('year1','S4','cap'):26.35,
('year1','S5','cap'):28.45,
('year1','S6','cap'):30.55,
('year1','S7','cap'):32.65,
('year1','S8','cap'):34.75,
('year1','S9','cap'):36.85,
('year1','S10','cap'):40,
('year2','S1','cap'):19,
('year2','S2','cap'):22.15,
('year2','S3','cap'):24.25,
('year2','S4','cap'):26.35,
('year2','S5','cap'):28.45,
('year2','S6','cap'):30.55,
('year2','S7','cap'):32.65,
('year2','S8','cap'):34.75,
('year2','S9','cap'):36.85,
('year2','S10','cap'):40,
('year3','S1','cap'):19,
('year3','S2','cap'):22.15,
('year3','S3','cap'):24.25,
('year3','S4','cap'):26.35,
('year3','S5','cap'):28.45,
('year3','S6','cap'):30.55,
('year3','S7','cap'):32.65,
('year3','S8','cap'):34.75,
('year3','S9','cap'):36.85,
('year3','S10','cap'):40,
('year4','S1','cap'):19,
('year4','S2','cap'):22.15,
('year4','S3','cap'):24.25,
('year4','S4','cap'):26.35,
('year4','S5','cap'):28.45,
('year4','S6','cap'):30.55,
('year4','S7','cap'):32.65,
('year4','S8','cap'):34.75,
('year4','S9','cap'):36.85,
('year4','S10','cap'):40,
('year5','S1','cap'):19,
('year5','S2','cap'):22.15,
('year5','S3','cap'):24.25,
('year5','S4','cap'):26.35,
('year5','S5','cap'):28.45,
('year5','S6','cap'):30.55,
('year5','S7','cap'):32.65,
('year5','S8','cap'):34.75,
('year5','S9','cap'):36.85,
('year5','S10','cap'):40,
('year1','S1','om'):0.08,
('year1','S2','om'):0.08,
('year1','S3','om'):0.08,
('year1','S4','om'):0.08,
('year1','S5','om'):0.08,
('year1','S6','om'):0.08,
('year1','S7','om'):0.08,
('year1','S8','om'):0.08,
('year1','S9','om'):0.08,
('year1','S10','om'):0.08,
('year2','S1','om'):0.17,
('year2','S2','om'):0.17,
('year2','S3','om'):0.17,
('year2','S4','om'):0.17,
('year2','S5','om'):0.17,
('year2','S6','om'):0.17,
('year2','S7','om'):0.17,
('year2','S8','om'):0.17,
('year2','S9','om'):0.17,
('year2','S10','om'):0.17,
('year3','S1','om'):0.05,
('year3','S2','om'):0.05,
('year3','S3','om'):0.05,
('year3','S4','om'):0.05,
('year3','S5','om'):0.05,
('year3','S6','om'):0.05,
('year3','S7','om'):0.05,
('year3','S8','om'):0.05,
('year3','S9','om'):0.05,
('year3','S10','om'):0.05,
('year4','S1','om'):0.15,
('year4','S2','om'):0.15,
('year4','S3','om'):0.15,
('year4','S4','om'):0.15,
('year4','S5','om'):0.15,
('year4','S6','om'):0.15,
('year4','S7','om'):0.15,
('year4','S8','om'):0.15,
('year4','S9','om'):0.15,
('year4','S10','om'):0.15,
('year5','S1','om'):0.14,
('year5','S2','om'):0.14,
('year5','S3','om'):0.14,
('year5','S4','om'):0.14,
('year5','S5','om'):0.14,
('year5','S6','om'):0.14,
('year5','S7','om'):0.14,
('year5','S8','om'):0.14,
('year5','S9','om'):0.14,
('year5','S10','om'):0.14,
}

model.c ={('HP_feedwater_heater_upgrade','PlanA','year1','cap'):12.99,
('HP_feedwater_heater_upgrade','PlanB','year1','cap'):0,
('Presurizer_replacement','PlanA','year1','cap'):9.15,
('Presurizer_replacement','PlanB','year1','cap'):0,
('Presurizer_replacement','PlanC','year1','cap'):0,
('Improvement_to_emergency_diesel_generators','PlanA','year1','cap'):0,
('Improvement_to_emergency_diesel_generators','PlanB','year1','cap'):0,
('Secondary_system_PHM_system','PlanA','year1','cap'):0,
('Secondary_system_PHM_system','PlanB','year1','cap'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanA','year1','cap'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanB','year1','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','year1','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','year1','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','year1','cap'):0,
('Fire_protection','PlanA','year1','cap'):1.31,
('Fire_protection','PlanB','year1','cap'):0,
('Service_water_system_upgrade','PlanA','year1','cap'):2.34,
('Service_water_system_upgrade','PlanB','year1','cap'):0,
('Batteries_replacement','PlanA','year1','cap'):0.28,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','year1','cap'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','year1','cap'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','year1','cap'):0,
('Reactor_vessel_internals','PlanA','year1','cap'):0,
('Reactor_vessel_internals','PlanB','year1','cap'):0,
('Reactor_vessel_upgrade','PlanA','year1','cap'):5.25,
('Replace_LP_turbine','PlanA','year1','cap'):0,
('Replace_LP_turbine','PlanB','year1','cap'):0,
('Replace_instrumentation_and_control_cables','PlanA','year1','cap'):5.92,
('Condenser_retubing','PlanA','year1','cap'):5.24,
('Condenser_retubing','PlanB','year1','cap'):0,
('Replace_moisture_separator_reheater','PlanA','year1','cap'):3.16,
('Replace_moisture_separator_reheater','PlanB','year1','cap'):0,
('Replace_moisture_separator_reheater','PlanC','year1','cap'):0,
('HP_feedwater_heater_upgrade','PlanA','year2','cap'):1.3,
('HP_feedwater_heater_upgrade','PlanB','year2','cap'):12.99,
('Presurizer_replacement','PlanA','year2','cap'):0.92,
('Presurizer_replacement','PlanB','year2','cap'):9.15,
('Presurizer_replacement','PlanC','year2','cap'):0,
('Improvement_to_emergency_diesel_generators','PlanA','year2','cap'):0,
('Improvement_to_emergency_diesel_generators','PlanB','year2','cap'):0,
('Secondary_system_PHM_system','PlanA','year2','cap'):4.5,
('Secondary_system_PHM_system','PlanB','year2','cap'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanA','year2','cap'):18.6,
('Replacement_of_two_reactor_coolant_pumps','PlanB','year2','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','year2','cap'):2.24,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','year2','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','year2','cap'):0,
('Fire_protection','PlanA','year2','cap'):0.13,
('Fire_protection','PlanB','year2','cap'):0,
('Service_water_system_upgrade','PlanA','year2','cap'):0,
('Service_water_system_upgrade','PlanB','year2','cap'):0,
('Batteries_replacement','PlanA','year2','cap'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','year2','cap'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','year2','cap'):4.57,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','year2','cap'):0,
('Reactor_vessel_internals','PlanA','year2','cap'):19.82,
('Reactor_vessel_internals','PlanB','year2','cap'):0,
('Reactor_vessel_upgrade','PlanA','year2','cap'):0,
('Replace_LP_turbine','PlanA','year2','cap'):0,
('Replace_LP_turbine','PlanB','year2','cap'):0,
('Replace_instrumentation_and_control_cables','PlanA','year2','cap'):0.6,
('Condenser_retubing','PlanA','year2','cap'):0,
('Condenser_retubing','PlanB','year2','cap'):0,
('Replace_moisture_separator_reheater','PlanA','year2','cap'):0,
('Replace_moisture_separator_reheater','PlanB','year2','cap'):0,
('Replace_moisture_separator_reheater','PlanC','year2','cap'):0,
('HP_feedwater_heater_upgrade','PlanA','year3','cap'):0,
('HP_feedwater_heater_upgrade','PlanB','year3','cap'):1.3,
('Presurizer_replacement','PlanA','year3','cap'):0,
('Presurizer_replacement','PlanB','year3','cap'):0.92,
('Presurizer_replacement','PlanC','year3','cap'):0,
('Improvement_to_emergency_diesel_generators','PlanA','year3','cap'):0,
('Improvement_to_emergency_diesel_generators','PlanB','year3','cap'):10.08,
('Secondary_system_PHM_system','PlanA','year3','cap'):0.3,
('Secondary_system_PHM_system','PlanB','year3','cap'):4.5,
('Replacement_of_two_reactor_coolant_pumps','PlanA','year3','cap'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanB','year3','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','year3','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','year3','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','year3','cap'):0,
('Fire_protection','PlanA','year3','cap'):0,
('Fire_protection','PlanB','year3','cap'):0,
('Service_water_system_upgrade','PlanA','year3','cap'):0,
('Service_water_system_upgrade','PlanB','year3','cap'):2.34,
('Batteries_replacement','PlanA','year3','cap'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','year3','cap'):4.57,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','year3','cap'):0.46,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','year3','cap'):0,
('Reactor_vessel_internals','PlanA','year3','cap'):0,
('Reactor_vessel_internals','PlanB','year3','cap'):0,
('Reactor_vessel_upgrade','PlanA','year3','cap'):0,
('Replace_LP_turbine','PlanA','year3','cap'):18.77,
('Replace_LP_turbine','PlanB','year3','cap'):0,
('Replace_instrumentation_and_control_cables','PlanA','year3','cap'):0,
('Condenser_retubing','PlanA','year3','cap'):0,
('Condenser_retubing','PlanB','year3','cap'):5.24,
('Replace_moisture_separator_reheater','PlanA','year3','cap'):0,
('Replace_moisture_separator_reheater','PlanB','year3','cap'):0,
('Replace_moisture_separator_reheater','PlanC','year3','cap'):0,
('HP_feedwater_heater_upgrade','PlanA','year4','cap'):0,
('HP_feedwater_heater_upgrade','PlanB','year4','cap'):0,
('Presurizer_replacement','PlanA','year4','cap'):0,
('Presurizer_replacement','PlanB','year4','cap'):0,
('Presurizer_replacement','PlanC','year4','cap'):9.15,
('Improvement_to_emergency_diesel_generators','PlanA','year4','cap'):10.08,
('Improvement_to_emergency_diesel_generators','PlanB','year4','cap'):1.1,
('Secondary_system_PHM_system','PlanA','year4','cap'):0.2,
('Secondary_system_PHM_system','PlanB','year4','cap'):0.3,
('Replacement_of_two_reactor_coolant_pumps','PlanA','year4','cap'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanB','year4','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','year4','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','year4','cap'):2.24,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','year4','cap'):0,
('Fire_protection','PlanA','year4','cap'):0,
('Fire_protection','PlanB','year4','cap'):1.31,
('Service_water_system_upgrade','PlanA','year4','cap'):0,
('Service_water_system_upgrade','PlanB','year4','cap'):0,
('Batteries_replacement','PlanA','year4','cap'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','year4','cap'):0.46,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','year4','cap'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','year4','cap'):4.57,
('Reactor_vessel_internals','PlanA','year4','cap'):0,
('Reactor_vessel_internals','PlanB','year4','cap'):0,
('Reactor_vessel_upgrade','PlanA','year4','cap'):0,
('Replace_LP_turbine','PlanA','year4','cap'):0,
('Replace_LP_turbine','PlanB','year4','cap'):18.77,
('Replace_instrumentation_and_control_cables','PlanA','year4','cap'):0,
('Condenser_retubing','PlanA','year4','cap'):0,
('Condenser_retubing','PlanB','year4','cap'):0,
('Replace_moisture_separator_reheater','PlanA','year4','cap'):0,
('Replace_moisture_separator_reheater','PlanB','year4','cap'):3.16,
('Replace_moisture_separator_reheater','PlanC','year4','cap'):0,
('HP_feedwater_heater_upgrade','PlanA','year5','cap'):0,
('HP_feedwater_heater_upgrade','PlanB','year5','cap'):0,
('Presurizer_replacement','PlanA','year5','cap'):0,
('Presurizer_replacement','PlanB','year5','cap'):0,
('Presurizer_replacement','PlanC','year5','cap'):0.92,
('Improvement_to_emergency_diesel_generators','PlanA','year5','cap'):1.1,
('Improvement_to_emergency_diesel_generators','PlanB','year5','cap'):0,
('Secondary_system_PHM_system','PlanA','year5','cap'):0,
('Secondary_system_PHM_system','PlanB','year5','cap'):0.2,
('Replacement_of_two_reactor_coolant_pumps','PlanA','year5','cap'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanB','year5','cap'):18.6,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','year5','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','year5','cap'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','year5','cap'):2.24,
('Fire_protection','PlanA','year5','cap'):0,
('Fire_protection','PlanB','year5','cap'):0.13,
('Service_water_system_upgrade','PlanA','year5','cap'):0,
('Service_water_system_upgrade','PlanB','year5','cap'):0,
('Batteries_replacement','PlanA','year5','cap'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','year5','cap'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','year5','cap'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','year5','cap'):0.46,
('Reactor_vessel_internals','PlanA','year5','cap'):0,
('Reactor_vessel_internals','PlanB','year5','cap'):19.82,
('Reactor_vessel_upgrade','PlanA','year5','cap'):0,
('Replace_LP_turbine','PlanA','year5','cap'):0,
('Replace_LP_turbine','PlanB','year5','cap'):0,
('Replace_instrumentation_and_control_cables','PlanA','year5','cap'):0,
('Condenser_retubing','PlanA','year5','cap'):0,
('Condenser_retubing','PlanB','year5','cap'):0,
('Replace_moisture_separator_reheater','PlanA','year5','cap'):0,
('Replace_moisture_separator_reheater','PlanB','year5','cap'):0,
('Replace_moisture_separator_reheater','PlanC','year5','cap'):3.16,
('HP_feedwater_heater_upgrade','PlanA','year1','om'):0.02,
('HP_feedwater_heater_upgrade','PlanB','year1','om'):0,
('Presurizer_replacement','PlanA','year1','om'):0.04,
('Presurizer_replacement','PlanB','year1','om'):0,
('Presurizer_replacement','PlanC','year1','om'):0,
('Improvement_to_emergency_diesel_generators','PlanA','year1','om'):0,
('Improvement_to_emergency_diesel_generators','PlanB','year1','om'):0,
('Secondary_system_PHM_system','PlanA','year1','om'):0,
('Secondary_system_PHM_system','PlanB','year1','om'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanA','year1','om'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanB','year1','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','year1','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','year1','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','year1','om'):0,
('Fire_protection','PlanA','year1','om'):0.01,
('Fire_protection','PlanB','year1','om'):0,
('Service_water_system_upgrade','PlanA','year1','om'):0.01,
('Service_water_system_upgrade','PlanB','year1','om'):0,
('Batteries_replacement','PlanA','year1','om'):0.01,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','year1','om'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','year1','om'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','year1','om'):0,
('Reactor_vessel_internals','PlanA','year1','om'):0,
('Reactor_vessel_internals','PlanB','year1','om'):0,
('Reactor_vessel_upgrade','PlanA','year1','om'):0.02,
('Replace_LP_turbine','PlanA','year1','om'):0,
('Replace_LP_turbine','PlanB','year1','om'):0,
('Replace_instrumentation_and_control_cables','PlanA','year1','om'):0.02,
('Condenser_retubing','PlanA','year1','om'):0.02,
('Condenser_retubing','PlanB','year1','om'):0,
('Replace_moisture_separator_reheater','PlanA','year1','om'):0.01,
('Replace_moisture_separator_reheater','PlanB','year1','om'):0,
('Replace_moisture_separator_reheater','PlanC','year1','om'):0,
('HP_feedwater_heater_upgrade','PlanA','year2','om'):0.01,
('HP_feedwater_heater_upgrade','PlanB','year2','om'):0.02,
('Presurizer_replacement','PlanA','year2','om'):0.01,
('Presurizer_replacement','PlanB','year2','om'):0.04,
('Presurizer_replacement','PlanC','year2','om'):0,
('Improvement_to_emergency_diesel_generators','PlanA','year2','om'):0,
('Improvement_to_emergency_diesel_generators','PlanB','year2','om'):0,
('Secondary_system_PHM_system','PlanA','year2','om'):0.01,
('Secondary_system_PHM_system','PlanB','year2','om'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanA','year2','om'):0.03,
('Replacement_of_two_reactor_coolant_pumps','PlanB','year2','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','year2','om'):0.2,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','year2','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','year2','om'):0,
('Fire_protection','PlanA','year2','om'):0.01,
('Fire_protection','PlanB','year2','om'):0,
('Service_water_system_upgrade','PlanA','year2','om'):0,
('Service_water_system_upgrade','PlanB','year2','om'):0,
('Batteries_replacement','PlanA','year2','om'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','year2','om'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','year2','om'):0.01,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','year2','om'):0,
('Reactor_vessel_internals','PlanA','year2','om'):0.03,
('Reactor_vessel_internals','PlanB','year2','om'):0,
('Reactor_vessel_upgrade','PlanA','year2','om'):0,
('Replace_LP_turbine','PlanA','year2','om'):0,
('Replace_LP_turbine','PlanB','year2','om'):0,
('Replace_instrumentation_and_control_cables','PlanA','year2','om'):0.01,
('Condenser_retubing','PlanA','year2','om'):0,
('Condenser_retubing','PlanB','year2','om'):0,
('Replace_moisture_separator_reheater','PlanA','year2','om'):0,
('Replace_moisture_separator_reheater','PlanB','year2','om'):0,
('Replace_moisture_separator_reheater','PlanC','year2','om'):0,
('HP_feedwater_heater_upgrade','PlanA','year3','om'):0,
('HP_feedwater_heater_upgrade','PlanB','year3','om'):0.01,
('Presurizer_replacement','PlanA','year3','om'):0,
('Presurizer_replacement','PlanB','year3','om'):0.01,
('Presurizer_replacement','PlanC','year3','om'):0,
('Improvement_to_emergency_diesel_generators','PlanA','year3','om'):0,
('Improvement_to_emergency_diesel_generators','PlanB','year3','om'):0.01,
('Secondary_system_PHM_system','PlanA','year3','om'):0.01,
('Secondary_system_PHM_system','PlanB','year3','om'):0.01,
('Replacement_of_two_reactor_coolant_pumps','PlanA','year3','om'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanB','year3','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','year3','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','year3','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','year3','om'):0,
('Fire_protection','PlanA','year3','om'):0,
('Fire_protection','PlanB','year3','om'):0,
('Service_water_system_upgrade','PlanA','year3','om'):0,
('Service_water_system_upgrade','PlanB','year3','om'):0.01,
('Batteries_replacement','PlanA','year3','om'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','year3','om'):0.01,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','year3','om'):0.01,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','year3','om'):0,
('Reactor_vessel_internals','PlanA','year3','om'):0,
('Reactor_vessel_internals','PlanB','year3','om'):0,
('Reactor_vessel_upgrade','PlanA','year3','om'):0,
('Replace_LP_turbine','PlanA','year3','om'):0.02,
('Replace_LP_turbine','PlanB','year3','om'):0,
('Replace_instrumentation_and_control_cables','PlanA','year3','om'):0,
('Condenser_retubing','PlanA','year3','om'):0,
('Condenser_retubing','PlanB','year3','om'):0.02,
('Replace_moisture_separator_reheater','PlanA','year3','om'):0,
('Replace_moisture_separator_reheater','PlanB','year3','om'):0,
('Replace_moisture_separator_reheater','PlanC','year3','om'):0,
('HP_feedwater_heater_upgrade','PlanA','year4','om'):0,
('HP_feedwater_heater_upgrade','PlanB','year4','om'):0,
('Presurizer_replacement','PlanA','year4','om'):0,
('Presurizer_replacement','PlanB','year4','om'):0,
('Presurizer_replacement','PlanC','year4','om'):0.04,
('Improvement_to_emergency_diesel_generators','PlanA','year4','om'):0.01,
('Improvement_to_emergency_diesel_generators','PlanB','year4','om'):0.01,
('Secondary_system_PHM_system','PlanA','year4','om'):0.01,
('Secondary_system_PHM_system','PlanB','year4','om'):0.01,
('Replacement_of_two_reactor_coolant_pumps','PlanA','year4','om'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanB','year4','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','year4','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','year4','om'):0.2,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','year4','om'):0,
('Fire_protection','PlanA','year4','om'):0,
('Fire_protection','PlanB','year4','om'):0.01,
('Service_water_system_upgrade','PlanA','year4','om'):0,
('Service_water_system_upgrade','PlanB','year4','om'):0,
('Batteries_replacement','PlanA','year4','om'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','year4','om'):0.01,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','year4','om'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','year4','om'):0.01,
('Reactor_vessel_internals','PlanA','year4','om'):0,
('Reactor_vessel_internals','PlanB','year4','om'):0,
('Reactor_vessel_upgrade','PlanA','year4','om'):0,
('Replace_LP_turbine','PlanA','year4','om'):0,
('Replace_LP_turbine','PlanB','year4','om'):0.02,
('Replace_instrumentation_and_control_cables','PlanA','year4','om'):0,
('Condenser_retubing','PlanA','year4','om'):0,
('Condenser_retubing','PlanB','year4','om'):0,
('Replace_moisture_separator_reheater','PlanA','year4','om'):0,
('Replace_moisture_separator_reheater','PlanB','year4','om'):0.01,
('Replace_moisture_separator_reheater','PlanC','year4','om'):0,
('HP_feedwater_heater_upgrade','PlanA','year5','om'):0,
('HP_feedwater_heater_upgrade','PlanB','year5','om'):0,
('Presurizer_replacement','PlanA','year5','om'):0,
('Presurizer_replacement','PlanB','year5','om'):0,
('Presurizer_replacement','PlanC','year5','om'):0.01,
('Improvement_to_emergency_diesel_generators','PlanA','year5','om'):0.01,
('Improvement_to_emergency_diesel_generators','PlanB','year5','om'):0,
('Secondary_system_PHM_system','PlanA','year5','om'):0,
('Secondary_system_PHM_system','PlanB','year5','om'):0.01,
('Replacement_of_two_reactor_coolant_pumps','PlanA','year5','om'):0,
('Replacement_of_two_reactor_coolant_pumps','PlanB','year5','om'):0.03,
('Seismic_modification_requalification_reinforcement_improvement','PlanA','year5','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanB','year5','om'):0,
('Seismic_modification_requalification_reinforcement_improvement','PlanC','year5','om'):0.2,
('Fire_protection','PlanA','year5','om'):0,
('Fire_protection','PlanB','year5','om'):0.01,
('Service_water_system_upgrade','PlanA','year5','om'):0,
('Service_water_system_upgrade','PlanB','year5','om'):0,
('Batteries_replacement','PlanA','year5','om'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanA','year5','om'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanB','year5','om'):0,
('Replace_CCW_piping_heat_exchangers_valves','PlanC','year5','om'):0.01,
('Reactor_vessel_internals','PlanA','year5','om'):0,
('Reactor_vessel_internals','PlanB','year5','om'):0.03,
('Reactor_vessel_upgrade','PlanA','year5','om'):0,
('Replace_LP_turbine','PlanA','year5','om'):0,
('Replace_LP_turbine','PlanB','year5','om'):0,
('Replace_instrumentation_and_control_cables','PlanA','year5','om'):0,
('Condenser_retubing','PlanA','year5','om'):0,
('Condenser_retubing','PlanB','year5','om'):0,
('Replace_moisture_separator_reheater','PlanA','year5','om'):0,
('Replace_moisture_separator_reheater','PlanB','year5','om'):0,
('Replace_moisture_separator_reheater','PlanC','year5','om'):0.01,
}

model.q = {}
model.a={}
most = {}
npvpercen = {}

#print(model.atemp)
# Variables

model.x = Var(model.I,model.J,model.wb,model.wl,model.wm, domain=NonNegativeIntegers, bounds=(0,1)) # variable x, 1 if project i is selected
model.y = Var(model.I,model.wb,model.wl,model.wm,domain=NonNegativeIntegers, bounds=(0,1)) # variable y, 1 if project i has higher priority than i
model.s = Var(model.I,model.I,domain=NonNegativeIntegers, bounds=(0,1))
model.zz = Var(model.I,model.J,domain=NonNegativeIntegers, bounds=(0,1)) # these are the z variables from the model

for i in model.wb:
    for j in model.wl:
        for k in model.wm:
            model.q[i,j,k]=model.qwb[i]*model.qwl[j]*model.qwm[k]
            #print("Probability q = ",model.q[i,j,k])
            #print("Probability q = ",model.qwb[i]*model.qwl[j]*model.qwm[k])
nscenarios = len(model.wb)*len(model.wl)*len(model.wm)


for i in model.I:
    for j in model.J:
        for k in model.wb:
            for l in model.wl:
                for u in model.wm:
                    if (i,j) in model.IJ:
                        #print(i,j,k,l,u,model.atemp[i,j,l])
                        if i in model.risklow:
                            model.a[i,j,k,l,u]=model.atemp[i,j,l]
                            #print(i,j,k,l,u,model.a[i,j,k,l,u])


for i in model.I:
    for j in model.J:
        for k in model.wb:
            for l in model.wl:
                for u in model.wm:
                    if (i,j) in model.IJ:
                        if i in model.riskmed:
                            model.a[i,j,k,l,u]=model.atemp[i,j,u]
                            #print(i,j,k,l,u,model.a[i,j,k,l,u])


for i in model.I:
    for j in model.J:
        for k in model.wb:
            for l in model.wl:
                for u in model.wm:
                    if (i,j) in model.IJ:
                        if i in model.norisk:
                            model.a[i,j,k,l,u]=model.atemp[i,j,"Low"]
                            #print(i,j,k,l,u,model.a[i,j,k,l,u])



#Objective function


def obj_rule(model):
    return sum(model.q[wb,wl,wm]*sum(model.a[i,j,wb,wl,wm]*model.x[i,j,wb,wl,wm] for (i,j) in model.IJ) \
               for wb in model.wb for wl in model.wl for wm in model.wm)

model.z = Objective(rule=obj_rule, sense=maximize)



# Constraints
def NodesIn_init(model, project_i): # here we create the set of options j for given project i
    retval = []
    for (i,j) in model.IJ:
        if i == project_i:
            retval.append(j)
    return retval
model.NodesIn = Set(model.I, initialize=NodesIn_init)

# combined 1b and 1h THIS DOES NOT CHANGE
def orderConstraintI(model, i, j):
  if i < j:
    return model.s[i,j] + model.s[j,i] == 1
  else:
      return Constraint.Skip
model.orderConstraintI = Constraint(model.I, model.I,rule=orderConstraintI)


# equation 14c from MS paper THIS DOES NOT CHANGE
def constraintY(model, i):
  return model.s[i,i] == 0
model.constraintY = Constraint(model.I,rule=constraintY)

# equation 1c MUST UPDATE
def constraint_1c(model, i, iprime, wb,wl,wm):
  if i != iprime:
    return model.y[iprime,wb,wl,wm] + model.s[i,iprime] -1 <= model.y[i,wb,wl,wm]
  else:
      return Constraint.Skip
model.constraint_1c = Constraint(model.I, model.I, model.wb,model.wl,model.wm,rule=constraint_1c)

# equation 1d MUST UPDATE
def ax_constraint_rule(model, t, wb,wl,wm,k):
  return sum(model.c[i,j,t,k]*model.x[i,j,wb,wl,wm] for (i,j) in model.IJ) <= model.b[t,wb,k]
model.ax_constraint_rule = Constraint(model.T, model.wb,model.wl,model.wm,model.K,rule=ax_constraint_rule)




#equation 1e MUST UPDATE
def constraintX(model, i,wb,wl,wm): #sum of all x[i,j]=y[i,om] for all j
  return sum(model.x[i,j,wb,wl,wm]  for j in model.NodesIn[i]) == model.y[i,wb,wl,wm]
model.constraintX = Constraint(model.I,model.wb,model.wl,model.wm, rule=constraintX)

#equation 1f MUST UPDATE
def must_constraint(model,i,wb,wl,wm):
        return model.y[i,wb,wl,wm]==1
model.must_constraint = Constraint(model.IM,model.wb,model.wl,model.wm,rule=must_constraint)

#new 1i THIS DOES NOT CHANGE
def constraint_1i(model,i,iprime,idprime):
    if i!=iprime and iprime!=idprime and idprime !=i:
       return model.s[i,iprime]+model.s[iprime,idprime]+model.s[idprime,i]<=2
    else:
       return Constraint.Skip

model.constraint_1i=Constraint(model.I,model.I,model.I,rule=constraint_1i)

#equation 1j MUST UPDATE
def consistentConstraint(model, i,i_prime,j, wb,wl,wm):
    if i!=i_prime and (i_prime,j) in model.IJ:
        return sum(model.x[i,j_prime,wb,wl,wm]  for j_prime in model.NodesIn[i] if j_prime <=j) >= model.x[i_prime,j,wb,wl,wm]+model.s[i,i_prime]-1
    else:
        return Constraint.Skip

model.consistentConstraint=Constraint(model.I,model.I,model.J,model.wb,model.wl,model.wm,rule=consistentConstraint)


# #new 1k MUST UPDATE
# def constraint_1k(model,i,j):
#     if (i,j) in model.IJ:
#         return sum(model.x[i,j,wb,wl,wm] for wb in model.wb for wl in model.wl for wm in model.wm) <= model.zz[i,j]*nscenarios
#     else:
#         return Constraint.Skip
#
# model.constraint_1k=Constraint(model.I,model.J,rule=constraint_1k)
#
# #new 1l THIS DOES NOT CHANGE
# def constraint_1l(model,i):
#     return sum(model.zz[i,j] for j in model.NodesIn[i]) <=1
# model.constraint_1l=Constraint(model.I,rule=constraint_1l)

#model.ax_constraint_rule.pprint() #this command prints just one specified constraint

results=opt.solve(model)
#model.display()
print(results)


#for con in model.component_map(Constraint).itervalues(): #this prints all constraints
        #con.pprint()
for i in model.I:
    most[i] = 16-sum(value(model.s[i,ip]) for ip in model.I)

for i in model.I:
    print(i,most[i])

for wb in model.wb:
    for wl in model.wl:
        for wm in model.wm:
            npvpercen[wb,wl,wm]=sum(model.a[i,j,wb,wl,wm]*value(model.x[i,j,wb,wl,wm]) for (i,j) in model.IJ)
            print(wb,wl,wm,npvpercen[wb,wl,wm])
