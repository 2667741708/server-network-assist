from server_network_assist.client_native import main
from server_network_assist.client_install import installed_data
import sys

if __name__ == '__main__':
    if '--data' not in sys.argv:
        data = installed_data(sys.executable)
        if data is not None:
            sys.argv.extend(['--data', str(data)])
    main()
