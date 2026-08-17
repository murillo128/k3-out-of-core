# Issue 99 tooling provenance

The issue-99 quality helper is parent-owned research tooling linked against the
unchanged pinned K3 runtime. It adapts production-envelope, telemetry, route
observer, and trace-recording patterns from these MIT-licensed project inputs:

| Input | Frozen identity |
|---|---|
| `llama.cpp/tests/phase13-mode-p-probe.cpp` | nested commit `a702c36b4ec50db5b5f653d5177eb4d732eeaaa9`; SHA-256 `63aac2223dd14a5d466939f4ee6cc80a1d406f04c6de12d94cd58c41b590ea91` |
| `llama.cpp/tests/phase13-routing-probe.cpp` | nested commit `a702c36b4ec50db5b5f653d5177eb4d732eeaaa9`; SHA-256 `896270c0085454c9381e03fa3a0136c35705a6a1434b90014bd1223a24875072` |
| issue-102 generated cross-prompt helper | `/mnt/nvme1/issue102/build/issue102-cross-prompt-probe.cpp`; SHA-256 `1ba6c540196e7f777f9ec761813b240057f08abfbc5ce711cae037c4babf8b43` |
| `llama.cpp/LICENSE` | SHA-256 `94f29bbed6a22c35b992c5c6ebf0e7c92f13b836b90f36f461c9cf2f0f1d010d` |

No third-party implementation was imported beyond those already accepted
project sources. The nested gitlink and production routing/cache code remain
unchanged.
