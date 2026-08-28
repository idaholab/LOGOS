# LPP Mutation Results

These results were generated from the `src/CPM` directory with:

```bash
python ga_priority_rule_vs_random.py benchmarks/LPP_Json/LPP_80.json --replacement-strategies diverse_elitist --output-dir results/LPP_mutation --mutations insertion_window --cxpb 0.1 --mutpb 0.9
```

The genetic algorithm used the `diverse_elitist` replacement strategy with the
`insertion_window` mutation operator, a crossover probability of `0.1`, and a
mutation probability of `0.9`.

For this run, all results produced by the priority rules were used as the
initial seed population for the genetic algorithm. This differs from the earlier
priority-rule initialization behavior that selected only the best 20% of
priority-rule results before filling the remaining population.
