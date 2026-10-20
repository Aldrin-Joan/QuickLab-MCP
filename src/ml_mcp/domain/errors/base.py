"""Domain exceptions with sanitized public messages and structured error codes."""

from typing import Any

from ml_mcp.domain.errors.codes import ErrorCode


class DomainError(Exception):
    """Base domain exception that never leaks internal sensitive details to external clients."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        """Return safe, machine-readable dictionary representation for API / MCP responses."""
        payload: dict[str, Any] = {
            "error_code": self.code.value,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class AuthenticationRequiredError(DomainError):
    def __init__(
        self,
        message: str = "Authentication required to perform this action",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(ErrorCode.AUTHENTICATION_REQUIRED, message, details, status_code=401)


class AuthorizationDeniedError(DomainError):
    def __init__(
        self,
        message: str = "Access denied for the requested resource",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(ErrorCode.AUTHORIZATION_DENIED, message, details, status_code=403)


class ResourceNotFoundError(DomainError):
    def __init__(self, resource_type: str, resource_id: str) -> None:
        super().__init__(
            ErrorCode.RESOURCE_NOT_FOUND,
            f"{resource_type} '{resource_id}' was not found",
            details={"resource_type": resource_type, "resource_id": resource_id},
            status_code=404,
        )


class ResourceVersionRevokedError(DomainError):
    def __init__(self, resource_type: str, version: str) -> None:
        super().__init__(
            ErrorCode.RESOURCE_VERSION_REVOKED,
            f"{resource_type} version '{version}' has been revoked",
            details={"resource_type": resource_type, "version": version},
            status_code=410,
        )


class InvalidExperimentError(DomainError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.INVALID_EXPERIMENT, message, details, status_code=422)


class DatasetValidationError(DomainError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCode.DATASET_VALIDATION_FAILED, message, details, status_code=422)


class ModelNotApprovedError(DomainError):
    def __init__(self, model_id: str, version: str) -> None:
        super().__init__(
            ErrorCode.MODEL_NOT_APPROVED,
            f"Model '{model_id}' version '{version}' is not approved for execution",
            details={"model_id": model_id, "version": version},
            status_code=403,
        )


class ExperimentAlreadyExistsError(DomainError):
    def __init__(self, experiment_id: str) -> None:
        super().__init__(
            ErrorCode.EXPERIMENT_ALREADY_EXISTS,
            f"Experiment '{experiment_id}' already exists",
            details={"experiment_id": experiment_id},
            status_code=409,
        )


class ExperimentNotCancellableError(DomainError):
    def __init__(self, experiment_id: str, current_status: str) -> None:
        super().__init__(
            ErrorCode.EXPERIMENT_NOT_CANCELLABLE,
            f"Experiment '{experiment_id}' in status '{current_status}' cannot be cancelled",
            details={"experiment_id": experiment_id, "current_status": current_status},
            status_code=409,
        )


class TaskUnavailableError(DomainError):
    def __init__(self, message: str = "Execution task or worker is currently unavailable") -> None:
        super().__init__(ErrorCode.TASK_UNAVAILABLE, message, status_code=503)


class ArtifactAccessDeniedError(DomainError):
    def __init__(self, artifact_id: str) -> None:
        super().__init__(
            ErrorCode.ARTIFACT_ACCESS_DENIED,
            f"Access to artifact '{artifact_id}' is denied",
            details={"artifact_id": artifact_id},
            status_code=403,
        )


class ResourceLimitExceededError(DomainError):
    def __init__(self, resource: str, limit: Any, current: Any) -> None:
        super().__init__(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"Resource limit exceeded for '{resource}': limit is {limit}, requested/current is {current}",
            details={"resource": resource, "limit": limit, "current": current},
            status_code=429,
        )


class DependencyUnavailableError(DomainError):
    def __init__(self, dependency: str) -> None:
        super().__init__(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            f"Dependent service '{dependency}' is currently unavailable",
            details={"dependency": dependency},
            status_code=503,
        )


class InvalidInputError(DomainError):
    def __init__(
        self, message: str = "Invalid input provided", details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            ErrorCode.INVALID_INPUT,
            message,
            details=details,
            status_code=400,
        )
