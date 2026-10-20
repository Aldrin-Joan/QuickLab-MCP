"""Project management application service."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ml_mcp.domain.errors import InvalidInputError, ResourceNotFoundError
from ml_mcp.infrastructure.postgres.base import generate_uuid7
from ml_mcp.infrastructure.postgres.models import ProjectOrm
from ml_mcp.infrastructure.postgres.repositories.projects import ProjectRepository


class ProjectService:
    """Service coordinating project creation, retrieval, and defaults."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ProjectRepository(session)

    async def create_project(
        self,
        tenant_id: str,
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a new project under the specified tenant."""
        clean_name = name.strip()
        if not clean_name:
            raise InvalidInputError("Project name cannot be empty.")

        existing = await self.repo.get_project_by_name(tenant_id, clean_name)
        if existing:
            raise InvalidInputError(f"Project with name '{clean_name}' already exists.")

        project = ProjectOrm(
            id=generate_uuid7(),
            tenant_id=tenant_id,
            name=clean_name,
            description=description.strip(),
        )
        saved = await self.repo.create_project(project)
        return {
            "project_id": saved.id,
            "name": saved.name,
            "description": saved.description,
            "tenant_id": saved.tenant_id,
            "created_at": saved.created_at.isoformat(),
        }

    async def get_project(self, tenant_id: str, project_id: str) -> dict[str, Any]:
        """Retrieve project by ID or raise ResourceNotFoundError."""
        project = await self.repo.get_project(tenant_id, project_id)
        if not project:
            raise ResourceNotFoundError("Project", project_id)
        return {
            "project_id": project.id,
            "name": project.name,
            "description": project.description,
            "tenant_id": project.tenant_id,
            "created_at": project.created_at.isoformat(),
        }

    async def list_projects(
        self,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List all projects belonging to the tenant."""
        projects = await self.repo.list_projects(tenant_id, limit=limit, offset=offset)
        return [
            {
                "project_id": p.id,
                "name": p.name,
                "description": p.description,
                "tenant_id": p.tenant_id,
                "created_at": p.created_at.isoformat(),
            }
            for p in projects
        ]

    async def ensure_default_project(self, tenant_id: str = "default-tenant") -> dict[str, Any]:
        """Ensure the tenant and default-project exist, returning its details."""
        default_proj = await self.repo.get_or_create_default_project(tenant_id)
        return {
            "project_id": default_proj.id,
            "name": default_proj.name,
            "description": default_proj.description,
            "tenant_id": default_proj.tenant_id,
            "created_at": default_proj.created_at.isoformat(),
        }
