# Stage 23: one conversion and a header-only observation

One unchanged 493-byte C-shell script from the cached public Varian 2D case was
run once with NMRPipe 13.0 revision 2026.072.12.03. The script contains only
the original conversion step; the original processing script was not supplied.
The separate 14-operation SpinFlow recipe is validation-authored and is not
the original processing workflow. The retained output is a private reference
artifact, not a public example or redistributed vendor/data package.

The conversion ran from new private working copies under Ubuntu 24.04.5,
CPython 3.12.3, one CPU and one numerical thread. It exited zero, was reaped,
and left no owned process group. Its private output is 3,986,048 bytes
(SHA-256 `66cf36c912487fc939bb513a4a7f18758e0aad9526a758c78fc3f86735182e95`).
The immutable input script has SHA-256
`71622387d57b4d80e677e2facddeb1e92273429424a40641ae889fc7789eeb6b`.
NMRPipe help was observed from the installed core, but its nonzero help exit
was retained; the normal help-exit convention is unverified. The archived
vendor suite and output remain private.

## Header audit

An independent audit read only the first 2,048 bytes and interpreted header
fields 0–2. The observed little-endian bytes for field 1 were `efee6e4f`, or
unsigned word `0x4f6eeeef`; as float32, this is `4008636160.0`, the numeric
float32 encoding of the `FDIEEECONS` integer `0xeeeeeeee`. Field 2 decoded as
`2.3450000286102295`. This resolves the observed marker representation for
this artifact.

The frozen private v3 checker instead requires the raw unsigned word
`0xeeeeeeee`, so its A23-L02 compatibility check fails on this observed
header. This is a checker representation defect; no SpinFlow sample mismatch
was demonstrated. The checker remains unchanged. No sample arrays, product
decoder, comparator, FFT, phase, peaks, volumes or equivalent benchmark ran.

| Qualification | Recorded result |
| --- | --- |
| One original conversion and retained reference artifact | PASS |
| Independent first-header-fields audit | PASS |
| Frozen private v3 raw-word compatibility guard (A23-L02) | FAIL |
| Scientific array, coordinate, sign, phase, peak and volume gates | UNVERIFIED |
| Original processing-script and physical/researcher acceptance | UNVERIFIED |

These observations do not promote a scientific gate, support profile or
compatibility registry. Strict zero-tolerance raw float32 quadrature rules
remain unchanged; no alignment, scaling, phase fitting or tolerance relaxation
was applied. S19 remains FAIL on six of eighteen targets. The released S21 R6
whole-process-group timeout guard remains FAIL, with repair pending approval
and independent review. Scientific phase acceptance remains false and
independent physical-human acceptance remains UNVERIFIED. See the
[laboratory workflow protocol](laboratory-workflow-protocol.md) for the
declared comparison criteria and the [Stage 22 tutorial record](stage22-results.md)
for the separate acquired-data tutorial. This observation does not establish
NMRPipe parity or a general compatibility claim.
