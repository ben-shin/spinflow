"""Independent small binary decoders; no nmrglue or production imports."""
import struct
from pathlib import Path

import numpy as np


def raw_blocks(path):
    raw = Path(path).read_bytes()
    nb,nt,n,eb,tb,bb,version,status,nh = struct.unpack('>6i2hi',raw[:32])
    assert nt == nh == 1 and eb == 4 and tb == n*4 and bb == tb+28
    assert len(raw) == 32+nb*bb
    return np.array([struct.unpack('>'+str(n)+'f',raw[32+i*bb+28:32+(i+1)*bb]) for i in range(nb)],dtype=np.float32)


def pipe_components(path, dimensions):
    """Read archived complex, untransposed NMRPipe FIDs directly by offsets."""
    raw = Path(path).read_bytes()
    order = '<' if abs(struct.unpack('<f',raw[8:12])[0]-2.345) < 1e-5 else '>'
    header = struct.unpack(order+'512f',raw[:2048])
    assert abs(header[2]-2.345) < 1e-5
    assert header[9] == dimensions and header[221] == 0
    n = int(header[99])
    rows = 1 if dimensions == 1 else int(header[219])
    values = np.frombuffer(raw, dtype=order+'f4', offset=2048).reshape(rows,2*n)
    # NMRPipe stores all real points then all imaginary points per trace.
    if dimensions == 1:
        return np.stack((values[0,:n], values[0,n:]))
    return np.stack((values[0::2,:n],values[1::2,:n],values[0::2,n:],values[1::2,n:]))


def positive_transform(components, dimensions):
    """Independent NMRPipe-sign transform, retaining all hypercomplex parts."""
    out = components.astype(np.float64,copy=True)
    for axis in range(dimensions):
        mask = 1 << axis
        for real in range(2**dimensions):
            if real & mask:
                continue
            z = out[real]+1j*out[real|mask]
            result = np.fft.fftshift(np.fft.ifft(z,axis=axis)*z.shape[axis],axes=axis)
            out[real],out[real|mask] = result.real,result.imag
    return out


def pipe_real(path, dimensions):
    """Independently decode stored processed real payload and ppm header grids."""
    raw=Path(path).read_bytes()
    order='<' if abs(struct.unpack('<f',raw[8:12])[0]-2.345)<1e-5 else '>'
    h=struct.unpack(order+'512f',raw[:2048])
    assert h[9]==dimensions and h[221]==0 and h[220]==1
    sizes=[int(h[99])] if dimensions==1 else [int(h[219]),int(h[99])]
    offsets=[(100,119,101)] if dimensions==1 else [(229,218,249),(100,119,101)]
    values=np.frombuffer(raw,dtype=order+'f4',offset=2048).reshape(sizes)
    ppm=[]
    for size,(width,obs,origin) in zip(sizes,offsets):
        # ORIG is the final point in Hz; spectral width covers N bins.
        step=-h[width]/size
        start=h[origin]-(size-1)*step
        ppm.append((start+np.arange(size)*step)/h[obs])
    return values,ppm
