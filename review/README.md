# Review probes

Runnable proofs for the findings in [../REVIEW.md](../REVIEW.md). They run against
the provided `planted_bugs/` code and need no extra dependencies.

```
python3 review/probe_rate_lock.py     # finding 2: the quoted rate is not honoured
python3 review/probe_concurrency.py   # finding 1: one quote executes many times
```

The concurrency race is timing-dependent, so a single execution can look fine.
`probe_concurrency.py` runs many rounds and reports the worst case; a correct
engine would write exactly one transaction per quote in every round.
