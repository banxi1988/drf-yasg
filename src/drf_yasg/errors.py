from typing import Optional, List


class SwaggerError(Exception):
    pass


class SwaggerValidationError(SwaggerError):
    def __init__(
        self,
        msg: str,
        errors: Optional[List[str]] = None,
        spec: str = None,
        source_codec: str = None,
        *args
    ):
        super(SwaggerValidationError, self).__init__(msg, *args)
        self.errors = errors
        self.spec = spec
        self.source_codec = source_codec


class SwaggerGenerationError(SwaggerError):
    pass
