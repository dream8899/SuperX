class VideoRemixError(Exception):
    """Base exception with a stable machine code and process exit code."""

    def __init__(self, message: str, *, code: str, exit_code: int):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


class InputError(VideoRemixError):
    def __init__(self, message: str, *, code: str = "INVALID_INPUT"):
        from .constants import EXIT_INPUT

        super().__init__(message, code=code, exit_code=EXIT_INPUT)


class ToolError(VideoRemixError):
    def __init__(self, message: str, *, code: str = "TOOL_FAILED"):
        from .constants import EXIT_TOOL

        super().__init__(message, code=code, exit_code=EXIT_TOOL)


class PlanError(VideoRemixError):
    def __init__(self, message: str, *, code: str = "INVALID_PLAN"):
        from .constants import EXIT_PLAN

        super().__init__(message, code=code, exit_code=EXIT_PLAN)


class NotImplementedPhaseError(VideoRemixError):
    def __init__(self, message: str):
        from .constants import EXIT_NOT_IMPLEMENTED

        super().__init__(message, code="PHASE_NOT_IMPLEMENTED", exit_code=EXIT_NOT_IMPLEMENTED)
