"""Ponto de entrada do TrampoTime.exe (PyInstaller precisa de um script fora do pacote)."""
import sys

from trampotime.__main__ import main

sys.exit(main(sys.argv[1:]))
