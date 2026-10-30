cmake_minimum_required(VERSION 3.20)
project(spinflow_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
find_package(pybind11 CONFIG REQUIRED)
find_package(Threads REQUIRED)

pybind11_add_module(_native cpp/native.cpp)
target_include_directories(_native PRIVATE cpp)
target_link_libraries(_native PRIVATE Threads::Threads)
target_compile_definitions(_native PRIVATE VERSION_INFO="0.3.0b1")
if(MSVC)
  target_compile_options(_native PRIVATE /W4 /O2)
else()
  target_compile_options(_native PRIVATE -Wall -Wextra -Wpedantic -O3)
endif()

# scikit-build-core stages CMake installs at the wheel's platlib root.
# Include Python sources but never stale host-specific extension binaries.
install(DIRECTORY src/nmrnext/ DESTINATION nmrnext FILES_MATCHING PATTERN "*.py")
install(FILES compatibility/support.json DESTINATION nmrnext)
install(TARGETS _native LIBRARY DESTINATION nmrnext RUNTIME DESTINATION nmrnext)
