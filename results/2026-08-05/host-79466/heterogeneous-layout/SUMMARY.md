# Heterogeneous expert layout validation

Checkpoint A passes at parent revision `6d774a46e46e01ebde235906f290f6987b185dfa` and nested revision `1fdb55b891a3ac09ea7f4b1e7e0e50ae40060afc`.

The accepted DeepSeek V4 UD-Q3_K_XL artifact contains three routed-expert layout classes: the common 41-layer class plus distinct layer 26 and layer 42 classes. Class IDs are assigned from full canonical descriptor bytes, propagated through every cache and transfer identity, and exposed in bounded telemetry. All tiers retain one global LRU/ALWAYS policy while allocating universal, class-independent physical slots.

The real provider path exercised 516 bundles across all three classes. It stayed finite, produced token ID 270, drained all transfer and request references, and reported no trace loss or cleanup failures. The 16-slot hot pool, 20-slot cold pool, and four-lane pinned transfer ring matched their exact allocation arithmetic.

The decisive layout correctness check read real expert bytes for layers 0, 26, and 42, then ran up, gate, and down projections on CUDA using identical inputs, expert ID, and routing weight through compact and universal views. All nine comparisons were finite and bit-exact with intact padding guards. That probe and the full provider path both passed Compute Sanitizer with zero errors and zero leaked bytes. CPU Debug, CUDA Release, and CPU ASan focused suites each passed 8/8 tests.

This is mechanism evidence only. The final result remains pending until the conventional placement and heterogeneous-provider full-model matrix completes and passes Checkpoint B.
