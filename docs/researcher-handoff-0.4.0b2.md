# Fixed 0.4.0b2: Ubuntu researcher trial

**Unreleased experimental candidate, not V1. Human acceptance: NOT RUN.** Use native Ubuntu 24.04 x86-64 and CPython 3.12 with `venv` support. Keep this installation separate from the public **0.4.0b1** release.

**Obtain the exact build.** Sign into GitHub if required; open [build run 43348956935](https://github.com/ben-shin/spinflow/actions/runs/43348956935) and download **spinflow-experimental-release-assets** (artifact `14002448604`). Extract its six files together into a new local `assets` folder. Retained intake evidence listed this artifact as available; its current availability has not been rechecked. If missing/expired, stop and request these exact retained assets—do not substitute another same-version build.

In `build-manifest.json`, require version `0.4.0b2`, source **`9c09e89ae1d2928097640f8c55451c6916ed5b53`**, and ABI `cp312`. Run `sha256sum -c SHA256SUMS`; all five listed files must pass. Also match these independently retained pins before executing:

- `install-spinflow.py`: `4edeea8b08f667795fe0a1323cdcd79bd20f982af5f467d955c2577578666166`
- `spinflow-0.4.0b2-cp312-cp312-linux_x86_64.whl`: `b6106203841b0f9d2e3d592c1817b5ca4012fc10da7b8532df9a96c5a20b01b4`

From that `assets` folder, choose an **absent** prefix:

```bash
python3.12 install-spinflow.py --version 0.4.0b2 --asset-dir "$PWD" \
  --prefix "$HOME/.local/share/spinflow/b2-fixed-9c09e89" --gui --plot
source "$HOME/.local/share/spinflow/b2-fixed-9c09e89/bin/activate"
spinflow --version
spinflow doctor
python -m pip check
spinflow gui
```

Require version `0.4.0b2` and native backend available. The local wheel needs no compiler; Python dependencies still require downloads, and Qt may need desktop runtime libraries. Use a terminal outside any source checkout. Stop and retain logs on installation failure.

Mark each check **PASS / FAIL / NOT RUN**, keeping originals and using fresh outputs:

1. **View:** try **1D demo**, **2D demo**, dark contours and both signs; right-drag/release to zoom, then **View All**. Record unreadable controls or lost features.
2. **Your raw FID:** use **Open FID folder…** for a documented supported profile. Inspect axes/components; apply only reviewed processing. **Save runnable workflow…** saves a bundle. In **Load ordered recipe…**, choose its newly saved `workflow.json`, keep `source.nmrh5` beside it, then press **Process** before comparing: loading restores the source and pending recipe. An audit report is not a runnable recipe. Record refusals; this does not establish NMRPipe equivalence.
3. **Two spectra:** save a fixed-label template, map axes for two compatible inputs in **Batch**, then save/open the manifest, **Plan**, **Apply to files**, and **Export CSV…**. Check IDs, heights, signed relative volumes and missing/ambiguous flags; blanks are not zero. Use points only on identical grids, Hz in a compatible frame, ppm only with justified calibration.
4. **External reopen:** export NMRPipe/UCSF only after reviewing/acknowledging every loss. In your independent application, compare orientation, coordinates and signs against predeclared expectations; record its version. These exports retain one real F32 component. Unknown chemical referencing remains unknown.
5. **Report:** copy the existing [blank acceptance form](https://github.com/ben-shin/spinflow/blob/9c09e89ae1d2928097640f8c55451c6916ed5b53/docs/acceptance-form-0.4.0b1.md), replace its B1 candidate fields with the source/build above, and attach versions, `doctor`, logs and all failures. Keep your data private.

Automated evidence: **1,280 tests + 101 subtests passed; 16 cache-gated cases skipped** in the CP312 installed suite. CP313 CI includes installer/GUI/plot smoke, not a second full installed suite. Physical human testing, independent consumer acceptance and full original-method/reference gates remain open; no performance gain is claimed.
