from pydantic import BaseModel


class IpState(BaseModel):
    public_ip: str = ""
    previous_ip: str = ""
    source: str = ""
    checked_at: str = ""
    changed_at: str = ""
    consecutive_failures: int = 0
    hostname: str = ""
    monitor_enabled: bool = False
    interval_minutes: int = 0
