# C1 — c1-pilot-2026-10-01

PILOT. INTERIM (19 of 20 planned trials). 

Finished trials: 19; unresolved harness errors: 1; errors later retried successfully: 0.

| condition | strategy | n | Accuracy | Prompt tokens/question | Memories kept | Approvals/question | Wall s/question |
|---|---|---|---|---|---|---|---|
| bonsai27b-ternary | full_context | 4 | 1/4 = 25% [5, 70] | 8146.5 [8020.2, 8300.2] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | 11.6 [9.2, 13.1] |
| bonsai27b-ternary | hybrid_forget | 4 | 3/4 = 75% [30, 95] | 35039.8 [32904.5, 36870.5] | 20.0 [15.5, 28.0] | 0.50 [0.00, 1.00] | 192.3 [117.0, 292.6] |
| bonsai27b-ternary | hybrid_keep | 4 | 3/4 = 75% [30, 95] | 44517.2 [35792.5, 57768.0] | 24.0 [17.8, 34.2] | 0.00 [0.00, 0.00] | 207.8 [167.3, 248.3] |
| bonsai27b-ternary | retrieval | 4 | 4/4 = 100% [51, 100] | 10136.5 [8326.5, 11946.5] | 43.5 [38.0, 49.0] | 0.00 [0.00, 0.00] | 30.6 [19.8, 41.3] |
| bonsai27b-ternary | rolling_summary | 3 | 2/3 = 67% [21, 94] | 7396.0 [6891.0, 7843.0] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | 98.2 [83.3, 115.1] |
