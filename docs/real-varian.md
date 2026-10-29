# Controlled Varian / Agilent acquisition profiles

SpinFlow reads a bounded float32 `procpar`/`fid` profile. Raw vendor writing is
not implemented. This is not general VNMR/VnmrJ compatibility.

- `varian-raw-1d` (`varian1d`, `agilent1d`): one unarrayed complex FID.
- `varian-raw-2d` (`varian2d`, `agilent2d`): the acquired `cpjdcptan2d`
  C13/N15 States experiment, `array=phase`, `phase=[1,2]`, with two blocks per
  indirect increment. Other pulse-sequence mappings require separate validation.

Both require big-endian float32 samples, version 0, status 73 or 201, one trace
and one 28-byte header per block, exact file lengths, and consistent `procpar`
dimensions. Integer data, block scaling, drift corrections, higher dimensions,
NUS, arrayed acquisitions and ambiguous indirect encodings fail explicitly.
Recorded block phases and acquisition fields are retained; they are not applied.

All raw procpar text, parsed fields, file and block headers, source byte counts,
and SHA-256 hashes survive native storage. The source directory is read only.
Unknown acquisition fields remain available as source metadata. The adapter
reuses the already pinned nmrglue binary codec; no new dependency is needed.
Read budgets include a conservative allowance for numeric copies. Python import
overhead and metadata objects are additional process overhead, not a hard RSS cap.

The axes are `(a0,)` for 1D and `(indirect a0, direct a1)` for 2D. Channels are
`R,I` or `RR,IR,RI,II`, with the first quadrature bit belonging to `a0`. Original
acquired signs are retained. nmrglue's documented Varian-to-NMRPipe conversion
conjugates direct data and negates alternate acquired States traces. Consequently
the independently archived NMRPipe channels map to SpinFlow with fixed factors
`[1,-1]` or `[1,-1,-1,1]`. This is independent-axis conjugation for SpinFlow's
negative-exponent DFT. It is fixed before comparison, never fitted to peaks.
Bare FFT coordinates are mathematical offsets, not yet the acquisition's
physical frequency offsets. For these retained-pair-sign profiles the explicit
`reference` operation must use `frequency_scale: -1` on each physical axis.
It reflects the Hz coordinate grid before anchoring ppm, without changing any
sample. This calibration is included in both example recipes. The FFT kernel
and its mathematical grid have no vendor-specific behavior.

Spectral widths and observation frequencies come from `sw/sfrq` and, for the
bounded 2D sequence, `sw1/dfrq2`. The original conversion examples supply their
own chemical references. SpinFlow therefore leaves ppm referencing unset: apply
the explicit `reference` operation after the FFT, with the frequency scale above.
It does not infer a reference
from inconsistent `reffrq` fields or apply stored phase settings automatically.

## Real-data evidence

The official nmrglue conversion examples contain acquired 1D and 2D data. The
separately archived processing examples contain stored NMRPipe time-domain
files; plotting examples contain stored processed spectra. These files predate
this implementation. Source scripts are recorded and never executed on import.

| Case | Raw shape | Raw fid bytes | Independent stored raw reference |
|---|---|---:|---|
| C13 1D | 1500 complex | 12,060 | `processing/nmrpipe_1d/test.fid` |
| C13/N15 2D | 166 × 1500 hypercomplex | 3,993,328 | `processing/nmrpipe_2d/test.fid` |

The manifest pins six archives, each selected member, provenance URLs, and
comparison tolerances. No explicit redistribution license was located for the
acquisition archives, so fetched data remain in a private ignored cache. The
nmrglue BSD license applies to its code and does not establish data permissions.

```sh
python tests/fixtures/fetch_varian_public.py
python tools/validate_public_varian.py --backend native \
  --report .artifacts/coverage-formats/raw-report.json
SPINFLOW_VARIAN_ROOT=.artifacts/coverage-formats pytest tests/io/test_varian_public.py
```

The validator independently decodes the archived NMRPipe bytes and raw Varian
bytes without production helpers or nmrglue. Every acquired quadrature must
match exactly after the fixed convention mapping. Native FFTs on both physical
axes are compared with independently computed positive-exponent transforms of
the archived data; the preregistered relative maximum-error limit is `2e-6`.
Source hashes must remain unchanged. Both NumPy and native backends are tested.

The additional full-processing gate runs the fixed archived recipes using
shifted sine windows, zero filling and explicitly transformed phases. For the
conjugated native convention, both NMRPipe phase angles change sign because
both phase operators multiply by `exp(i*(p0+p1*j/N))`. No phase fitting occurs.
The all-real output must agree within relative maximum error `2e-5`, peak bins
must match exactly, and signed sums in the five externally specified 2D boxes
(or four fixed 1D quarters) must agree within relative error `2e-5`. Every
calibrated ppm coordinate is checked against independently decoded archived
headers within `2e-5` ppm, accounting for rounded float32 header calibration.
All other native quadratures are retained; the processed archive supplies only
the real component, so it cannot establish full processed quadrature parity.

Run the native workflows after fetching the private corpus:

```sh
spinflow run examples/varian-1d-public.json
spinflow run examples/varian-2d-public.json
```

The recipes explicitly declare a 2 GiB working-array budget and write new native
files in the private cache. They retain float32 and all quadratures. Sine-window
`off` and `end` default to `0` and `1`; setting them uses
`sin(pi*off + pi*(end-off)*j/(N-1))**power`. An explicit repeated
`frequency_scale: -1` reflects the current grid again, undoing the first
reflection. Inverse FFT rejects a reflected grid until this calibration is
explicitly undone. There is no claim of general vendor phase, chemical-reference,
quantitative-integral or external-consumer parity.

Sources: [official 1D example](https://nmrglue.readthedocs.io/en/latest/examples/agilent2pipe_1d.html),
[official 2D example](https://nmrglue.readthedocs.io/en/latest/examples/agilent2pipe_2d.html),
[nmrglue 0.12 conversion source](https://github.com/jjhelmus/nmrglue/blob/v0.12/nmrglue/fileio/convert.py),
[stored 1D processing example](https://nmrglue.readthedocs.io/en/latest/examples/process_pipe_1d.html),
[stored 2D processing example](https://nmrglue.readthedocs.io/en/latest/examples/process_pipe_2d.html).
