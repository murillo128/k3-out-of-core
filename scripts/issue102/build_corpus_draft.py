#!/usr/bin/env python3
"""Build the authored, outcome-independent draft for issue #102."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


FAMILIES = [
    ("mathematical reasoning", "quantities, domains, equations, boundary cases, and exact checks", [
        "Factor 221 and explain.",
        "Find every integer solution of x squared minus 5x plus 6 equals zero.",
        "A tank is three-fifths full; adding 84 liters makes it nine-tenths full. Find its capacity.",
        "Determine the area of a right triangle with hypotenuse 25 and one leg 7, justifying every step.",
        "A sequence starts 3, 8, 18, 38. Infer a defensible recurrence, compute two terms, and discuss ambiguity.",
        "Minimize x squared plus y squared subject to 3x plus 4y equals 20, using two methods.",
        "Count six-character strings over A, B, C, D with exactly two As and no adjacent Bs.",
        "Analyze convergence of the series sum from n equals 1 of n divided by n cubed plus 4, and bound its tail after N terms.",
    ]),
    ("formal logic / proof-style reasoning", "propositions, quantifiers, inference rules, countermodels, and discharged assumptions", [
        "Prove sqrt(2) irrational.",
        "Test whether affirming the consequent is valid and provide a countermodel.",
        "Prove by induction that the sum of the first n odd integers is n squared.",
        "Formalize: every editor reviewed some paper, but no paper was reviewed by every editor.",
        "Given P implies Q, Q implies R, and not R, derive every forced conclusion in natural deduction.",
        "Prove that a finite tree with at least two vertices has at least two leaves without using induction.",
        "Decide whether every finitely satisfiable first-order theory has a model, and explain the compactness proof schema.",
        "Compare direct proof, contradiction, and contrapositive for the claim that an integer whose square is even must be even.",
    ]),
    ("physics / scientific reasoning", "physical systems, units, causal mechanisms, uncertainty, and limiting cases", [
        "Explain why ice floats.",
        "Estimate the fall time from a 45-meter tower while neglecting air resistance.",
        "A sealed gas is heated from 290 K to 348 K at fixed volume. Predict its pressure change.",
        "Explain why seasons are caused primarily by axial tilt rather than Earth-Sun distance.",
        "A 2-kilogram cart moving at 6 meters per second sticks to a stationary 4-kilogram cart. Analyze the collision.",
        "Compare conductive, convective, and radiative heat loss from a warm metal cup in still room air.",
        "Design a controlled experiment testing whether salt concentration changes seed germination, including confounders and uncertainty.",
        "A satellite moves from a circular orbit of radius r to one of radius 4r. Derive changes in speed, period, and total energy.",
    ]),
    ("factual / explanatory knowledge", "definitions, causal sequences, actors, exceptions, timescales, and misconceptions", [
        "Explain what DNS does.",
        "Explain how a bill becomes federal law in the United States, including major exceptions.",
        "Describe why antibiotic resistance evolves and why an individual patient does not become resistant.",
        "Explain the difference between weather and climate using timescale, statistics, and examples.",
        "Describe how double-entry bookkeeping represents a purchase paid partly in cash and partly on credit.",
        "Explain how public-key cryptography enables key agreement without claiming that all metadata becomes secret.",
        "Trace the historical and technical reasons standardized shipping containers changed global trade.",
        "Explain how central banks transmit a policy-rate change through lending, asset prices, exchange rates, demand, and inflation.",
    ]),
    ("code generation", "interfaces, ownership, errors, complexity, boundaries, tests, and language semantics", [
        "Write Python to reverse words safely.",
        "Write a Rust function returning the median of a mutable slice of signed integers.",
        "Implement a TypeScript parser for comma-separated key=value fields with whitespace trimming.",
        "Write a C function that reads an entire regular file with bounded allocation and explicit error handling.",
        "Implement a Python context manager that acquires two locks in stable order and always releases them.",
        "Write a Go HTTP handler that validates JSON, enforces a size limit, and returns structured errors.",
        "Implement an immutable Java interval set supporting union, containment, and normalized nonoverlapping storage.",
        "Write a C++17 bounded worker queue with graceful shutdown, exception-safe task ownership, and tests for racing producers.",
    ]),
    ("debugging / code review", "symptoms, minimal reproductions, ownership, control flow, fixes, and regression evidence", [
        "Diagnose division by zero.",
        "Review a loop that erases vector elements while incrementing the same iterator.",
        "Diagnose a Python cache whose default argument is an empty dictionary shared across calls.",
        "Review SQL joining orders to items, summing order totals, and unexpectedly double-counting each order.",
        "A multithreaded logger sometimes emits truncated lines during shutdown. Explain likely lifetime and synchronization faults.",
        "Review a retry loop that catches every exception, sleeps exponentially, and never caps attempts or elapsed time.",
        "Diagnose a service where memory grows after clients cancel streamed responses, while completed streams remain stable.",
        "Review C++ code storing string_view keys in a map while request buffers are recycled by another thread.",
    ]),
    ("algorithms / data-structure reasoning", "input models, invariants, pseudocode, correctness proofs, complexity, and adversarial cases", [
        "Find duplicates in linear time.",
        "Choose a data structure for an undo stack with snapshots and bounded memory.",
        "Design an algorithm merging k sorted streams when only one item per stream fits in memory.",
        "Compare BFS and bidirectional BFS for shortest paths in an unweighted social graph.",
        "Design an online algorithm for the median of a numeric stream with insertions but no deletions.",
        "Given a directed graph, find all vertices belonging to at least one cycle and justify complexity.",
        "Design an external-memory deduplication pipeline for records larger than RAM while preserving first-seen order.",
        "Analyze a concurrent LRU cache requiring expected O(1) lookup, bounded capacity, and no use-after-free during eviction.",
    ]),
    ("summarization / synthesis", "decisions, evidence, conflicts, themes, uncertainty, timing, and negative findings", [
        "Synthesize reopened roads after rain.",
        "Summarize this update: launch moved to Friday; security review passed; documentation remains incomplete.",
        "Synthesize: users value speed, support logs show setup failures, and churn peaks during onboarding.",
        "Summarize a meeting where finance approved the pilot, legal requested a retention clause, and engineering flagged capacity risk.",
        "Synthesize evidence that sales rose, margins fell, returns increased, and a price promotion changed customer mix.",
        "Synthesize three reports: wetlands reduced flood peaks, nearby housing costs increased, and maintenance funding remains uncertain.",
        "Summarize competing incident accounts where monitoring shows latency first, logs show retries later, and operators recall a network change.",
        "Create a balanced synthesis of a policy debate involving commuting time, construction emissions, unequal neighborhood impacts, uncertain ridership, and constrained budgets.",
    ]),
    ("structured extraction / transformation", "schemas, raw values, nulls, ambiguity, stable identifiers, validation, and loss accounting", [
        "JSON for Ana, 31.",
        "Convert 2026-03-04, north, 17.5 into a typed JSON object with explicit units.",
        "Extract people, organizations, dates, and commitments from: Priya told Acme Tuesday the audit would finish Friday.",
        "Transform a markdown task list into CSV columns for item, owner, due date, status, and dependency.",
        "Normalize mixed addresses into street, locality, region, postal code, country, and parse-warning fields.",
        "Extract medication, dose, route, frequency, start date, and uncertainty from inconsistent narrative instructions.",
        "Transform nested invoice JSON into one row per item while preserving currency, tax, customer, and source identifiers.",
        "Design a loss-aware transformation for multilingual event records with partial dates, local times, aliases, units, and conflicting identifiers.",
    ]),
    ("planning / constraint satisfaction", "hard constraints, preferences, capacities, dependencies, slack, fallbacks, and validation", [
        "Plan three errands before noon.",
        "Schedule four interviews in two rooms, avoiding interviewer conflicts and a lunch blackout.",
        "Plan a two-day migration with one maintenance window, a rollback deadline, and no simultaneous replicas offline.",
        "Create a weekly meal plan under budget with vegetarian Tuesdays, two leftover nights, and one shared ingredient per adjacent day.",
        "Plan deliveries for three vehicles with capacity limits, time windows, a driver break, and one refrigerated route.",
        "Construct staffing where every shift has two certified operators, nobody exceeds ten hours, and absences are honored.",
        "Plan a launch across legal, localization, support, and infrastructure with dependencies, uncertain reviews, and one contingency day.",
        "Design a phased evacuation for four zones sharing two exits, limited buses, hospital priority, changing wind, and preserved emergency access.",
    ]),
    ("multi-step instruction following / structured response", "step order, exact labels, carried facts, permitted audiences, consistency, and output syntax", [
        "Define entropy; give an analogy.",
        "List three risks, rank them, and mitigate the highest risk.",
        "Extract a proposal's goal, rewrite it plainly, then ask two clarifying questions.",
        "Classify five statements as fact or opinion, explain uncertain cases, then output counts as JSON.",
        "Draft a customer reply, an internal escalation note, and a checklist; exclude confidential details from the reply.",
        "Analyze a project update in four sections: completed work, blockers, changed assumptions, and next actions with owners.",
        "For a dataset description, define fields, identify validation rules, propose three tests, simulate an invalid row, and summarize errors.",
        "Produce a decision memo with an executive answer, evidence table, counterargument, risk register, reversible pilot, and stop conditions, in that order.",
    ]),
    ("analytical comparison / argumentation", "common criteria, evidence, values, stakeholders, uncertainty, reversibility, and adverse scenarios", [
        "Compare renting and buying.",
        "Compare REST and event-driven integration for a small internal service.",
        "Argue for and against open-book exams, then state which evidence would change the conclusion.",
        "Compare congestion pricing and parking reform for reducing downtown traffic without assuming equal distributional effects.",
        "Evaluate build-versus-buy for identity using cost, security, lock-in, staffing, reliability, and migration risk.",
        "Compare randomized trials and observational studies when randomization is expensive and attrition is uneven.",
        "Assess a seawall with known capital cost versus wetland restoration with uncertain land acquisition and ecosystem benefits.",
        "Construct the strongest case for and against regulating a changing general-purpose technology, including enforcement, innovation, concentration, and reversible options.",
    ]),
    ("creative / language generation", "setting, voice, imagery, conflict, pacing, consistency, implication, and ending resonance", [
        "Write a four-line winter poem.",
        "Write a micro-story where a lost key becomes good news, without supernatural explanations.",
        "Create a radio advertisement for a repair cafe using warmth, one sound cue, and no exaggerated claims.",
        "Write dialogue between an old bridge and a new bicycle, giving each a distinct voice without naming emotions.",
        "Write an opening where a night-shift baker discovers a town map printed incorrectly, using sensory detail and restrained humor.",
        "Write a fable about cooperation between a patient heron and an impulsive otter, without an explicit moral sentence.",
        "Draft a museum audio-guide monologue by a cracked ceramic bowl whose provenance is disputed, balancing imagination with uncertainty.",
        "Write a speculative scene about a city renting memories for planning; include intimate conflict, a bureaucratic artifact, and an ending image that reinterprets an earlier detail.",
    ]),
    ("conversational / direct QA", "direct answers, low-risk actions, reasons, clarification, warning signs, tradeoffs, and limits", [
        "Why is the sky blue?",
        "I missed a deadline. How should I tell my teammate today?",
        "What is the practical difference between saving and investing for a beginner?",
        "My laptop battery suddenly drains overnight while asleep. What should I check first and why?",
        "I reread technical pages without retaining them. Suggest a realistic study routine for this week.",
        "A friend wants immediate advice about a stressful job choice, but I do not know their finances. How can I help without taking over?",
        "I am organizing a neighborhood meeting where two groups distrust each other. How can I surface concrete issues without escalating blame?",
        "Two professionals gave conflicting explanations for recurring home moisture. How should I compare them, gather evidence safely, and decide before major repairs?",
    ]),
    ("Spanish-language reasoning/explanation", "facts, assumptions, mechanisms, exceptions, uncertainty, and conclusions", [
        "Explica la lluvia.",
        "Explica la diferencia entre media y mediana con un ejemplo sencillo.",
        "Una familia ahorra electricidad, pero paga más. Analiza causas sin suponer un error.",
        "Explica cómo evaluar si una noticia viral es fiable, separando fuente, evidencia e interpretación.",
        "Compara transporte urbano por tiempo, costo, accesibilidad, emisiones y efectos entre barrios.",
        "Analiza un experimento donde plantas con más luz crecieron menos, incluyendo confusores, mediciones y seguimiento.",
        "Explica cómo funciona una cooperativa de ahorro y crédito, sus incentivos compartidos y los riesgos que permanecen.",
        "Razona sobre una política de agua en sequía que proteja consumo básico, agricultura, ecosistemas y pequeñas empresas con datos incompletos.",
    ]),
    ("multilingual / translation / cross-language transformation", "meaning, register, entities, numbers, ambiguity, terminology, notes, and equivalent obligations", [
        "Translate buenos días.",
        "Translate The meeting moved to Thursday into French and German, preserving a neutral tone.",
        "Translate a polite Japanese request for a revised invoice into business English and explain one honorific nuance.",
        "Convert a Spanish customer complaint into an empathetic English reply plus a factual internal summary.",
        "Translate an English safety notice into plain Spanish and Brazilian Portuguese, preserving every prohibition and emergency number.",
        "Reconcile bilingual product descriptions where French promises waterproofing but English says only water resistance.",
        "Translate a Korean project update into English, then extract dates, owners, blockers, and uncertainty without flattening indirect language.",
        "Produce aligned English, Spanish, and Arabic public-service announcements about a water shutdown, preserving times, addresses, accessibility instructions, and respectful register.",
    ]),
]

DETAILS = [
    "Start by defining the relevant {focus}.",
    "Separate observations from assumptions and make every important dependency explicit.",
    "Organize the response as a sequence whose intermediate conclusions can be checked independently.",
    "Include one concrete example that exercises a nontrivial aspect of the task.",
    "Handle missing information without inventing facts, values, sources, or consensus.",
    "Identify boundary conditions, exceptions, and failure modes that could change the answer.",
    "Explain why the selected method fits better than one credible alternative.",
    "Track terminology and quantities consistently across the complete response.",
    "State uncertainty at the same level of precision supported by the provided information.",
    "Consider the strongest plausible counterexample or adverse scenario before concluding.",
    "Give a compact validation check that would reveal a mistaken result or interpretation.",
    "Distinguish reversible next steps from costly or irreversible commitments.",
    "End with a concise answer that preserves all material qualifications.",
]


def build_prompt(core: str, focus: str, band: int) -> str:
    if band == 1:
        return core
    count = 2 * band - 3
    start = (band * 3) % len(DETAILS)
    clauses = [DETAILS[(start + i) % len(DETAILS)].format(focus=focus) for i in range(count)]
    return core + " Requirements: " + " ".join(clauses)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    assert len(FAMILIES) == 16 and all(len(cores) == 8 for _, _, cores in FAMILIES)
    cases = []
    for band in range(1, 9):
        for family_index, (family, focus, cores) in enumerate(FAMILIES, start=1):
            cases.append({
                "id": f"f{family_index:02d}-b{band}",
                "family_index": family_index,
                "semantic_family": family,
                "token_band": band,
                "raw_prompt": build_prompt(cores[band - 1], focus, band),
            })
    result = {
        "schema_version": "issue102-cross-prompt-corpus-draft-v1",
        "status": "tokenizer-preflight-only",
        "construction": "authored distinct tasks plus rotated meaningful requirements; no performance inputs",
        "cases": cases,
        "sentinel": {
            "id": "issue102-sentinel",
            "semantic_family": "sentinel",
            "token_band": 0,
            "raw_prompt": "Explain why a careful measurement should distinguish observed facts from assumptions.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
