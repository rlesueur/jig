# C1 — c1-full-v1

INTERIM (11 of 150 planned trials). 

Finished trials: 11; unresolved harness errors: 3; errors later retried successfully: 0.

| condition | strategy | n | Accuracy | Prompt tokens/question | Memories kept | Approvals/question | Wall s/question |
|---|---|---|---|---|---|---|---|
| bonsai27b-ternary | full_context | 3 | 1/3 = 33% [6, 79] | 9623.3 [9344.0, 9835.0] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | 18.1 [12.3, 26.5] |
| bonsai27b-ternary | hybrid_forget | 2 | 2/2 = 100% [34, 100] | 128748.5 [122933.0, 134564.0] | 92.0 [90.0, 94.0] | 0.00 [0.00, 0.00] | 633.7 [553.4, 714.1] |
| bonsai27b-ternary | hybrid_keep | 3 | 3/3 = 100% [44, 100] | 159634.3 [137306.0, 195017.0] | 98.3 [86.0, 117.0] | 0.00 [0.00, 0.00] | 653.7 [607.4, 712.5] |
| bonsai27b-ternary | retrieval | 3 | 3/3 = 100% [44, 100] | 11212.7 [7425.0, 14225.0] | 138.3 [121.0, 173.0] | 0.00 [0.00, 0.00] | 19.1 [12.7, 26.2] |
