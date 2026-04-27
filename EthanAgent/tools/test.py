import os
from pathlib import Path
cwd = os.getcwd()
print(cwd)
requested = Path(cwd).expanduser().resolve()
print(requested.parent)
import sys 
print(sys.platform)