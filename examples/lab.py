"""Basic integration example. Run from the repository after editable installation."""

import argparse

from tark_nrc.config import create_chiller


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args()
    config = None if args.simulate else args.config
    with create_chiller(config, simulate=args.simulate) as chiller:
        print(chiller.identity)
        print(chiller.read_sample())


if __name__ == "__main__":
    main()
