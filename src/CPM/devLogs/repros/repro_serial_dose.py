import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from datetime import datetime
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool, ResourceAvailability


def make_pert():
    start_dt = datetime(2025, 6, 1, 6, 0)
    rp = ResourcePool()
    # consumable skill: budget_per_worker=125 * peak 4 = 500 mRem total budget
    rp.resources['MECHANIC'] = ResourceAvailability(
        'MECHANIC',
        [{'start_date': datetime(2025, 1, 1), 'end_date': datetime(2025, 12, 31),
          'available_count': 4}],
        resource_type='consumable',
        dose_budget_per_worker_mrem=125.0,
    )
    ep = EquipmentPool()
    lp = LocationPool()

    def act(name, dose):
        a = Activity(name, 4.0, required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}])
        a.dose_rate_mrem_per_hour = dose   # 50*2*4 = 400 mRem per activity
        return a

    A, B, C = act('A', 50.0), act('B', 50.0), act('C', 50.0)
    S, E = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {S: [A, B, C], A: [E], B: [E], C: [E], E: []}

    p = Pert(graph=fwd)
    p.crew_pool = rp; p.equipment_pool = ep; p.location_pool = lp
    p.dose_trackers = rp.build_dose_trackers()
    p._precompute_availability_events()
    p.generateInfo()
    p.startTime = start_dt
    return p


BUDGET = 125.0 * 4          # 500 mRem
PER_ACT_DOSE = 50.0 * 2 * 4  # 400 mRem

print("Total dose budget      :", BUDGET, "mRem")
print("Dose per dose-activity :", PER_ACT_DOSE, "mRem  (only 1 of 3 fits within budget)")
print()

# ---- PARALLEL scheduler ----
p = make_pert()
p.calculateScheduleWithResources(sgs='max_use_res_ranked')
tr = p.dose_trackers['MECHANIC']
dose_acts_par = [a.name for a in p.completed if getattr(a, 'dose_rate_mrem_per_hour', 0) > 0]
print("PARALLEL: dose-activities actually placed:", sorted(dose_acts_par))
print("PARALLEL: tracker.consumed_mrem =", tr.consumed_mrem, "/", tr.total_budget_mrem)
print()

# ---- SERIAL scheduler ----
p2 = make_pert()
res = p2.calculateSerialScheduleWithResources(priority_rule='lf')
tr2 = p2.dose_trackers['MECHANIC']
dose_acts_ser = [a.name for a in p2.completed if getattr(a, 'dose_rate_mrem_per_hour', 0) > 0]
# actual physical dose incurred by the schedule serial produced:
actual_serial_dose = len(dose_acts_ser) * PER_ACT_DOSE
print("SERIAL:   dose-activities actually placed:", sorted(dose_acts_ser))
print("SERIAL:   tracker.consumed_mrem =", tr2.consumed_mrem, "(now populated on commit)")
print("SERIAL:   ACTUAL physical dose of produced schedule =", actual_serial_dose, "mRem")
over = actual_serial_dose - BUDGET
print("SERIAL:  ", "over budget by %s mRem" % over if over > 0 else "within budget (%s mRem headroom)" % -over)
print()
# SC1 is FIXED: serial now enforces the dose budget and records consumption.
if len(dose_acts_ser) <= 1 and tr2.consumed_mrem > 0 and actual_serial_dose <= BUDGET:
    print("FIXED (SC1): serial placed %d dose activity within the %s mRem budget "
          "and populated the tracker (%s mRem). Parallel placed %d."
          % (len(dose_acts_ser), BUDGET, tr2.consumed_mrem, len(dose_acts_par)))
else:
    print("BUG:", "serial placed", len(dose_acts_ser), "dose activities vs parallel",
          len(dose_acts_par), "-> serial ignores dose budget entirely.")
