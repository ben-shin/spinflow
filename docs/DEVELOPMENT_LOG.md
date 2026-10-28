# Development log

## Stage 10 — Linux user-testing preparation

The public [Linux CI record](public-validation.md) establishes Ubuntu 24.04 source, native wheel, sdist and offscreen Qt checks for published commit `17bc266d9f341836e553f38797f576b1572b6a78`. This stage adds a guided [synthetic desktop test](linux-user-testing.md), improves Linux GUI installation and error guidance, and adds a portable path to replay a GUI processing session through the CLI. Frozen executable tree `fa1e666bd9af6cc3ca6e6570b694919fbaa586cf` passed ten automated GUI workflow groups on a virtual Ubuntu X11 display with a fresh GUI-only installed wheel. A separately upgraded installed-wheel test environment passed the complete 168-test/100-subtest suite under QEMU TCG with the stated startup-timeout allowance. The private historical work log and detailed local validation artifacts are intentionally outside this repository.

Human desktop interaction on X11/Wayland, researcher usability, permitted real acquisition processing, independent external consumer reopening and NMRPipe parity require separate evidence. The [roadmap](../ROADMAP.md) retains those gates.
