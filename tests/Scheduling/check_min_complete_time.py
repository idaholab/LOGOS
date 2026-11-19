#!/usr/bin/env python
"""
Checks only the RCPSP objective value ("MinCompleteTime") in the test output
CSV against the gold CSV.

The full RCPSP solution written by logos_rcpsp is a MILP decision-variable
assignment (which task finishes at which time step). CBC can return a
*different but equally optimal* schedule depending on solver version,
platform, and branch-and-cut tie-breaking, even though the objective value
(MinCompleteTime) is identical. Comparing the full CSV with UnorderedCsv
therefore produces false-positive diffs across platforms/CI runners. This
script narrows the check to the one value this test is actually meant to
verify: the optimal makespan.
"""
import sys
import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent
REL_TOL = 1e-6


def readMinCompleteTime(csvPath):
  """
    Read the MinCompleteTime value out of a LOGOS RCPSP output CSV.
    @ In, csvPath, Path, path to the CSV file
    @ Out, value, float, the MinCompleteTime value
  """
  with open(csvPath, newline='') as f:
    rows = list(csv.DictReader(f))
  if len(rows) != 1:
    raise ValueError('Expected exactly one data row in "{}", found {}'.format(csvPath, len(rows)))
  return float(rows[0]['MinCompleteTime'])


def main():
  """
    Compare MinCompleteTime between the test output and the gold CSV.
    @ In, None
    @ Out, code, int, 0 if matching within tolerance, else 1
  """
  testCsv = HERE / 'test_scheduling.csv'
  goldCsv = HERE / 'gold' / 'test_scheduling.csv'
  testVal = readMinCompleteTime(testCsv)
  goldVal = readMinCompleteTime(goldCsv)
  if abs(testVal - goldVal) > REL_TOL * max(abs(goldVal), 1.0):
    print('MinCompleteTime mismatch: test={} gold={}'.format(testVal, goldVal))
    return 1
  print('MinCompleteTime matches gold: {}'.format(testVal))
  return 0


if __name__ == '__main__':
  sys.exit(main())
