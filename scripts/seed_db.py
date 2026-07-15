import asyncio
import sys
sys.path.insert(0, '/app/backend')

from app.db.session import AsyncSessionLocal
from app.core.security import hash_password
from sqlalchemy import text


async def seed():
    async with AsyncSessionLocal() as db:
        # tenants table has no RLS — insert directly
        result = await db.execute(text("SELECT id FROM tenants WHERE slug='demo'"))
        tenant = result.fetchone()
        if tenant:
            print('Tenant already exists:', tenant[0])
            tenant_id = str(tenant[0])
        else:
            await db.execute(text(
                "INSERT INTO tenants (name, slug, is_active) VALUES ('Seventh AI Demo', 'demo', true)"
            ))
            result = await db.execute(text("SELECT id FROM tenants WHERE slug='demo'"))
            tenant_id = str(result.fetchone()[0])
            print('Created tenant:', tenant_id)

        # Set RLS tenant context before writing to any tenant-scoped table
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {'tid': tenant_id})

        result = await db.execute(text("SELECT id FROM users WHERE email='admin@seventh.ai'"))
        if result.fetchone():
            print('Admin user already exists')
        else:
            pwd = hash_password('Admin@1234')
            await db.execute(text(
                "INSERT INTO users (tenant_id, role_id, email, hashed_password, full_name, is_active) "
                "VALUES (:tid, 1, 'admin@seventh.ai', :pwd, 'Super Admin', true)"
            ), {'tid': tenant_id, 'pwd': pwd})
            print('Created admin: admin@seventh.ai / Admin@1234')

        await db.commit()
        print('Seed complete.')


asyncio.run(seed())
