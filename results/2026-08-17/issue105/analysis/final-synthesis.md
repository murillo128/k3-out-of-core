# Issue 105 final synthesis

Status: `PASS` for the deterministic offline analysis package. All results below are `POST_HOC_EXPLORATORY`.

## Headline results

- `TPS_PROJECTION_GATE = PASS` using `M2` / `loads_per_token`. Primary LOFO R² is 0.993536 with RMSE 0.000928; protocol-compatible sensitivity LOFO R² is 0.992656 with RMSE 0.001406. Both family-cluster bootstrap and family/policy/length-level residual gates pass.
- Virtual-capacity analysis reports discrete published brackets, never fitted thresholds or extrapolation: 44/44 cases are bracket-consistent. Across hit-derived physical-reference EXACT amplification intervals, the median lower/upper endpoints are 1.247×/1.497× (lower-endpoint range 1.000–1.497×; upper-endpoint range 1.247–1.996×). Across EXACT/S2 fixed-route amplification intervals, the median lower/upper endpoints are 1.224×/1.996×. Bounds use monotone endpoint propagation; no quotient of two upper endpoints is presented as a bound, exact RAM threshold, or measured saving.
- The best single frozen working-set feature for physical hit ratio is `top16_selected_mass_fraction` with pooled LOFO R² 0.465884.
- Recurrent selected-expert cores exist under top-k/top-M observables, but committee pinning is heterogeneous and preserves 308 regressing fixed-route cells.
- Actual-token effects remain weak/heterogeneous after family adjustment; the constructed 16×8 corpus is not treated as an IID prompt-population sample.
- Prior-art values are classified claim by claim as normalized-comparable or qualitative-only; heterogeneous raw TPS is never pooled.

## Authority limits

- Physical TPS/locality: `MEASURED_PHYSICAL` only.
- Larger capacity: `EXACT_REPLAY` or `FIXED_ROUTE_COUNTERFACTUAL`.
- Any permitted projected TPS: `TPS_PROJECTION`, constrained to the measured predictor domain.
- Semantic sanity is narrow and is not long-horizon quality.
- Fixed-route replay does not identify autoregressive route-feedback causality; that remains unmeasured and belongs to #99.
- No policy was designed, tuned, benchmarked, or authorized for production by this issue.
