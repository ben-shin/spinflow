"""Stable errors shared by command-line, API and adapters."""


class NMRNextError(ValueError):
    def __init__(self, code, message, *, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def to_dict(self):
        return {"code": self.code, "message": str(self), "details": self.details}
