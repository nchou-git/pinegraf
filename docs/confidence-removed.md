Confidence scoring was removed.

LLM-reported confidence was poorly calibrated. `source.trust_weight` was a static default that was never empirically updated. The noisy-OR aggregation assumed source independence we could not verify.

Trust now flows through evidence URLs, source count for multi-source corroboration, and human `review_status` decisions such as verify and dispute.
