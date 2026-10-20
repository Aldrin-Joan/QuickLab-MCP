"""Repository for project control plane persistence."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ml_mcp.infrastructure.postgres.models import ProjectOrm, TenantOrm


class ProjectRepository:
    """Tenant-scoped data access for projects."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_project(self, project: ProjectOrm) -> ProjectOrm:
        self.session.add(project)
        await self.session.flush()
        return project

    async def get_project(self, tenant_id: str, project_id: str) -> ProjectOrm | None:
        stmt = select(ProjectOrm).where(
            ProjectOrm.tenant_id == tenant_id,
            ProjectOrm.id == project_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_project_by_name(self, tenant_id: str, name: str) -> ProjectOrm | None:
        stmt = select(ProjectOrm).where(
            ProjectOrm.tenant_id == tenant_id,
            ProjectOrm.name == name,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_projects(
        self, tenant_id: str, limit: int = 50, offset: int = 0
    ) -> list[ProjectOrm]:
        stmt = (
            select(ProjectOrm)
            .where(ProjectOrm.tenant_id == tenant_id)
            .order_by(ProjectOrm.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_or_create_default_project(self, tenant_id: str = "default-tenant") -> ProjectOrm:
        """Ensure tenant and default project exist, returning the project."""
        # 1. Ensure tenant exists
        tenant_stmt = select(TenantOrm).where(TenantOrm.id == tenant_id)
        tenant_res = await self.session.execute(tenant_stmt)
        tenant = tenant_res.scalar_one_or_none()
        if not tenant:
            tenant = TenantOrm(
                id=tenant_id,
                name="Default Tenant" if tenant_id == "default-tenant" else tenant_id,
            )
            self.session.add(tenant)
            await self.session.flush()

        # 2. Ensure default-project exists
        proj_id = (
            "default-project" if tenant_id == "default-tenant" else f"{tenant_id}-default-project"
        )
        proj_stmt = select(ProjectOrm).where(
            ProjectOrm.tenant_id == tenant_id,
            (ProjectOrm.id == proj_id) | (ProjectOrm.name == "Default Project"),
        )
        proj_res = await self.session.execute(proj_stmt)
        project = proj_res.scalar_one_or_none()
        if not project:
            project = ProjectOrm(
                id=proj_id,
                tenant_id=tenant_id,
                name="Default Project",
                description="Default ModelLab experimentation project",
            )
            self.session.add(project)
            await self.session.flush()

        return project
