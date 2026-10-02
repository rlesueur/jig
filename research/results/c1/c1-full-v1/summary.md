# C1 — c1-full-v1

INTERIM (7 of 150 planned trials). 

Finished trials: 7; unresolved harness errors: 2; errors later retried successfully: 0.

| condition | strategy | n | Accuracy | Prompt tokens/question | Memories kept | Approvals/question | Wall s/question |
|---|---|---|---|---|---|---|---|
| bonsai27b-ternary | full_context | 2 | 0/2 = 0% [0, 66] | 9517.5 [9344.0, 9691.0] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | 19.4 [12.3, 26.5] |
| bonsai27b-ternary | hybrid_forget | 1 | 1/1 = 100% [21, 100] | 134564.0 [134564.0, 134564.0] | 94.0 [94.0, 94.0] | 0.00 [0.00, 0.00] | 714.1 [714.1, 714.1] |
| bonsai27b-ternary | hybrid_keep | 2 | 2/2 = 100% [34, 100] | 141943.0 [137306.0, 146580.0] | 89.0 [86.0, 92.0] | 0.00 [0.00, 0.00] | 624.2 [607.4, 641.0] |
| bonsai27b-ternary | retrieval | 2 | 2/2 = 100% [34, 100] | 10825.0 [7425.0, 14225.0] | 121.0 [121.0, 121.0] | 0.00 [0.00, 0.00] | 19.4 [12.7, 26.2] |
