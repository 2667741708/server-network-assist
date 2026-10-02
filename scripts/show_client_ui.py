"""Open only an authenticated existing customer panel; never start a backend."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from server_network_assist.client_native import main

if __name__ == '__main__':
    sys.argv = [sys.argv[0], '--show']
    main()
