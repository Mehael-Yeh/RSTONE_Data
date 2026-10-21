import argparse
import json

from rstone_data.mirrors import deploy_mirror
from rstone_data.protocol import DataError, decode_json


def main():
    parser = argparse.ArgumentParser(description="Copy canonical resources to a mounted mirror, latest last")
    parser.add_argument("--source-store", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--keys", required=True)
    parser.add_argument("--allow-local-http", action="store_true")
    args = parser.parse_args()
    try:
        from pathlib import Path
        keys = decode_json(Path(args.keys).read_bytes())["keys"]
        print(json.dumps(deploy_mirror(args.source_store, args.target, args.base_url, keys, args.allow_local_http)))
        return 0
    except (DataError, OSError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
