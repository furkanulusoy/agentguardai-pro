from infrastructure.database.models import SecurityEvent


def record_security_event(session, user, action, target):
    """Participates in the caller transaction; callers pass only constant codes and IDs."""
    session.add(
        SecurityEvent(tenant_id=user.tenant_id, actor_id=user.id, action=action, target=str(target))
    )
