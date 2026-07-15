import type { ReactNode } from 'react'
import { usePermission } from '@/hooks/usePermission'

interface Props {
  permission: string
  children: ReactNode
  fallback?: ReactNode
}

export function PermissionGuard({ permission, children, fallback = null }: Props) {
  const allowed = usePermission(permission)
  return allowed ? <>{children}</> : <>{fallback}</>
}
