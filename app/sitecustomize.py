"""Do not print unhandled exception text, which may contain request data."""
import sys


def safe_exception(*args):
    sys.stderr.write('{"event":"unhandled_exception"}\n')


sys.excepthook = safe_exception
sys.unraisablehook = safe_exception
