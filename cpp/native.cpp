#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <complex>
#include <cstddef>
#include <limits>
#include <vector>

#include "vendor/pocketfft/pocketfft_hdronly.h"

namespace py = pybind11;

template <typename T>
py::array fft_typed(const py::array &input, std::size_t axis, bool inverse,
                    std::size_t threads) {
  const auto typed = py::reinterpret_borrow<py::array_t<std::complex<T>>>(input);
  std::vector<py::ssize_t> py_shape;
  pocketfft::shape_t shape;
  pocketfft::stride_t input_strides;
  py_shape.reserve(static_cast<std::size_t>(typed.ndim()));
  shape.reserve(static_cast<std::size_t>(typed.ndim()));
  input_strides.reserve(static_cast<std::size_t>(typed.ndim()));
  for (py::ssize_t i = 0; i < typed.ndim(); ++i) {
    py_shape.push_back(typed.shape(i));
    shape.push_back(static_cast<std::size_t>(typed.shape(i)));
    input_strides.push_back(static_cast<std::ptrdiff_t>(typed.strides(i)));
  }

  py::array_t<std::complex<T>> output(py_shape);
  pocketfft::stride_t output_strides;
  output_strides.reserve(shape.size());
  for (py::ssize_t i = 0; i < output.ndim(); ++i) {
    output_strides.push_back(static_cast<std::ptrdiff_t>(output.strides(i)));
  }

  const auto *source = typed.data();
  auto *destination = output.mutable_data();
  const T scale = inverse ? T(1) / static_cast<T>(shape[axis]) : T(1);
  {
    py::gil_scoped_release release;
    pocketfft::c2c<T>(shape, input_strides, output_strides, {axis}, !inverse,
                      source, destination, scale, threads);
  }
  return output;
}

py::array fft_complex(const py::object &object, py::ssize_t axis, bool inverse,
                      py::ssize_t threads) {
  if (!py::isinstance<py::array>(object)) {
    throw py::type_error("fft_complex requires a NumPy ndarray; no implicit conversion is performed");
  }
  const auto input = py::reinterpret_borrow<py::array>(object);
  const auto ndim = input.ndim();
  if (ndim == 0) {
    throw py::value_error("fft_complex requires at least one axis");
  }
  if (axis < -ndim || axis >= ndim) {
    throw py::value_error("fft_complex axis is out of range");
  }
  if (axis < 0) {
    axis += ndim;
  }
  if (input.shape(axis) == 0) {
    throw py::value_error("fft_complex cannot transform an empty axis");
  }
  if (threads < 1) {
    throw py::value_error("fft_complex threads must be positive");
  }
  if ((input.flags() & py::array::c_style) == 0) {
    throw py::value_error("fft_complex requires C-contiguous input; copy explicitly to control memory use");
  }
  const auto thread_count = static_cast<std::size_t>(threads);
  const auto normalized_axis = static_cast<std::size_t>(axis);
  if (input.dtype().is(py::dtype::of<std::complex<float>>())) {
    return fft_typed<float>(input, normalized_axis, inverse, thread_count);
  }
  if (input.dtype().is(py::dtype::of<std::complex<double>>())) {
    return fft_typed<double>(input, normalized_axis, inverse, thread_count);
  }
  throw py::type_error("fft_complex accepts only native-endian complex64 or complex128 arrays");
}

PYBIND11_MODULE(_native, module) {
  module.doc() = "PocketFFT complex transforms; input is preserved and output is independently owned";
  module.attr("__version__") = "0.1.0";
  module.attr("pocketfft_commit") = "4d865cba24359b05b37da4e75aee24498c3b78e7";
  module.def("fft_complex", &fft_complex, py::arg("array"), py::arg("axis"),
             py::arg("inverse") = false, py::arg("threads") = 1,
             "Unshifted FFT. Forward exponent is negative and unscaled; inverse is scaled by 1/N. "
             "Input must be C-contiguous complex64/complex128 and is never modified.");
}
