"""Offline URL-to-code conversion. Never enroll or change network settings."""
import argparse
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from server_network_assist.client_subscription_code import encode_subscription_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.input.stat().st_size > 32768:
        parser.error('Input file is too large')
    code = encode_subscription_code(args.input.read_text(encoding='utf-8-sig'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent,
                                         prefix='.subscription-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(code + '\n')
        os.replace(temporary, args.output)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    print('Subscription code saved. No enrollment or network operations performed.')


if __name__ == '__main__':
    main()
