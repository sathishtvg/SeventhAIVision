from enum import Enum


class AiModuleType(str, Enum):
    LPR = "lpr"
    FACE = "face"
    INTRUSION = "intrusion"


class WatchlistMatch(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


class AlertSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class IncidentStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class StreamStatus(str, Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class CameraHealthEventType(str, Enum):
    STREAM_DISCONNECTED = "stream_disconnected"
    STREAM_RECONNECTED = "stream_reconnected"
    STREAM_DEGRADED = "stream_degraded"


class RoleCode(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    OPERATOR = "operator"
    SECURITY_GUARD = "security_guard"
    VIEWER = "viewer"
