"""A model proposes, code disposes.

A model session produces an artifact; code checks it. A failure gets one repair by a fresh session.
A pass gets a second look by a different session, and its comments one revision round. Nothing is
delivered without a passing check on the bytes delivered, and "not delivered" is a valid outcome.
"""
from .commands import CommandCheck, CommandSession, SessionRequest, parse_check_output
from .faults import Fault, run_faults
from .gate import (DELIVER, NOT_DELIVERED, PRODUCE, REPAIR, REVISE, SECOND_LOOK, Finding, Gate, GateError,
                   Verdict)
from .runner import Runner

__version__ = '0.1.0'

__all__ = ['Gate', 'GateError', 'Verdict', 'Finding', 'CommandCheck', 'CommandSession', 'SessionRequest', 'Runner',
           'Fault', 'run_faults', 'parse_check_output', 'PRODUCE', 'REPAIR', 'SECOND_LOOK', 'REVISE', 'DELIVER',
           'NOT_DELIVERED', '__version__']
