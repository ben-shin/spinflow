"""Clearly synthetic, fully sampled examples; never vendor acquisitions."""
import math
import numpy as np
from .errors import NMRNextError
from .model import Axis, Dataset, positive_int


def generate_demo(*, dimensions=2, points=None, dtype="float64"):
    if isinstance(dimensions, bool) or dimensions not in (1, 2):
        raise NMRNextError("UNSUPPORTED_DIMENSIONS", "Demo generation supports 1D or 2D")
    points = tuple(points if points is not None else ((256,) if dimensions == 1 else (64,256)))
    if len(points) != dimensions:
        raise NMRNextError("INVALID_SHAPE", "Provide one --points length per physical dimension")
    for n in points:
        positive_int(n, "points")
    if dtype not in ("float32", "float64"):
        raise NMRNextError("INVALID_DTYPE", "Demo dtype must be float32 or float64")
    required = 2**dimensions * math.prod(points)*np.dtype(dtype).itemsize
    if required*8 > 256*1024**2:
        raise NMRNextError("MEMORY_BUDGET_EXCEEDED", "Demo generator limits working arrays to a conservative 256 MiB estimate")
    ids = ("t2",) if dimensions == 1 else ("t1","t2")
    axes = tuple(Axis(i,n,step=.0005 if i == "t2" else .001,
                      label="Direct" if i == "t2" else "Indirect",
                      nucleus="1H" if i == "t2" else "15N",
                      observation_mhz=600 if i == "t2" else 60,
                      reference_ppm=4.7 if i == "t2" else 118.,
                      encoding="complex" if i == "t2" else "states")
                 for i,n in zip(ids,points))
    # Two signed separable damped signals with different frequencies/linewidths.
    peaks = [{"amplitude":1.0,"frequency_hz":{"t1":125.,"t2":250.},"linewidth_hz":3.},
             {"amplitude":-.2,"frequency_hz":{"t1":-187.5,"t2":-125.},"linewidth_hz":6.}]
    data = np.zeros((2**dimensions,*points),dtype=dtype)
    for peak in peaks:
        vectors = []
        for a in axes:
            t = a.start+np.arange(a.size)*a.step
            vectors.append(np.exp((2j*np.pi*peak["frequency_hz"][a.id]-np.pi*peak["linewidth_hz"])*t))
        for channel in range(2**dimensions):
            value = np.asarray(peak["amplitude"])
            for k,v in enumerate(vectors):
                shape=[1]*dimensions; shape[k]=len(v)
                value=value*(v.imag if channel & (1<<k) else v.real).reshape(shape)
            data[channel] += value.astype(dtype)
    return Dataset(data,axes,ids,metadata={"synthetic":True,"sampling":"fully_sampled",
                   "generator":"nmrnext.demo.v1","peaks":peaks,
                   "acquisition_profile":"mathematical complex/States abstraction; no vendor encoding claim"},
                   provenance=[{"operation":{"op":"synthetic_demo","version":"1","dimensions":dimensions,"points":list(points),"dtype":dtype}}])
