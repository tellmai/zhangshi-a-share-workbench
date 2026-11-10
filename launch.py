"""Default launcher opens the native desktop program."""
import subprocess
import sys
from pathlib import Path


def main():
    root=Path(__file__).resolve().parent
    executable=root/'dist/Zhangshi/Zhangshi.exe'
    if executable.exists():subprocess.Popen([str(executable)],cwd=root);return
    runtime=root/'.desktop-env/Scripts/pythonw.exe'
    if runtime.exists():subprocess.Popen([str(runtime),str(root/'desktop.py')],cwd=root);return
    from desktop import main as native_main
    native_main()


if __name__ == "__main__":
    main()
