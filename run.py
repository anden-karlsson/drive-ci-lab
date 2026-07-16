"""Dummy pipeline for the Drive-event-driven CI lab. Grows a flag per phase."""
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--hello", action="store_true", help="phase 1: prove CI runs this file")
args = parser.parse_args()

if args.hello:
    print("hello from run.py - executed by CI")
