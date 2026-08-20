# Issue 99 long-horizon quality synthesis

Status: `OBSERVED` complete campaign and primary analysis. This synthesis reports the preregistered outcomes without changing routing policy or selecting a new threshold.

## Evidence coverage

- Campaign target: parent `63cf9e59affca9d07e104c07e7c39a45094a4c9a`; nested `llama.cpp` `a702c36b4ec50db5b5f653d5177eb4d732eeaaa9`.
- Preregistration SHA-256: `73917df7f533a7a15f8b2de708c03b937613f9a13ded0d1a2c33a7aad19afdba`.
- Frozen campaign: 72/72 cells passed; all 50 changed-policy pair summaries passed.
- Analysis rows: 31,744 token, 2,246,262 substitution-event, 2,920,448 layer, and 2,920,448 route observations. Statistical uncertainty remains clustered at the prompt/semantic-family level; these rows are not treated as independent model samples.
- A platform interruption during cell 032 produced no accepted scientific outcome. Its partial files were checksum-preserved separately, and the manifest-validated campaign resumed at the first incomplete cell.
- The two release archives jointly cover all 169 files in `release-index.json`. Their Zstandard integrity, exact combined member coverage, and extracted-package reproduction passed.

## Preregistered primary outcomes

| Outcome | Result |
| --- | --- |
| `LONG_HORIZON_PREDICTIVE_DRIFT` | `gradual` |
| `KNEE_VS_S2_QUALITY_ORDERING` | `no_clear_difference` |
| `CUMULATIVE_REGRET_PREDICTIVE` | `weak` |
| `RAW_REGRET_ADDS_SIGNAL` | `weak` |
| `PERTURBED_FRACTION_ADDS_SIGNAL` | `no` |
| `TOKEN_MEDIATED_ROUTE_FEEDBACK` | `material` |
| `FEEDBACK_GROWTH_TO_1024` | `heterogeneous` |
| `FOLLOWUP_ROUTING_DESIGN_JUSTIFIED` | `no` |

The KNEE-versus-S2_P50 paired delta at the last available checkpoint was 0.001090 mean reference-NLL damage (16 prompt clusters; 95% cluster-bootstrap interval -0.001228 to 0.003205), so the registered ordering is `no_clear_difference`. S2_P50 versus EXACT showed positive direct damage in this cohort: 0.012030 mean (16 clusters; interval 0.008440 to 0.015435). That does not establish that S2_P50's long-horizon damage is acceptable; `S2P50_LONG_HORIZON_ACCEPTABLE` remains `inconclusive`.

The predictor hierarchy found only weak support when cumulative corrected regret was added to the static baseline and weak support from raw regret beyond that. Perturbed fraction did not add held-out signal, and depth conditioning did not add signal. The registered breakpoint comparison was weak, with the interval spanning zero; the evidence supports gradual rather than breakpoint drift.

Free-trajectory attribution found material token-mediated route feedback (three prompt clusters; mean amplification 1.4210, interval 1.2225 to 1.7099), but trajectories to 1,024 tokens were heterogeneous: two grew and four were non-monotonic. Capacity changed both predictive damage and realized perturbation, but this does not justify a new routing design.

The imported clean systems joins showed an inverse association between S2 systems gains and measured quality, while virtual-capacity associations were unclear. These are associations, not causal performance claims. Generation-phase interaction was unavailable because no valid prompt clusters crossed the preregistered phase split.

## Interpretation guards

- Instrumented wall time is not clean TPS authority; #98, #102, and #105 remain the systems-performance sources.
- Generated-token equality is not semantic-quality proof.
- Virtual-capacity associations are not causal proof.
- No routing parameter was tuned from issue 99 outcomes, and `FOLLOWUP_ROUTING_DESIGN_JUSTIFIED=no` means no dynamic routing mechanism is implemented or proposed as accepted.

## Reproduction

Extract both release assets into the same destination. They overlay at `issue99-long-horizon-quality-v1/analysis`. From a checkout of the release target, with the analysis dependencies installed, run:

```bash
python scripts/issue99/reproduce_release.py \
  --dataset-root <destination>/issue99-long-horizon-quality-v1/analysis/datasets \
  --expected-analysis <destination>/issue99-long-horizon-quality-v1/analysis/analysis.json \
  --output-root <output-directory>
```

The reproduction uses the frozen #105 CSV inputs committed in the checkout. It does not require the original OCI host, K3 model, or raw probe traces. The immutable archive identities and the complete per-file index are in `release-archive-index.json` and `release-index.json`.
