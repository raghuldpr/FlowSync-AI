"""Compile and run the car host test with the system C++ compiler (g++, clang++ or c++); warnings are errors."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib" / "car_logic"


def main() -> int:
    cxx = next((c for c in ("g++", "clang++", "c++") if shutil.which(c)), None)
    if not cxx:
        print("no C++ compiler found (install g++ or clang++)")
        return 2
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "host_tests.exe"
        cmd = [cxx, "-std=c++11", "-O1", "-Wall", "-Wextra", "-Werror", f"-I{LIB}",
               str(HERE / "host_tests.cpp"), str(LIB / "car_logic.cpp"), "-o", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            print(r.stderr)
            return 1
        r = subprocess.run([str(out)], capture_output=True, text=True)
        print(r.stdout + r.stderr)
        return r.returncode


if __name__ == "__main__":
    sys.exit(main())
