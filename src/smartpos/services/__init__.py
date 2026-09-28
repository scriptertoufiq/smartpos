"""Business rules.

The UI calls services; services call repositories. No SQL in the UI, no Qt in
the services -- that keeps the rules testable without a display.
"""
